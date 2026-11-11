"""Shared pytest fixtures: the fake-vendor server and an ephemeral MongoDB."""

from __future__ import annotations

import ftplib
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from neodymium.dbmanager.database_manager import DatabaseManager
from tests.fake_vendor import create_app
from tests.fake_vendor.ftp import FTP_BASE, populate
from tests.fake_vendor.ftp.sftp_server import SFTPFixtureServer
from tests.fake_vendor.loader import Device, load_devices

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"
DEVICES_DIR = Path(__file__).parent / "fake_vendor" / "devices"

# Credentials for the ephemeral test Mongo; must match tests/docker-compose.yml.
MONGO_USER = "root"
MONGO_PASS = "example"
MONGO_HOST = "localhost"
MONGO_PORT = 27017

# Credentials / endpoint for the test FTP server; must match tests/docker-compose.yml.
FTP_USER = "firmware"
FTP_PASS = "firmware"
FTP_HOST = "127.0.0.1"
FTP_PORT = 2121
# Bind-mounted into the FTP container (compose resolves ./ftp_root next to the
# compose file); populated from JSON before the server starts.
FTP_ROOT = Path(__file__).parent / "ftp_root"

# Escape hatch: when set, assume the backing services are already running
# (CI / a dev loop) and don't manage the docker-compose lifecycle here.
_NO_DOCKER = os.environ.get("NEODYMIUM_TEST_NO_DOCKER", "").lower() in {
    "1",
    "true",
    "yes",
}


# --------------------------------------------------------------------------- #
# Fake-vendor fixture server
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def devices() -> list[Device]:
    """The parsed device definitions served by the /basic/ site."""
    return load_devices()


@pytest.fixture(scope="session")
def fake_vendor_url() -> Iterator[str]:
    """Run the fake-vendor Flask app on an ephemeral port in a background thread.

    Scrapers use real ``requests`` against a real socket, so we need an actual
    listening server rather than Flask's test client.
    """
    app = create_app()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.socket.getsockname()[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# MongoDB
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def _mongo_service() -> Iterator[None]:
    """Bring up the ephemeral, RAM-backed Mongo container for the test session."""
    if _NO_DOCKER:
        yield
        return

    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait", "mongodb"],
        check=True,
    )
    try:
        yield
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
            check=False,
        )


# --------------------------------------------------------------------------- #
# (S)FTP fixture servers
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def ftp_devices() -> list[Device]:
    """Device definitions served over (S)FTP (distinct from the /basic/ set)."""
    return load_devices(DEVICES_DIR / "ftp.json")


def _wait_for_ftp(
    host: str, port: int, user: str, password: str, attempts: int = 40
) -> None:
    """Poll the FTP server until a login succeeds (it has no healthcheck)."""
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            conn = ftplib.FTP()
            conn.connect(host, port, timeout=5)
            conn.login(user, password)
            conn.quit()
            return
        except Exception as e:  # noqa: BLE001 - any failure means "not ready yet"
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"FTP server never became ready at {host}:{port}: {last_err}")


@pytest.fixture(scope="session")
def ftp_service(ftp_devices: list[Device]) -> Iterator[dict[str, object]]:
    """Dockerised pure-ftpd serving a firmware tree populated from JSON.

    The bind-mounted root is (re)populated here before the container starts, so
    the server comes up already serving the fixture devices.
    """
    if FTP_ROOT.exists():
        shutil.rmtree(FTP_ROOT)
    FTP_ROOT.mkdir(parents=True)
    populate(FTP_ROOT, ftp_devices)

    if not _NO_DOCKER:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "ftp"],
            check=True,
        )
    try:
        _wait_for_ftp(FTP_HOST, FTP_PORT, FTP_USER, FTP_PASS)
        yield {
            "host": FTP_HOST,
            "port": FTP_PORT,
            "user": FTP_USER,
            "password": FTP_PASS,
            "url": f"ftp://{FTP_USER}:{FTP_PASS}@{FTP_HOST}:{FTP_PORT}/{FTP_BASE}",
        }
    finally:
        if not _NO_DOCKER:
            # Only tear down the ftp service; the Mongo fixture owns its own.
            subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "rm", "-sf", "ftp"],
                check=False,
            )
        shutil.rmtree(FTP_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def sftp_service(
    ftp_devices: list[Device], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, object]]:
    """In-process SFTP server over the same firmware tree (no docker needed)."""
    root = tmp_path_factory.mktemp("sftp_root")
    populate(root, ftp_devices)

    server = SFTPFixtureServer(root, FTP_USER, FTP_PASS).start()
    assert server.port is not None
    try:
        yield {
            "host": server.host,
            "port": server.port,
            "user": FTP_USER,
            "password": FTP_PASS,
            "url": f"sftp://{FTP_USER}:{FTP_PASS}@{server.host}:{server.port}/{FTP_BASE}",
        }
    finally:
        server.stop()


@pytest.fixture
def dm(_mongo_service: None) -> Iterator[DatabaseManager]:
    """A DatabaseManager against the test Mongo, with collections cleaned per test."""
    manager = DatabaseManager(
        username=MONGO_USER,
        password=MONGO_PASS,
        host=MONGO_HOST,
        port=MONGO_PORT,
    )
    manager.collection.delete_many({})
    manager.failed_downloads.delete_many({})
    try:
        yield manager
    finally:
        manager.collection.delete_many({})
        manager.failed_downloads.delete_many({})
        manager.client.close()
