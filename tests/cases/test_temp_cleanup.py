"""Guard against /tmp filling up unbounded during a scrape.

``run()`` downloads each firmware into a single per-run temp dir before copying
it into the real filestore. The downloaded temp copy must be deleted once it's
stored, otherwise every file in a run piles up in /tmp until the run ends (and
leaks entirely if the run is killed). These tests pin the invariant that the
temp dir holds at most one in-flight file no matter how many devices are
scraped, and that it's gone once the run completes.
"""

from __future__ import annotations

import os
from pathlib import Path

from neodymium.dbmanager.database_manager import DatabaseManager
from neodymium.filestore import LocalFileStore
from tests.fake_vendor.loader import Device
from tests.scrapers.basic_scraper import BasicScraper

CREDENTIALS = ("admin", "secret")


def _temp_file_count(scraper: BasicScraper) -> int:
    temp_dir = scraper.temp_dir
    assert temp_dir is not None
    return len(os.listdir(temp_dir))


def test_temp_dir_stays_bounded_during_run(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device], tmp_path: Path
) -> None:
    # Needs more than one device, or "bounded by one file" is trivially true
    # and a regression (removed cleanup) couldn't be detected.
    assert len(devices) > 1

    fs = LocalFileStore(str(tmp_path / "store"))
    scraper = BasicScraper(dm, fs, base_url=fake_vendor_url, credentials=CREDENTIALS)

    peak = 0
    yielded = 0
    for _ in scraper.run():
        # Each yield happens right after a download; the previous file must
        # already be gone, so only the in-flight one is ever present.
        peak = max(peak, _temp_file_count(scraper))
        yielded += 1

    assert yielded == len(devices)
    assert peak <= 1, f"temp dir grew to {peak} files; downloads aren't cleaned up"


def test_temp_dir_removed_after_run(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device], tmp_path: Path
) -> None:
    fs = LocalFileStore(str(tmp_path / "store"))
    scraper = BasicScraper(dm, fs, base_url=fake_vendor_url, credentials=CREDENTIALS)

    list(scraper.run())

    # The TemporaryDirectory context exits when the run completes, leaving
    # nothing behind on disk.
    assert scraper.temp_dir is not None
    assert not os.path.exists(scraper.temp_dir)
