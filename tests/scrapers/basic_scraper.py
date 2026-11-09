"""A concrete scraper for the fake-vendor ``/basic/`` static site.

This exists to validate the test framework end-to-end: point it at the
fake-vendor server, run it, and confirm firmware metadata lands in MongoDB with
correct checksums. It also exercises Basic Auth scraping, since some fixture
devices gate their page and/or download.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Generator
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from neodymium.firmware import Firmware, Tags
from neodymium.scraper import Scraper

if TYPE_CHECKING:
    from neodymium.dbmanager.database_manager import DatabaseManager
    from neodymium.filestore import FileStore


class BasicScraper(Scraper):
    """Walks ``/basic/``: index -> per-device page -> firmware download."""

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
        base_url = base_url or os.environ.get("BASIC_SCRAPER_URL")
        if not base_url:
            raise ValueError(
                "BasicScraper needs a base_url (arg) or BASIC_SCRAPER_URL (env)"
            )
        if credentials is None:
            user = os.environ.get("BASIC_SCRAPER_USER")
            password = os.environ.get("BASIC_SCRAPER_PASS")
            if user and password:
                credentials = (user, password)

        super().__init__(base_url, dm, fs)
        # Fixture server is local; no polite delay needed between downloads.
        self.min_wait_s = 0
        self._credentials = credentials

    def _auth_headers(self) -> dict[str, str]:
        if self._credentials is None:
            return {}
        user, password = self._credentials
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _get_download_headers(self) -> dict[str, str]:
        return self._auth_headers()

    @staticmethod
    def _parse_tags(raw: str) -> set[Tags]:
        return {Tags(t) for t in raw.split(",") if t}

    @staticmethod
    def _parse_date(raw: str) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None

    def scrape(self) -> Generator[tuple[Firmware, str], None, None]:
        index_url = urljoin(self.base_url + "/", "basic/")
        index = self.soup(index_url, headers=self._auth_headers(), logger=self.logger)
        if index is None:
            self.logger.error(f"Failed to load index: {index_url}")
            return

        page_urls = [
            urljoin(index_url, a["href"])
            for a in index.select("a.device-link")
            if a.get("href")
        ]

        for page_url in page_urls:
            page = self.soup(page_url, headers=self._auth_headers(), logger=self.logger)
            if page is None:
                self.logger.warning(f"Skipping gated/unavailable page: {page_url}")
                continue

            article = page.select_one("article.firmware")
            link = page.select_one("a.download")
            if article is None or link is None or not link.get("href"):
                self.logger.warning(f"No firmware found on page: {page_url}")
                continue

            desc_el = article.select_one("p.description")
            download_url = urljoin(page_url, link["href"])
            fw = Firmware(
                vendor=article["data-vendor"],
                product=article["data-product"],
                version=article["data-version"],
                hw_rev=article.get("data-hw-rev") or None,
                region=article.get("data-region") or None,
                upload_date=self._parse_date(article.get("data-upload-date", "")),
                description=desc_el.get_text(strip=True) if desc_el else None,
                filename=article["data-filename"],
                tags=self._parse_tags(article.get("data-tags", "")),
                url=download_url,
            )

            fw_path = self.http_download(
                download_url,
                directory=self.temp_dir,
                filename=fw.filename,
                headers=self._get_download_headers(),
                logger=self.logger,
            )
            if fw_path is None:
                self.logger.error(f"Download failed: {download_url}")
                self.push_failed_download(fw, download_url)
                continue

            yield fw, fw_path
