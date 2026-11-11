"""A minimal in-process SFTP server for tests.

Serves a directory tree (the one :func:`populate` writes) read-only over SFTP so
the ``sftp`` backend of :class:`neodymium.ftp.FTPClient` can be tested without
docker, mirroring how the fake-vendor webserver runs in-process. It is a test
fixture, not production code: password auth only, one generated host key, and
every path is mapped under a fixed root.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
from pathlib import Path

import paramiko


class _SFTPServerInterface(paramiko.SFTPServerInterface):
    """Maps every remote path under a fixed root and serves files read-only."""

    def __init__(self, server, root: str, *args: object, **kwargs: object) -> None:
        super().__init__(server, *args, **kwargs)
        self.root = root

    def _realpath(self, path: str) -> str:
        # Collapse the request to an absolute path, then re-root it so ".."
        # can never escape the served tree.
        rel = os.path.normpath("/" + path).lstrip("/")
        return os.path.join(self.root, rel)

    def list_folder(self, path: str):
        real = self._realpath(path)
        try:
            out = []
            for name in os.listdir(real):
                attr = paramiko.SFTPAttributes.from_stat(
                    os.stat(os.path.join(real, name))
                )
                attr.filename = name
                out.append(attr)
            return out
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def stat(self, path: str):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._realpath(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def lstat(self, path: str):
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(self._realpath(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def open(self, path: str, flags: int, attr):
        real = self._realpath(path)
        try:
            fd = os.open(real, flags)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        handle = paramiko.SFTPHandle(flags)
        handle.filename = real  # type: ignore[attr-defined]
        handle.readfile = os.fdopen(fd, "rb")  # type: ignore[attr-defined]
        return handle


class _ServerInterface(paramiko.ServerInterface):
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    def check_auth_password(self, username: str, password: str) -> int:
        if username == self._username and password == self._password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class SFTPFixtureServer:
    """Serves ``root`` over SFTP on an ephemeral port in a background thread."""

    def __init__(
        self,
        root: str | Path,
        username: str,
        password: str,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.root = str(root)
        self.username = username
        self.password = password
        self.host = host
        self.port: int | None = None
        self._requested_port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._host_key = paramiko.RSAKey.generate(2048)

    def start(self) -> SFTPFixtureServer:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self._requested_port))
        sock.listen(10)
        sock.settimeout(0.5)
        self.port = sock.getsockname()[1]
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        transport = paramiko.Transport(client)
        transport.add_server_key(self._host_key)
        transport.set_subsystem_handler(
            "sftp", paramiko.SFTPServer, _SFTPServerInterface, self.root
        )
        try:
            transport.start_server(
                server=_ServerInterface(self.username, self.password)
            )
        except Exception:
            transport.close()
            return
        # Keep the transport alive while it (and we) are active; paramiko
        # services SFTP requests on its own threads.
        while transport.is_active() and not self._stop.is_set():
            self._stop.wait(0.2)
        transport.close()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
