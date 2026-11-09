"""The ``/basic/`` site: a static, server-rendered (Jinja) vendor site.

This is the first of several planned site "styles" (future ones — e.g. a
client-rendered React style — will mount under their own prefixes). Pages here
are fully rendered server-side from the loaded device JSON, so a scraper can
walk them with plain HTML parsing.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    Response,
    current_app,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import NotFound

from tests.fake_vendor.loader import FILE, PAGE, Device

basic = Blueprint("basic", __name__, template_folder="templates")


def _devices() -> dict[str, Device]:
    return current_app.config["DEVICES"]


def _auth_gate(device: Device, target: str) -> Response | None:
    """Return a 401 response if ``target`` is gated and credentials are bad/missing."""
    if device.auth is None or not device.auth.protects(target):
        return None

    creds = request.authorization
    if (
        creds is not None
        and creds.type == "basic"
        and creds.username == device.auth.username
        and creds.password == device.auth.password
    ):
        return None

    return Response(
        "Authentication required",
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="basic"'},
    )


@basic.route("/")
def index() -> str:
    # The index is always public so a scraper can discover devices.
    return render_template("index.html", devices=list(_devices().values()))


@basic.route("/device/<slug>")
def device_page(slug: str) -> Response | str:
    device = _devices().get(slug)
    if device is None:
        raise NotFound()

    gate = _auth_gate(device, PAGE)
    if gate is not None:
        return gate

    download_url = url_for("basic.device_download", slug=slug)
    return render_template("device.html", device=device, download_url=download_url)


@basic.route("/device/<slug>/download")
def device_download(slug: str) -> Response:
    device = _devices().get(slug)
    if device is None:
        raise NotFound()

    gate = _auth_gate(device, FILE)
    if gate is not None:
        return gate

    return Response(
        device.content,
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{device.filename}"',
            "Content-Length": str(len(device.content)),
        },
    )
