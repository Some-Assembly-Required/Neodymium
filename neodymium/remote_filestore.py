import json
import logging
import os
from pathlib import Path
from typing import Optional

import coloredlogs
import requests

from .filestore import FileStore, LocalFileStore
from .firmware import Firmware

coloredlogs.install(level="INFO")


@FileStore.register("http-api")
class HttpApiStore(LocalFileStore):
    """
    LocalFileStore that also pushes each firmware file and its metadata to a
    remote HTTP API after storing it locally.

    Configuration (via .env or environment):
        REMOTE_URL      Base URL of the remote API (e.g. https://firmware.example.com)
        REMOTE_API_KEY  Bearer token for authentication (optional)

    Expected API contract:
        POST {REMOTE_URL}/firmware
        Content-Type: multipart/form-data
            file:     the firmware binary
            metadata: JSON-encoded Firmware fields
    """

    _UPLOAD_PATH = "/firmware"

    def __init__(self, root: str, remote_url: str, api_key: Optional[str] = None):
        super().__init__(root)
        self.remote_url = remote_url.rstrip("/")
        self.logger = logging.getLogger(self.__class__.__name__)

        self._session = requests.Session()
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

    @classmethod
    def from_env(cls, root: str) -> "HttpApiStore":
        remote_url = os.environ["REMOTE_URL"]
        api_key = os.environ.get("REMOTE_API_KEY")
        return cls(root, remote_url=remote_url, api_key=api_key)

    def add(self, firmware: Firmware, path: str) -> bool:
        result = super().add(firmware, path)
        self._push(firmware, path)
        return result

    def _push(self, firmware: Firmware, path: str) -> None:
        url = self.remote_url + self._UPLOAD_PATH
        metadata = firmware.model_dump(mode="json")

        try:
            filename = firmware.filename or Path(path).name
            with open(path, "rb") as f:
                resp = self._session.post(
                    url,
                    files={"file": (filename, f, "application/octet-stream")},
                    data={"metadata": json.dumps(metadata)},
                    timeout=300,
                )

            if resp.ok:
                self.logger.info(f"Remote upload OK: {filename}")
            else:
                self.logger.warning(
                    f"Remote upload failed [{resp.status_code}]: "
                    f"{filename} — {resp.text[:200]}"
                )
        except Exception as exc:
            self.logger.warning(f"Remote upload error for {firmware.filename}: {exc}")
