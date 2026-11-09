"""Fake-vendor fixture server: a Flask app serving synthetic firmware sites."""

from tests.fake_vendor.app import create_app

__all__ = ["create_app"]
