from __future__ import annotations

import contextlib
import datetime
import importlib
import importlib.util
import logging
import os
import pkgutil
import sys
import tempfile
import time
import types
from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlparse

import bs4
import coloredlogs
import lxml.etree
import requests
import tqdm

from .dbmanager.database_manager import DatabaseManager
from .filestore import FileStore
from .firmware import FailedDownload, Firmware

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


class _Skip:
    """Type of ``Scraper.SKIP``, the failed-download sentinel."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "Scraper.SKIP"


# A successful (Firmware, filepath), or SKIP for a failed/skipped download.
ScrapeYield = tuple[Firmware, str] | _Skip


class Scraper:
    """Base Scraper Class for a particular website/server/endpoint"""

    _REGISTRY: list[type[Scraper]] = []

    #: Yield this from ``scrape()`` on a failed/skipped download (see ``_Skip``).
    SKIP: _Skip = _Skip()

    @staticmethod
    def http_download(
        url: str,
        directory: str | None = None,
        filename: str | None = None,
        dry_run: bool = False,
        ok_400: bool = False,
        ok_500: bool = False,
        logger: logging.Logger | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 60,
        auth: tuple[str, str] | None = None,
    ) -> str | None:
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
            headers = {}
        # By Default, set User Agent to something thats not pythons' request (since that is frequently checked)
        if "User-Agent" not in headers:
            headers["User-Agent"] = DEFAULT_USERAGENT

        try:
            res = requests.get(
                url, stream=True, headers=headers, timeout=timeout, auth=auth
            )
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
            with contextlib.suppress(OSError):
                os.remove(filepath)
            return None

        return filepath

    @staticmethod
    def google_drive_quota_exceeded(filepath: str) -> bool | None:
        """Return True if a downloaded Google Drive file is actually the
        "Quota exceeded" HTML page, False if it looks like a real file, or
        None if the file could not be read."""
        try:
            with open(filepath, "rb") as f:
                # len("<!DOCTYPE html><html><head><title>Google Drive - Quota exceeded") => 63
                start = f.read(63)
                return b"Google Drive - Quota exceeded" in start
        except OSError:
            return None

    @staticmethod
    def get_html(
        url: str,
        ok_400: bool = False,
        ok_500: bool = False,
        logger: logging.Logger | None = None,
        headers: dict[str, str] | None = None,
    ) -> str | None:
        if headers is None:
            headers = {}
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
            except requests.exceptions.HTTPError:
                if logger:
                    logger.warning(f"Request Failed - Status Code: {res.status_code}")
                return None
        return res.text

    @staticmethod
    def soup(
        url: str,
        ok_400: bool = False,
        ok_500: bool = False,
        logger: logging.Logger | None = None,
        headers: dict[str, str] | None = None,
    ) -> bs4.BeautifulSoup | None:
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
        logger: logging.Logger | None = None,
        headers: dict[str, str] | None = None,
    ) -> lxml.etree._Element | None:
        html = Scraper.get_html(
            url, ok_400=ok_400, ok_500=ok_500, logger=logger, headers=headers
        )
        if html is None:
            return None

        return lxml.etree.HTML(html)

    @classmethod
    def _load_module_from_path(cls, path: Path) -> types.ModuleType | None:
        """Load a Python file or package from a filesystem path."""
        path = Path(path)
        if not path.exists():
            print(f"Failed to import: {path} does not exist")
            return None

        module_name = path.stem
        # Reuse an already-loaded module; re-executing it would re-register
        # its scrapers and break registry() idempotency.
        cached = sys.modules.get(module_name)
        if cached is not None:
            return cached

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
        cls, additional_modules: list[Path] | None = None
    ) -> list[type[Scraper]] | None:
        # The registry is populated on each subclassing of Scraper via __init_subclass__().
        # This only occurs if any of the given subclasses are imported. So here, we
        # explicitly import all Scrapers so the registry is populated correctly
        if cls is not Scraper:
            print("registry() is only invokable from the Scraper class")
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
        fs: FileStore | None = None,
        timeout: int = 0,
        log_level: int = logging.INFO,
    ):
        self.base_url: str = base_url
        self.timeout: int = timeout
        self.running: bool = False
        self.min_wait_s: float = 3  # Minimum wait time between downloads (seconds)
        self.dry_run: bool = False
        self.logger = logging.getLogger(self.__class__.__name__)
        self.temp_dir: str | None = None
        self.dm = dm
        self.fs = fs

    def push_failed_download(self, fw: Firmware, url: str) -> None:
        """Record a failed download in the database for later retry."""
        self.dm.push_failed_download(
            FailedDownload(scraper=self.__class__.__name__, url=url, firmware=fw)
        )

    def _get_download_headers(self) -> dict[str, str]:
        """Return extra HTTP headers used when downloading firmware files.
        Override in subclasses that require vendor-specific headers (e.g. Referer)."""
        return {}

    def retry_failed(self) -> None:
        """Re-attempt all failed downloads recorded for this scraper."""
        failed = self.dm.get_failed_downloads(scraper=self.__class__.__name__)
        if not failed:
            self.logger.info("No failed downloads to retry.")
            return

        self.logger.info(f"Retrying {len(failed)} failed download(s)...")
        headers = self._get_download_headers()

        with tempfile.TemporaryDirectory() as temp_dir:
            for record in failed:
                fw = record.firmware
                url = record.url

                fw_path: str | None = None
                for attempt in range(3):
                    fw_path = self.http_download(
                        url,
                        directory=temp_dir,
                        filename=fw.filename,
                        headers=headers,
                        logger=self.logger,
                    )
                    if fw_path:
                        break
                    if attempt < 2:
                        delay = 2 ** (attempt + 1)
                        self.logger.warning(
                            f"Retry attempt {attempt + 1}/3 failed, waiting {delay}s: {url}"
                        )
                        time.sleep(delay)

                if not fw_path:
                    self.logger.error(f"Still failing after 3 attempts: {url}")
                    self.push_failed_download(fw, url)  # increments attempts counter
                    continue

                if not fw.calc_file_metadata(fw_path):
                    self.logger.error(f"Failed to read retried file: {fw_path}")
                    continue

                fw.set_dl_now()

                if self.fs is not None:
                    self.fs.add(fw, fw_path)
                    self._discard_temp_file(fw_path)

                if not self.dm.find_duplicate(fw):
                    self.dm.add_firmware(fw)

                self.dm.clear_failed_download(url)
                self.logger.info(f"Retry succeeded: {fw.version}")

    def scrape(self) -> Generator[ScrapeYield, None, None]:
        """Yield ``(Firmware, filepath)`` per successful download, or ``Scraper.SKIP``
        on a failed/skipped one so throttling and interleaving still run. Never yield
        a tuple for a failure — ``run()`` would store it as real firmware."""
        raise NotImplementedError

    def _throttle(self, last_dl: datetime.datetime) -> datetime.datetime:
        """Block so attempts are >= ``min_wait_s`` apart; return the new reference time."""
        delta_t = (datetime.datetime.now() - last_dl).total_seconds()
        if delta_t < self.min_wait_s:
            time.sleep(self.min_wait_s - delta_t)
        return datetime.datetime.now()

    def _discard_temp_file(self, path: str) -> None:
        with contextlib.suppress(OSError):
            os.remove(path)

    def run(self, dry_run: bool = False) -> Generator[ScrapeYield, None, None]:
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
            for item in self.scrape():
                if isinstance(item, _Skip):
                    # Failed/skipped download: pace it and yield so other
                    # scrapers get a turn, but store/record nothing.
                    last_dl = self._throttle(last_dl)
                    yield item
                    continue

                fw, fw_path = item
                if self.dm.find_duplicate(fw):
                    if (
                        self.fs is not None
                        and not self.dry_run
                        and fw.calc_file_metadata(fw_path)
                    ):
                        self.fs.add(fw, fw_path)
                        self._discard_temp_file(fw_path)
                    continue

                if not fw.calc_file_metadata(fw_path):
                    self.logger.error(f"Failed to read file: {fw_path}")
                    continue

                fw.set_dl_now()

                last_dl = self._throttle(last_dl)

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
                if self.fs is not None and not self.dry_run:
                    self._discard_temp_file(fw_path)

        end_time = datetime.datetime.now() - start_time
        self.running = False
        self.logger.info(f"Downloaded {count} files in {end_time.total_seconds()}s")
