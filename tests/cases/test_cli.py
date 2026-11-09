"""Full end-to-end test that drives the ``neodymium`` CLI as a subprocess.

Runs the real CLI entrypoint (``python -m neodymium``) with BasicScraper loaded
as a plugin via NEODYMIUM_PLUGINS, pointed at the fake-vendor server. This
exercises the actual user-facing path: argument parsing, ``.env`` loading,
plugin discovery, the scraper registry, the filestore, and the DB wiring —
none of which the in-process tests touch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from neodymium.dbmanager.database_manager import DatabaseManager
from tests.fake_vendor.loader import Device

# The scraper plugin file the CLI is pointed at.
PLUGIN = Path(__file__).resolve().parents[1] / "scrapers" / "basic_scraper.py"

# Passed to the CLI subprocess; must match tests/docker-compose.yml.
MONGO_ENV = {
    "DB_USERNAME": "root",
    "DB_PASSWORD": "example",
    "DB_HOST": "localhost",
    "DB_PORT": "27017",
}


def test_cli_scrapes_into_mongo(
    fake_vendor_url: str,
    dm: DatabaseManager,
    devices: list[Device],
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env.update(MONGO_ENV)
    env["NEODYMIUM_PLUGINS"] = str(PLUGIN)
    env["BASIC_SCRAPER_URL"] = fake_vendor_url
    env["BASIC_SCRAPER_USER"] = "admin"
    env["BASIC_SCRAPER_PASS"] = "secret"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "neodymium",
            "BasicScraper",
            # Point --env at a nonexistent file so no stray local .env interferes.
            "--env",
            str(tmp_path / "none.env"),
            "--output",
            str(tmp_path / "output"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, (
        f"CLI exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "Successfully completed running all scrapers" in result.stdout

    # Firmware scraped by the CLI invocation landed in Mongo.
    assert dm.collection.count_documents({}) == len(devices)
    for device in devices:
        doc = dm.collection.find_one({"version": device.version})
        assert doc is not None, f"CLI did not store: {device.slug}"
        assert doc["filename"] == device.filename
