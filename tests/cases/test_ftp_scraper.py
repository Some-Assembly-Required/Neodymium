"""End-to-end (S)FTP tests exercising ``neodymium.ftp`` via ``FTPScraper``.

Every test runs twice — once against the dockerised pure-ftpd server (``ftp``)
and once against the in-process paramiko SFTP server (``sftp``) — so both
backends of ``FTPClient`` are validated by the same code paths. ``FTPScraper``
walks the tree, parses each ``metadata.json`` sidecar, and downloads the
firmware it names into MongoDB.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from neodymium.dbmanager.database_manager import DatabaseManager
from neodymium.ftp import FTPClient
from tests.fake_vendor.ftp import remote_dir
from tests.fake_vendor.loader import Device
from tests.scrapers.ftp_scraper import FTPScraper


@pytest.fixture(params=["ftp", "sftp"])
def ftp_endpoint(request: pytest.FixtureRequest) -> dict[str, object]:
    """Yield each backend's connection info so tests cover ftp and sftp."""
    return request.getfixturevalue(f"{request.param}_service")


def _run(scraper: FTPScraper) -> int:
    # run() is a generator; consume it to drive the scrape.
    return len(list(scraper.run()))


def test_scrapes_all_devices_into_mongo(
    ftp_endpoint: dict[str, object], dm: DatabaseManager, ftp_devices: list[Device]
) -> None:
    scraper = FTPScraper(dm, base_url=str(ftp_endpoint["url"]))
    yielded = _run(scraper)

    assert yielded == len(ftp_devices)
    assert dm.collection.count_documents({}) == len(ftp_devices)


def test_stored_metadata_and_checksums(
    ftp_endpoint: dict[str, object], dm: DatabaseManager, ftp_devices: list[Device]
) -> None:
    _run(FTPScraper(dm, base_url=str(ftp_endpoint["url"])))

    for device in ftp_devices:
        doc = dm.collection.find_one(
            {
                "vendor": device.vendor,
                "product": device.product,
                "version": device.version,
            }
        )
        assert doc is not None, f"missing: {device.slug}"
        # Metadata parsed from the sidecar round-trips back to the fixture.
        assert doc["filename"] == device.filename
        assert doc["hw_rev"] == device.hw_rev
        assert doc["region"] == device.region
        assert doc["description"] == device.description
        assert set(doc["tags"]) == set(device.tags)
        # The stored URL reflects where the file actually lives in the tree.
        expected_tail = f"/{remote_dir(device)}/{device.filename}"
        assert doc["url"].endswith(expected_tail)
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


def test_rerun_deduplicates(
    ftp_endpoint: dict[str, object], dm: DatabaseManager, ftp_devices: list[Device]
) -> None:
    _run(FTPScraper(dm, base_url=str(ftp_endpoint["url"])))
    # Second run: every device is a duplicate (same vendor/product/version),
    # so nothing new is yielded or inserted.
    second = _run(FTPScraper(dm, base_url=str(ftp_endpoint["url"])))

    assert second == 0
    assert dm.collection.count_documents({}) == len(ftp_devices)


def test_ftpclient_walk_and_download(
    ftp_endpoint: dict[str, object], ftp_devices: list[Device]
) -> None:
    """Directly exercise FTPClient: enumerate the tree and pull a file."""
    client = FTPClient.from_url(str(ftp_endpoint["url"]))
    with client:
        # Walking the base path reaches every device's firmware + sidecar.
        found_files: set[str] = set()
        for _dirpath, _dirs, files in client.walk(client.base_path):
            found_files.update(f.name for f in files)

        for device in ftp_devices:
            assert device.filename in found_files
        assert "metadata.json" in found_files

        # Listing one device's directory and reading the file it holds yields
        # the exact fixture bytes. base_path already points at "firmware", so
        # strip that leading segment from remote_dir().
        device = ftp_devices[0]
        subpath = remote_dir(device).split("/", 1)[1]
        device_dir = f"{client.base_path}/{subpath}"
        entries = {e.name: e for e in client.listdir(device_dir)}
        assert set(entries) >= {device.filename, "metadata.json"}

        data = client.read_bytes(entries[device.filename].path)
        assert data == device.content
