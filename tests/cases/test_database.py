"""DatabaseManager consistency tests for the failed-downloads collection.

The scraper happy-path (firmware collection) is covered in test_basic_scraper;
this covers the retry bookkeeping: upsert, attempt counting, scraper filtering,
and clearing — all round-tripped through Mongo.
"""

from __future__ import annotations

from neodymium.dbmanager.database_manager import DatabaseManager
from neodymium.firmware import FailedDownload, Firmware

URL = "http://vendor.example/fw/acme-1.0.0.bin"


def _firmware() -> Firmware:
    return Firmware(
        vendor="Acme",
        product="Router",
        version="1.0.0",
        filename="acme-1.0.0.bin",
        url=URL,
    )


def test_failed_download_roundtrip(dm: DatabaseManager) -> None:
    dm.push_failed_download(
        FailedDownload(url=URL, scraper="BasicScraper", firmware=_firmware())
    )

    records = dm.get_failed_downloads(scraper="BasicScraper")
    assert len(records) == 1
    record = records[0]
    assert record.url == URL
    assert record.scraper == "BasicScraper"
    assert record.attempts == 1
    # The embedded Firmware document round-trips intact.
    assert record.firmware.vendor == "Acme"
    assert record.firmware.filename == "acme-1.0.0.bin"


def test_repeated_failure_increments_attempts(dm: DatabaseManager) -> None:
    fd = FailedDownload(url=URL, scraper="BasicScraper", firmware=_firmware())
    dm.push_failed_download(fd)
    dm.push_failed_download(fd)

    records = dm.get_failed_downloads()
    assert len(records) == 1, "repeat failures must upsert, not duplicate"
    assert records[0].attempts == 2


def test_get_failed_downloads_filters_by_scraper(dm: DatabaseManager) -> None:
    dm.push_failed_download(
        FailedDownload(url=URL, scraper="BasicScraper", firmware=_firmware())
    )

    assert len(dm.get_failed_downloads(scraper="BasicScraper")) == 1
    assert dm.get_failed_downloads(scraper="OtherScraper") == []


def test_clear_failed_download(dm: DatabaseManager) -> None:
    dm.push_failed_download(
        FailedDownload(url=URL, scraper="BasicScraper", firmware=_firmware())
    )
    dm.clear_failed_download(URL)

    assert dm.get_failed_downloads() == []
