import coloredlogs, logging
from typing import List, Optional

from pymongo import MongoClient

from neodymium.firmware import Firmware, FailedDownload

logger = logging.getLogger(__file__)
coloredlogs.install(level="INFO")


class DatabaseManager:
    def __init__(
        self,
        username: str,
        password: str,
        host: str,
        port: int,
        log_level: int = logging.INFO,
    ):

        self.username = username
        self.password = password
        self.host = host
        self.port = port
        self.client = MongoClient(
            f"mongodb://{self.username}:{self.password}@{self.host}:{self.port}/"
        )
        self.db = self.client["firmware-database"]
        self.collection = self.db["firmware"]
        self.failed_downloads = self.db["failed-downloads"]
        self.logger = logging.getLogger(self.__class__.__name__)

    def add_firmware(self, firmware_metadata: Firmware):

        firmware = self.db.firmware
        firmware.insert_one(firmware_metadata.model_dump()).inserted_id

    def check_url(self, firmware_url: str):

        url_exists = self.collection.find_one({"url": firmware_url})
        if url_exists:
            return True

        return False

    def log_failed_download(self, failed: FailedDownload) -> None:
        """Upsert a failed download record, incrementing attempts on repeat failures."""
        self.failed_downloads.update_one(
            {"url": failed.url},
            {
                "$set": {
                    "scraper": failed.scraper,
                    "firmware": failed.firmware.model_dump(mode="json"),
                    "failed_at": failed.failed_at,
                },
                "$inc": {"attempts": 1},
            },
            upsert=True,
        )

    def get_failed_downloads(self, scraper: Optional[str] = None) -> List[FailedDownload]:
        """Return all failed downloads, optionally filtered by scraper name."""
        query = {"scraper": scraper} if scraper else {}
        return [
            FailedDownload(**doc)
            for doc in self.failed_downloads.find(query, {"_id": 0})
        ]

    def clear_failed_download(self, url: str) -> None:
        """Remove a failed download record after a successful retry."""
        self.failed_downloads.delete_one({"url": url})

    def find_duplicate(self, firmware_metadata: Firmware):

        vendor = firmware_metadata.vendor
        product = firmware_metadata.product
        version = firmware_metadata.version
        existing = self.collection.find_one(
            {
                "vendor": vendor,
                "product": product,
                "version": version,
            }
        )

        if existing:
            self.logger.warning(f"Duplicate entry for {firmware_metadata}")
            return True

        return False
