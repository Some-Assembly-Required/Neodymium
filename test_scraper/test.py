import logging
import typing

from neodymium.dbmanager.database_manager import DatabaseManager
from neodymium.filestore import FileStore
from neodymium.firmware import Firmware
from neodymium.scraper import Scraper


class Test(Scraper):
    def __init__(
        self,
        dm: DatabaseManager,
        fs: FileStore | None = None,
        timeout: int = 0,
        log_level: int = logging.INFO,
    ):
        super().__init__("https://example.com/", dm, fs, timeout, log_level)

    def scrape(self) -> typing.Generator[tuple[Firmware, str], None, None]:
        yield from []
