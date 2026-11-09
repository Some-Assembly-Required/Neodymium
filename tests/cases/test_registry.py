"""Regression test: Scraper.registry() must be idempotent for plugin modules.

Loading a plugin via a filesystem path used to re-execute the file on every
registry() call, re-running the ``class X(Scraper)`` statement and appending a
duplicate to the registry each time.
"""

from __future__ import annotations

import sys
from pathlib import Path

from neodymium.scraper import Scraper

PROBE = "IdempotencyProbeScraper"


def _count(name: str) -> int:
    return sum(1 for cls in Scraper._REGISTRY if cls.__name__ == name)


def test_registry_is_idempotent_for_plugins(tmp_path: Path) -> None:
    plugin = tmp_path / "probe_plugin.py"
    plugin.write_text(
        "from neodymium.scraper import Scraper\n\n\nclass "
        + PROBE
        + "(Scraper):\n    pass\n"
    )

    assert _count(PROBE) == 0  # sanity: a fresh, unique scraper name
    try:
        Scraper.registry([plugin])
        Scraper.registry([plugin])
        assert _count(PROBE) == 1, "plugin registered more than once"
    finally:
        # Keep global registry state from leaking into other tests.
        Scraper._REGISTRY[:] = [c for c in Scraper._REGISTRY if c.__name__ != PROBE]
        sys.modules.pop(plugin.stem, None)
