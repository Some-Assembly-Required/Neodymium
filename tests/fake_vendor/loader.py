"""Load fake-device definitions from JSON for the fake-vendor fixture server.

A "device" here is one firmware image plus the online metadata a vendor site
would display for it. The field set mirrors the "Properties pulled from online"
section of ``neodymium.firmware.Firmware``; the scraper-set and computed fields
(url, checksum, file_size, ...) are deliberately *not* defined here — the server
derives the URL from its own routes and the scraper computes the rest from the
bytes it downloads.

The downloadable bytes are defined per device via either ``content`` (an inline
string) or ``size_bytes`` (deterministically generated filler), so downloads and
checksums are reproducible across runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from neodymium.firmware import Tags

# Which targets a device's Basic Auth may protect.
PAGE = "page"
FILE = "file"
_VALID_PROTECT = {PAGE, FILE}

DEVICES_DIR = Path(__file__).parent / "devices"


@dataclass(frozen=True)
class Auth:
    """HTTP Basic Auth gate for a device's page and/or download."""

    username: str
    password: str
    protect: frozenset[str]  # subset of {"page", "file"}

    def protects(self, target: str) -> bool:
        return target in self.protect


@dataclass(frozen=True)
class Device:
    """A single fake firmware image and its online metadata."""

    vendor: str
    product: str
    version: str
    filename: str
    content: bytes
    slug: str
    hw_rev: str | None = None
    description: str | None = None
    region: str | None = None
    upload_date: str | None = None  # ISO date string as a site would render it
    tags: list[str] = field(default_factory=list)
    extra: dict | None = None
    auth: Auth | None = None


def _slugify(*parts: str) -> str:
    joined = "-".join(parts)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


def _firmware_bytes(entry: dict) -> bytes:
    """Resolve a device's downloadable bytes from its JSON entry.

    ``content`` (inline string) wins; otherwise ``size_bytes`` generates
    deterministic filler seeded by the filename so the output is reproducible.
    """
    if "content" in entry:
        return entry["content"].encode("utf-8")

    size = int(entry.get("size_bytes", 0))
    if size <= 0:
        return b""
    seed = (entry.get("filename") or "firmware").encode("utf-8")
    reps = (size // len(seed)) + 1
    return (seed * reps)[:size]


def _parse_auth(entry: dict) -> Auth | None:
    raw = entry.get("auth")
    if raw is None:
        return None
    if raw.get("type", "basic") != "basic":
        raise ValueError(f"Unsupported auth type: {raw.get('type')!r}")
    protect = frozenset(raw.get("protect", []))
    invalid = protect - _VALID_PROTECT
    if invalid:
        raise ValueError(f"Invalid auth.protect targets: {sorted(invalid)}")
    return Auth(username=raw["username"], password=raw["password"], protect=protect)


def _parse_device(entry: dict) -> Device:
    # Validate tags against the Firmware Tags enum up front so a bad fixture
    # fails loudly at load time rather than during a scrape.
    tags = list(entry.get("tags", []))
    for tag in tags:
        Tags(tag)

    vendor = entry["vendor"]
    product = entry["product"]
    version = entry["version"]
    return Device(
        vendor=vendor,
        product=product,
        version=version,
        filename=entry["filename"],
        content=_firmware_bytes(entry),
        slug=_slugify(vendor, product, version),
        hw_rev=entry.get("hw_rev"),
        description=entry.get("description"),
        region=entry.get("region"),
        upload_date=entry.get("upload_date"),
        tags=tags,
        extra=entry.get("extra"),
        auth=_parse_auth(entry),
    )


def load_devices(path: str | Path | None = None) -> list[Device]:
    """Load and parse a device-definition JSON file into ``Device`` objects."""
    path = Path(path) if path is not None else DEVICES_DIR / "basic.json"
    data = json.loads(Path(path).read_text())
    devices = [_parse_device(entry) for entry in data["devices"]]

    slugs = [d.slug for d in devices]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    if dupes:
        raise ValueError(f"Duplicate device slugs: {sorted(dupes)}")
    return devices
