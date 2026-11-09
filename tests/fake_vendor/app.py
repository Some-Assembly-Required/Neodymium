"""Flask application factory for the fake-vendor fixture server."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from tests.fake_vendor.basic import basic as basic_blueprint
from tests.fake_vendor.loader import load_devices


def create_app(devices_path: str | Path | None = None) -> Flask:
    """Build the fake-vendor Flask app.

    ``devices_path`` points at the device-definition JSON (defaults to the
    bundled ``devices/basic.json``). Devices are loaded once at app creation and
    stored on ``app.config["DEVICES"]`` keyed by slug.
    """
    app = Flask(__name__)
    devices = load_devices(devices_path)
    app.config["DEVICES"] = {d.slug: d for d in devices}

    app.register_blueprint(basic_blueprint, url_prefix="/basic")
    return app
