from __future__ import annotations

import os
import logging
import datetime
import tempfile
import time
import importlib
import importlib.util
import pkgutil
import sys
import types
from pathlib import Path
from urllib.parse import urlparse
from typing import Generator
from typing import Optional
from typing import Tuple
from typing import Dict
from typing import List
from typing import Type


import coloredlogs

# TODO Type ignore here because there's a bug with requests (https://github.com/python/mypy/issues/16400)
import requests  # type: ignore[import-untyped]
import bs4
import lxml
import tqdm

from .firmware import Firmware, FailedDownload
from .dbmanager.database_manager import DatabaseManager
from .filestore import FileStore

coloredlogs.install(level="INFO")

DEFAULT_USERAGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"


class UnhealthyScraper(Exception):
    """
    Raise when the we reach a hard error in the scraper logic because of the assuptions made of the target
    we are scraping. Usually due to the underlying resource (e.g. website) changing and assuptions the
    scraper has no longer hold. We use the this exception to denote that a scraper needs updates/fixing
    in order to continue functioning.
    """

    pass


class Scraper:
    """Base Scraper Class for a particular website/server/endpoint"""

    _REGISTRY: List[Type[Scraper]] = list()

    @staticmethod
    def http_download(
        url: str,
        directory: Optional[str] = None,
        filename: Optional[str] = None,
        dry_run: bool = False,
        ok_400: bool = False,
        ok_500: bool = False,
        logger: Optional[logging.Logger] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
    ) -> Optional[str]:
        """Download a file via http and returns the filepath it was downloaded to"""
        if filename is None:
            parsed_url = urlparse(url)
            path = parsed_url.path.rstrip("/")
            if path:
                filename = path.split("/")[-1]
            else:
                if logger:
                    logger.warning(f"Unable to extract file name from: {url}")
                return None

        filepath = os.path.join(directory or "./", filename)
        if dry_run:
            return filepath

        if headers is None:
            headers = dict()
        # By Default, set User Agent to something thats not pythons' request (since that is frequently checked)
        if "User-Agent" not in headers:
            headers["User-Agent"] = DEFAULT_USERAGENT

        try:
            res = requests.get(url, stream=True, headers=headers, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if logger:
                logger.warning(f"Failed to connect to {url}")
            return None

        if (not ok_400 and res.status_code // 100 == 4) or (
            not ok_500 and res.status_code // 100 == 5
        ):
            res.raise_for_status()

        if logger:
            logger.info(f"Downloading: {url}")

        content_size = int(res.headers.get("content-length", 0))
        try:
            with (
                open(filepath, "wb") as f,
                tqdm.tqdm(
                    total=content_size, unit="B", unit_scale=True, unit_divisor=1024
                ) as bar,
            ):
                for chunk in res.iter_content(chunk_size=4096):
                    if chunk:
                        f.write(chunk)
                        bar.update(len(chunk))
        except (
            OSError,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
        ) as e:
            if logger:
                logger.warning(
                    f"Download interrupted ({type(e).__name__}): {url} — {e}"
                )
            # Remove the partial file so a retry starts fresh
            try:
                os.remove(filepath)
            except OSError:
                pass
            return None

        return filepath

    @staticmethod
    def get_html(
        url: str,
        ok_400: bool = False,
        ok_500: bool = False,
        logger: Optional[logging.Logger] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        if headers is None:
            headers = dict()
        # By Default, set User Agent to something thats not pythons' request (since that is frequently checked)
        if "User-Agent" not in headers:
            headers["User-Agent"] = DEFAULT_USERAGENT

        try:
            res = requests.get(url, headers=headers)
        except requests.exceptions.ConnectionError:
            if logger:
                logger.warning(f"Failed to connect to {url}")
            return None
        if logger:
            logger.info(f"[GET] {url} => {res.status_code}")
        if (not ok_400 and res.status_code // 100 == 4) or (
            not ok_500 and res.status_code // 100 == 5
        ):
            try:
                res.raise_for_status()
            except:
                if logger:
                    logger.warning(f"Request Failed - Status Code: {res.status_code}")
                return None
        return res.text

    @staticmethod
    def soup(
        url: str,
        ok_400: bool = False,
        ok_500: bool = False,
        logger: Optional[logging.Logger] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[bs4.BeautifulSoup]:
        """Performs a GET reqest against `url` and returns a BeautifulSoup object wrapping it"""
        html = Scraper.get_html(
            url, ok_400=ok_400, ok_500=ok_500, logger=logger, headers=headers
        )
        if html is None:
            return None

        return bs4.BeautifulSoup(html, "html.parser")

    @staticmethod
    def tree(
        url: str,
        ok_400: bool = False,
        ok_500: bool = False,
        logger: Optional[logging.Logger] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[lxml.etree._Element]:
        html = Scraper.get_html(
            url, ok_400=ok_400, ok_500=ok_500, logger=logger, headers=headers
        )
        if html is None:
            return None

        return lxml.etree.HTML(html)

    @classmethod
    def _load_module_from_path(cls, path: Path) -> Optional[types.ModuleType]:
        """Load a Python file or package from a filesystem path."""
        path = Path(path)
        if not path.exists():
            print(f"Failed to import: {path} does not exist")
            return None

        module_name = path.stem
        spec = importlib.util.spec_from_file_location(
            module_name, path / "__init__.py" if path.is_dir() else path
        )
        if spec is None or spec.loader is None:
            print(f"Failed to import: could not load spec for {path}")
            return None

        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"Failed to import {path}: {e}")
            return None

        return mod

    @classmethod
    def registry(
        cls, additional_modules: List[Path] | None = None
    ) -> Optional[List[Type[Scraper]]]:
        # The registry is populated on each subclassing of Scraper via __init_subclass__().
        # This only occurs if any of the given subclasses are imported. So here, we
        # explicitly import all Scrapers so the registry is populated correctly
        if cls is not Scraper:
            print(f"registry() is only invokable from the Scraper class")
            return None

        modules_to_load = ["neodymium.scrapers"] + (additional_modules or [])
        for entry in modules_to_load:
            if isinstance(entry, Path):
                scrapers_module = cls._load_module_from_path(entry)
                if scrapers_module is None:
                    continue
                sys.modules[scrapers_module.__name__] = scrapers_module
            else:
                try:
                    scrapers_module = importlib.import_module(entry)
                except ModuleNotFoundError as e:
                    print(f"{e}: Failed to import {entry}")
                    continue

            if not hasattr(scrapers_module, "__path__"):
                continue

            for mod_info in pkgutil.iter_modules(scrapers_module.__path__):
                submodule_name = f"{scrapers_module.__package__}.{mod_info.name}"
                try:
                    importlib.import_module(submodule_name)
                except ModuleNotFoundError as e:
                    print(f"{e}: Failed to import: {submodule_name}")

        return cls._REGISTRY

    def __init_subclass__(cls, **kwargs):
        # This function is called each time cls is subclassed
        super().__init_subclass__(**kwargs)
        Scraper._REGISTRY.append(cls)

    def __init__(
        self,
        base_url: str,
        dm: DatabaseManager,
        fs: Optional[FileStore] = None,
        timeout: int = 0,
        log_level: int = logging.INFO,
    ):
        self.base_url: str = base_url
        self.timeout: int = timeout
        self.running: bool = False
        self.min_wait_s: float = 3  # Minimum wait time between downloads (seconds)
        self.dry_run: bool = False
        self.logger = logging.getLogger(self.__class__.__name__)
        self.temp_dir: Optional[str] = None
        self.dm = dm
        self.fs = fs

    def push_failed_download(self, fw: Firmware, url: str) -> None:
        """Record a failed download in the database for later retry."""
        self.dm.push_failed_download(
            FailedDownload(scraper=self.__class__.__name__, url=url, firmware=fw)
        )

    def scrape(self) -> Generator[Tuple[Firmware, str], None, None]:
        """Yields a Tuple of a Firmware Object and a filepath to the downloaded firmware"""
        raise NotImplementedError

    def run(self, dry_run: bool = False) -> Generator[Tuple[Firmware, str], None, None]:
        self.logger.info(f"Starting {self.__class__.__name__} Scraper")
        self.dry_run = dry_run
        if self.dry_run:
            self.logger.info("DRY RUN")

        self.running = True
        count = 0
        start_time = datetime.datetime.now()
        last_dl = start_time
        with tempfile.TemporaryDirectory() as temp_dir:
            self.temp_dir = temp_dir
            for fw, fw_path in self.scrape():
                if self.dm.find_duplicate(fw):
                    continue

                if not fw.calc_file_metadata(fw_path):
                    self.logger.error(f"Failed to read file: {fw_path}")
                    continue

                # Ensure this loop iterates at a minimum of self.min_wait_s seconds
                delta_t = (datetime.datetime.now() - last_dl).total_seconds()
                if delta_t < self.min_wait_s:
                    time.sleep(self.min_wait_s - delta_t)
                last_dl = datetime.datetime.now()

                if not self.dry_run:
                    _fs_wrote = False
                    if self.fs is not None:
                        self.fs.add(fw, fw_path)
                        _fs_wrote = True
                    # Guard against Ctrl+C between fs.add() and dm.add_firmware():
                    # if interrupted after the file is stored, retry the DB write
                    # so the stored file is tracked and won't be re-downloaded.
                    _ki = False
                    try:
                        self.dm.add_firmware(fw)
                    except KeyboardInterrupt:
                        if _fs_wrote:
                            try:
                                self.dm.add_firmware(fw)
                            except Exception as e:
                                self.logger.error(
                                    f"{e}: Failed to add firmware to database. May be out of sync from filestore "
                                )
                        _ki = True
                    if _ki:
                        raise KeyboardInterrupt

                self.logger.info(f"Downloaded: {str(fw)}")
                yield fw, fw_path
                count += 1

        end_time = datetime.datetime.now() - start_time
        self.running = False
        self.logger.info(f"Downloaded {count} files in {end_time.total_seconds()}s")
