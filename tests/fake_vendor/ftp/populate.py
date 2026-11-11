"""Lay out fake firmware on disk from device definitions.

Each device becomes a directory ``firmware/<vendor>/<product>/<version>/``
holding the firmware file plus a ``metadata.json`` sidecar carrying the online
metadata a scraper would otherwise read from an HTML page. The nesting is
deliberate so the FTP scraper has to *walk* the tree, exercising
``FTPClient.walk``.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

from tests.fake_vendor.loader import Device, _slugify

#: Top-level directory the scraper is pointed at (relative to the FTP root).
FTP_BASE = "firmware"

#: Name of the per-device sidecar the scraper parses for metadata.
METADATA_NAME = "metadata.json"


def remote_dir(device: Device) -> str:
    """POSIX directory (relative to the FTP root) a device is laid out in."""
    return posixpath.join(
        FTP_BASE,
        _slugify(device.vendor),
        _slugify(device.product),
        _slugify(device.version),
    )


def metadata(device: Device) -> dict:
    """The sidecar metadata written next to a device's firmware file."""
    return {
        "vendor": device.vendor,
        "product": device.product,
        "version": device.version,
        "hw_rev": device.hw_rev,
        "region": device.region,
        "upload_date": device.upload_date,
        "description": device.description,
        "filename": device.filename,
        "tags": list(device.tags),
        "extra": device.extra,
    }


def populate(root: str | Path, devices: list[Device]) -> Path:
    """Write the firmware tree for ``devices`` under ``root`` and return it.

    Everything is made world-readable so a daemon running as a different user
    (e.g. the dockerised FTP server) can serve it.
    """
    root = Path(root)
    for device in devices:
        device_dir = root / remote_dir(device)
        device_dir.mkdir(parents=True, exist_ok=True)
        (device_dir / device.filename).write_bytes(device.content)
        (device_dir / METADATA_NAME).write_text(json.dumps(metadata(device), indent=2))

    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return root
