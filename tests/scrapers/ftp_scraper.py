"""A concrete scraper for an (S)FTP firmware server, built on ``neodymium.ftp``.

It exists to exercise ``neodymium.ftp.FTPClient`` end-to-end: point it at the
root of a firmware tree, walk it, and for every directory holding a
``metadata.json`` sidecar, parse the metadata and download the firmware file it
names. The same scraper drives both the dockerised FTP server and the
in-process SFTP server (the scheme comes from ``base_url``), so it validates
both backends.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from neodymium.firmware import Firmware, Tags
from neodymium.ftp import FTPClient, FTPError
from neodymium.scraper import Scraper, ScrapeYield

if TYPE_CHECKING:
    from neodymium.dbmanager.database_manager import DatabaseManager
    from neodymium.filestore import FileStore

METADATA_NAME = "metadata.json"


class FTPScraper(Scraper):
    """Walks an (S)FTP firmware tree: metadata.json sidecar -> firmware file."""

    def __init__(
        self,
        dm: DatabaseManager,
        fs: FileStore | None = None,
        *,
        base_url: str | None = None,
        credentials: tuple[str, str] | None = None,
    ) -> None:
        # The CLI constructs scrapers as ``Scraper(dm, fs)``, so configuration
        # comes from the environment when not passed explicitly.
        base_url = base_url or os.environ.get("FTP_SCRAPER_URL")
        if not base_url:
            raise ValueError(
                "FTPScraper needs a base_url (arg) or FTP_SCRAPER_URL (env)"
            )
        if credentials is None:
            user = os.environ.get("FTP_SCRAPER_USER")
            password = os.environ.get("FTP_SCRAPER_PASS")
            if user and password:
                credentials = (user, password)

        super().__init__(base_url, dm, fs)
        # Fixture server is local; no polite delay needed between downloads.
        self.min_wait_s = 0
        self._credentials = credentials

        parsed = urlparse(base_url)
        self._url_prefix = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    def _file_url(self, remote_path: str) -> str:
        return f"{self._url_prefix}{remote_path}"

    def _client(self) -> FTPClient:
        if self._credentials is not None:
            user, password = self._credentials
            return FTPClient.from_url(
                self.base_url,
                username=user,
                password=password,
                logger=self.logger,
            )
        return FTPClient.from_url(self.base_url, logger=self.logger)

    @staticmethod
    def _parse_tags(raw: list[str]) -> set[Tags]:
        return {Tags(t) for t in raw}

    @staticmethod
    def _parse_date(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None

    def scrape(self) -> Generator[ScrapeYield, None, None]:
        client = self._client()
        try:
            client.connect()
        except FTPError as e:
            self.logger.error(f"Failed to connect: {e}")
            return

        try:
            for _dirpath, _dirs, files in client.walk(client.base_path):
                sidecar = next((f for f in files if f.name == METADATA_NAME), None)
                if sidecar is None:
                    continue

                try:
                    meta = json.loads(client.read_text(sidecar.path))
                except (FTPError, json.JSONDecodeError) as e:
                    self.logger.warning(f"Bad metadata at {sidecar.path}: {e}")
                    continue

                fw_entry = next(
                    (f for f in files if f.name == meta.get("filename")), None
                )
                if fw_entry is None:
                    self.logger.warning(
                        f"metadata.json references a missing file in {_dirpath}"
                    )
                    continue

                download_url = self._file_url(fw_entry.path)
                fw = Firmware(
                    vendor=meta["vendor"],
                    product=meta["product"],
                    version=meta["version"],
                    hw_rev=meta.get("hw_rev"),
                    region=meta.get("region"),
                    upload_date=self._parse_date(meta.get("upload_date")),
                    description=meta.get("description"),
                    filename=meta["filename"],
                    tags=self._parse_tags(meta.get("tags", [])),
                    extra=meta.get("extra"),
                    url=download_url,
                )

                fw_path = client.download(
                    fw_entry.path,
                    directory=self.temp_dir,
                    filename=fw.filename,
                    logger=self.logger,
                )
                if fw_path is None:
                    self.logger.error(f"Download failed: {fw_entry.path}")
                    self.push_failed_download(fw, download_url)
                    yield self.SKIP
                    continue

                yield fw, fw_path
        finally:
            client.close()
