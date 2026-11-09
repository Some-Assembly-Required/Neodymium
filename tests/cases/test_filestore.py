"""Verify the LocalFileStore layout after a scrape: content-addressed binaries
plus per-vendor symlinks, and that a re-scrape deduplicates rather than rewrites.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from neodymium.dbmanager.database_manager import DatabaseManager
from neodymium.filestore import LocalFileStore
from tests.fake_vendor.loader import Device
from tests.scrapers.basic_scraper import BasicScraper

CREDENTIALS = ("admin", "secret")


def _run(scraper: BasicScraper) -> None:
    list(scraper.run())


def _hash_file(root: Path, content: bytes) -> Path:
    checksum = hashlib.sha256(content).hexdigest()
    return root / "by-hash" / checksum[0] / checksum


def _symlink(root: Path, device: Device) -> Path:
    return root / device.vendor / device.product / device.filename


def _sig(path: Path) -> tuple[int, int, int]:
    """Stable identity of a file: (inode, size, mtime-ns). Excludes atime."""
    st = path.stat()
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def test_filestore_layout(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device], tmp_path: Path
) -> None:
    root = tmp_path / "store"
    fs = LocalFileStore(str(root))
    _run(BasicScraper(dm, fs, base_url=fake_vendor_url, credentials=CREDENTIALS))

    for device in devices:
        # Content-addressed binary: a real file named by its SHA256, matching bytes.
        hash_file = _hash_file(root, device.content)
        assert hash_file.is_file()
        assert not hash_file.is_symlink()
        assert hash_file.read_bytes() == device.content

        # Human-readable path is a symlink resolving to that binary.
        link = _symlink(root, device)
        assert link.is_symlink()
        assert link.resolve() == hash_file.resolve()
        assert link.read_bytes() == device.content


def test_filestore_deduplicates_on_rerun(
    fake_vendor_url: str, dm: DatabaseManager, devices: list[Device], tmp_path: Path
) -> None:
    root = tmp_path / "store"
    fs = LocalFileStore(str(root))

    _run(BasicScraper(dm, fs, base_url=fake_vendor_url, credentials=CREDENTIALS))
    # Snapshot the stored binaries to prove they aren't rewritten on re-scrape.
    before = {d.slug: _sig(_hash_file(root, d.content)) for d in devices}

    # Second scrape: every device is already in the DB and the store.
    _run(BasicScraper(dm, fs, base_url=fake_vendor_url, credentials=CREDENTIALS))

    for device in devices:
        hash_file = _hash_file(root, device.content)
        assert _sig(hash_file) == before[device.slug], "binary was rewritten"

        # No deconflicted duplicate (filename_<hash8>) was created.
        product_dir = root / device.vendor / device.product
        entries = list(product_dir.iterdir())
        assert len(entries) == 1, f"unexpected extra files: {entries}"
        assert entries[0].name == device.filename
