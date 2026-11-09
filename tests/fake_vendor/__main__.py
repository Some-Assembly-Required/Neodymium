"""Run the fake-vendor fixture server manually: ``python -m tests.fake_vendor``.

Handy for poking the /basic/ site in a browser or with curl. For the test suite
the server is started in-process by a fixture instead (see tests/conftest.py).
"""

from __future__ import annotations

import argparse

from tests.fake_vendor import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fake-vendor fixture server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind")
    parser.add_argument(
        "--devices",
        default=None,
        help="Path to a device-definition JSON (defaults to devices/basic.json)",
    )
    args = parser.parse_args()

    app = create_app(args.devices)
    print(f"fake-vendor serving /basic/ at http://{args.host}:{args.port}/basic/")
    app.run(host=args.host, port=args.port, debug=True)


if __name__ == "__main__":
    main()
