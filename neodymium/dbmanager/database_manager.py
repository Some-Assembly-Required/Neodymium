import coloredlogs, logging

from pymongo import MongoClient

from neodymium.firmware import Firmware

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
        self.logger = logging.getLogger(self.__class__.__name__)

    def add_firmware(self, firmware_metadata: Firmware):

        firmware = self.db.firmware
        firmware.insert_one(firmware_metadata.model_dump()).inserted_id

    def check_url(self, firmware_url: str):

        url_exists = self.collection.find_one({"url": firmware_url})
        if url_exists:
            return True

        return False

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
