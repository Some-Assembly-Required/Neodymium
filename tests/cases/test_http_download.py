"""Unit tests for ``Scraper.http_download`` failure handling.

A transient server error (e.g. Google Drive returning 503) must surface as a
``None`` return so the caller can skip it — not propagate an ``HTTPError`` that
tears down the whole scrape run.
"""

from __future__ import annotations

from unittest import mock

import requests

from neodymium.scraper import Scraper


def _fake_response(status_code: int) -> mock.Mock:
    res = mock.Mock()
    res.status_code = status_code
    res.headers = {}
    http_error = requests.exceptions.HTTPError(f"{status_code} Server Error")
    http_error.response = res
    res.raise_for_status.side_effect = (
        http_error if status_code // 100 in (4, 5) else None
    )
    return res


def test_server_error_returns_none(tmp_path: object) -> None:
    with mock.patch("neodymium.scraper.requests.get", return_value=_fake_response(503)):
        result = Scraper.http_download(
            "https://example.com/firmware.zip", directory=str(tmp_path)
        )
    assert result is None


def test_client_error_returns_none(tmp_path: object) -> None:
    with mock.patch("neodymium.scraper.requests.get", return_value=_fake_response(404)):
        result = Scraper.http_download(
            "https://example.com/firmware.zip", directory=str(tmp_path)
        )
    assert result is None


def test_server_error_ok_500_does_not_short_circuit(tmp_path: object) -> None:
    # With ok_500=True a 5xx is not treated as an error, so raise_for_status is
    # never consulted and the body is streamed as-is.
    res = _fake_response(503)
    res.iter_content.return_value = [b"body"]
    with mock.patch("neodymium.scraper.requests.get", return_value=res):
        result = Scraper.http_download(
            "https://example.com/firmware.zip",
            directory=str(tmp_path),
            ok_500=True,
        )
    assert result is not None
    res.raise_for_status.assert_not_called()
