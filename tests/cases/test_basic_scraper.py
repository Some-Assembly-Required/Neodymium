"""End-to-end: BasicScraper scrapes the fake-vendor /basic/ site into MongoDB.

Validates the whole framework: Flask fixture server + ephemeral Mongo + the
scraper contract. The scraper is given credentials, so devices gated behind
Basic Auth (including the both-gated one) must still land in the database —
that is the proof that Basic Auth scraping works.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from neodymium.dbmanager.database_manager import DatabaseManager
from tests.fake_vendor.loader import Device
from tests.scrapers.basic_scraper import BasicScraper

CREDENTIALS = ("admin", "secret")


def _run(scraper: BasicScraper) -> int:
    # run() is a generator; consume it to drive the scrape.
    return len(list(scraper.run()))


def test_scrapes_all_devices_into_mongo(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device]
) -> None:
    scraper = BasicScraper(dm, base_url=fake_vendor_url, credentials=CREDENTIALS)
    yielded = _run(scraper)

    assert yielded == len(devices)
    assert dm.collection.count_documents({}) == len(devices)


def test_stored_metadata_and_checksums(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device]
) -> None:
    _run(BasicScraper(dm, base_url=fake_vendor_url, credentials=CREDENTIALS))

    for device in devices:
        doc = dm.collection.find_one(
            {
                "vendor": device.vendor,
                "product": device.product,
                "version": device.version,
            }
        )
        assert doc is not None, f"missing: {device.slug}"
        # Metadata parsed from the served HTML round-trips back to the fixture.
        assert doc["filename"] == device.filename
        assert doc["url"].endswith(f"/basic/device/{device.slug}/download")
        assert doc["hw_rev"] == device.hw_rev
        assert doc["region"] == device.region
        assert doc["description"] == device.description
        assert set(doc["tags"]) == set(device.tags)
        expected_date = (
            datetime.strptime(device.upload_date, "%Y-%m-%d")
            if device.upload_date
            else None
        )
        assert doc["upload_date"] == expected_date
        # File bytes round-trip (size + checksum computed from the download).
        assert doc["file_size"] == len(device.content)
        assert doc["checksum_type"] == "SHA256"
        assert doc["checksum"] == hashlib.sha256(device.content).hexdigest()


def test_gated_device_requires_auth_is_stored(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device]
) -> None:
    # The both-gated device is only reachable with credentials; its presence
    # proves Basic Auth scraping (page + file) worked end-to-end.
    both_gated = next(
        d
        for d in devices
        if d.auth is not None and {"page", "file"} <= set(d.auth.protect)
    )
    _run(BasicScraper(dm, base_url=fake_vendor_url, credentials=CREDENTIALS))

    doc = dm.collection.find_one({"version": both_gated.version})
    assert doc is not None
    assert doc["checksum"] == hashlib.sha256(both_gated.content).hexdigest()


def test_rerun_deduplicates(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device]
) -> None:
    _run(BasicScraper(dm, base_url=fake_vendor_url, credentials=CREDENTIALS))
    # Second run: every device is a duplicate (same vendor/product/version),
    # so nothing new is yielded or inserted.
    second = _run(BasicScraper(dm, base_url=fake_vendor_url, credentials=CREDENTIALS))

    assert second == 0
    assert dm.collection.count_documents({}) == len(devices)
