"""Shared pytest fixtures: the fake-vendor server and an ephemeral MongoDB."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from neodymium.dbmanager.database_manager import DatabaseManager
from tests.fake_vendor import create_app
from tests.fake_vendor.loader import Device, load_devices

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"

# Credentials for the ephemeral test Mongo; must match tests/docker-compose.yml.
MONGO_USER = "root"
MONGO_PASS = "example"
MONGO_HOST = "localhost"
MONGO_PORT = 27017

# Escape hatch: when set, assume Mongo is already running (CI / a dev loop) and
# don't manage the docker-compose lifecycle here.
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
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
        check=True,
    )
    try:
        yield
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
            check=False,
        )


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
