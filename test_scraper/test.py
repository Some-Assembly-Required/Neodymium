import logging
import typing

from typing import Optional

from neodymium.scraper import Scraper
from neodymium.firmware import Firmware
from neodymium.dbmanager.database_manager import DatabaseManager
from neodymium.filestore import FileStore


class Test(Scraper):
    def __init__(
        self,
        dm: DatabaseManager,
        fs: Optional[FileStore] = None,
        timeout: int = 0,
        log_level: int = logging.INFO,
    ):
        super().__init__("https://example.com/", dm, fs, timeout, log_level)

    def scrape(self) -> typing.Generator[typing.Tuple[Firmware, str], None, None]:
        yield from []
