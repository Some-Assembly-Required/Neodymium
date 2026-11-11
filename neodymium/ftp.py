"""(S)FTP helper for scraper implementations.

A small, dependency-light client that lets a :class:`~neodymium.scraper.Scraper`
*enumerate* and *download* firmware from an FTP, FTPS, or SFTP server without
caring which protocol is behind the URL. It mirrors the ergonomics of
``Scraper.http_download``: build a client, walk the tree, and pull files into a
temp directory.

Backends
--------
* ``ftp`` / ``ftps`` -> the standard library :mod:`ftplib` (FTPS adds TLS).
* ``sftp``           -> ``paramiko`` over SSH, imported lazily so plain-FTP
  users never need it installed.

Both expose the same surface via :class:`FTPClient`: ``listdir`` / ``walk`` for
enumeration, ``download`` for streaming a file to disk, and ``read_bytes`` /
``read_text`` for small sidecar files (e.g. a ``metadata.json``).
"""

from __future__ import annotations

import contextlib
import ftplib
import io
import logging
import os
import posixpath
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

DEFAULT_FTP_PORT = 21
DEFAULT_SFTP_PORT = 22

FTP_SCHEMES = {"ftp", "ftps"}
SFTP_SCHEMES = {"sftp"}


class FTPError(Exception):
    """Raised for any (S)FTP connection, enumeration, or transfer failure."""


@dataclass(frozen=True)
class FTPEntry:
    """One entry in a remote directory listing."""

    name: str
    #: Full remote path (POSIX), suitable for passing back to the client.
    path: str
    is_dir: bool
    #: File size in bytes when known (files only); ``None`` if unknown.
    size: int | None = None

    @property
    def is_file(self) -> bool:
        return not self.is_dir


def _join(path: str, name: str) -> str:
    if path in ("", "."):
        return name
    return posixpath.join(path, name)


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class _FTPBackend:
    """``ftplib``-based backend for ``ftp`` and ``ftps``."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        tls: bool = False,
        timeout: int = 30,
        passive: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._tls = tls
        self._timeout = timeout
        self._passive = passive
        self._ftp: ftplib.FTP | None = None

    def connect(self) -> None:
        ftp: ftplib.FTP = ftplib.FTP_TLS() if self._tls else ftplib.FTP()
        try:
            ftp.connect(self._host, self._port, timeout=self._timeout)
            ftp.login(self._username, self._password)
            if isinstance(ftp, ftplib.FTP_TLS):
                # Encrypt the data channel too, not just the control channel.
                ftp.prot_p()
            ftp.set_pasv(self._passive)
        except ftplib.all_errors as e:
            with contextlib.suppress(Exception):
                ftp.close()
            raise FTPError(
                f"FTP connect/login failed for {self._host}:{self._port}: {e}"
            ) from e
        self._ftp = ftp

    def _require(self) -> ftplib.FTP:
        if self._ftp is None:
            raise FTPError("FTP backend is not connected")
        return self._ftp

    def listdir(self, path: str) -> list[FTPEntry]:
        ftp = self._require()
        norm = path or "."
        try:
            return self._listdir_mlsd(ftp, norm)
        except ftplib.error_perm:
            # MLSD unsupported (5xx): fall back to NLST + probing.
            return self._listdir_fallback(ftp, norm)
        except ftplib.all_errors as e:
            raise FTPError(f"Failed to list {path!r}: {e}") from e

    @staticmethod
    def _listdir_mlsd(ftp: ftplib.FTP, path: str) -> list[FTPEntry]:
        entries: list[FTPEntry] = []
        for name, facts in ftp.mlsd(path):
            typ = facts.get("type", "")
            if typ in ("cdir", "pdir") or name in (".", ".."):
                continue
            is_dir = typ == "dir"
            raw_size = facts.get("size")
            size = int(raw_size) if (raw_size is not None and not is_dir) else None
            entries.append(FTPEntry(name, _join(path, name), is_dir, size))
        return entries

    def _listdir_fallback(self, ftp: ftplib.FTP, path: str) -> list[FTPEntry]:
        try:
            names = ftp.nlst(path)
        except ftplib.error_perm as e:
            if str(e).startswith("550"):  # empty / missing directory
                return []
            raise FTPError(f"Failed to list {path!r}: {e}") from e

        entries: list[FTPEntry] = []
        for raw in names:
            name = posixpath.basename(raw.rstrip("/"))
            if name in ("", ".", ".."):
                continue
            full = _join(path, name)
            is_dir = self._is_dir(ftp, full)
            size: int | None = None
            if not is_dir:
                with contextlib.suppress(*ftplib.all_errors):
                    size = ftp.size(full)
            entries.append(FTPEntry(name, full, is_dir, size))
        return entries

    @staticmethod
    def _is_dir(ftp: ftplib.FTP, path: str) -> bool:
        current = ftp.pwd()
        try:
            ftp.cwd(path)
        except ftplib.error_perm:
            return False
        finally:
            with contextlib.suppress(*ftplib.all_errors):
                ftp.cwd(current)
        return True

    def retrieve(self, remote_path: str, local_path: str) -> None:
        ftp = self._require()
        try:
            with open(local_path, "wb") as f:
                ftp.retrbinary(f"RETR {remote_path}", f.write)
        except ftplib.all_errors as e:  # all_errors already includes OSError
            with contextlib.suppress(OSError):
                os.remove(local_path)
            raise FTPError(f"Failed to download {remote_path!r}: {e}") from e

    def read_bytes(self, remote_path: str) -> bytes:
        ftp = self._require()
        buf = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {remote_path}", buf.write)
        except ftplib.all_errors as e:
            raise FTPError(f"Failed to read {remote_path!r}: {e}") from e
        return buf.getvalue()

    def close(self) -> None:
        if self._ftp is None:
            return
        with contextlib.suppress(Exception):
            self._ftp.quit()
        with contextlib.suppress(Exception):
            self._ftp.close()
        self._ftp = None


class _SFTPBackend:
    """``paramiko``-based backend for ``sftp`` (lazy import)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        timeout: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._timeout = timeout
        self._transport = None
        self._sftp = None

    def connect(self) -> None:
        try:
            import paramiko
        except ImportError as e:
            raise FTPError(
                "SFTP support requires the 'paramiko' package (pip install paramiko)"
            ) from e

        try:
            transport = paramiko.Transport((self._host, self._port))
            transport.banner_timeout = self._timeout
            transport.connect(username=self._username, password=self._password)
            sftp = paramiko.SFTPClient.from_transport(transport)
        except Exception as e:
            raise FTPError(
                f"SFTP connect/login failed for {self._host}:{self._port}: {e}"
            ) from e
        if sftp is None:
            with contextlib.suppress(Exception):
                transport.close()
            raise FTPError("Failed to open an SFTP channel")
        self._transport = transport
        self._sftp = sftp

    def _require(self):
        if self._sftp is None:
            raise FTPError("SFTP backend is not connected")
        return self._sftp

    def listdir(self, path: str) -> list[FTPEntry]:
        sftp = self._require()
        norm = path or "."
        try:
            attrs = sftp.listdir_attr(norm)
        except OSError as e:
            raise FTPError(f"Failed to list {path!r}: {e}") from e
        entries: list[FTPEntry] = []
        for a in attrs:
            if a.filename in (".", ".."):
                continue
            is_dir = stat.S_ISDIR(a.st_mode or 0)
            size = None if is_dir else a.st_size
            entries.append(FTPEntry(a.filename, _join(norm, a.filename), is_dir, size))
        return entries

    def retrieve(self, remote_path: str, local_path: str) -> None:
        sftp = self._require()
        try:
            sftp.get(remote_path, local_path)
        except OSError as e:
            with contextlib.suppress(OSError):
                os.remove(local_path)
            raise FTPError(f"Failed to download {remote_path!r}: {e}") from e

    def read_bytes(self, remote_path: str) -> bytes:
        sftp = self._require()
        try:
            with sftp.open(remote_path, "rb") as fh:
                return fh.read()
        except OSError as e:
            raise FTPError(f"Failed to read {remote_path!r}: {e}") from e

    def close(self) -> None:
        if self._sftp is not None:
            with contextlib.suppress(Exception):
                self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            with contextlib.suppress(Exception):
                self._transport.close()
            self._transport = None


# --------------------------------------------------------------------------- #
# Public client
# --------------------------------------------------------------------------- #
class FTPClient:
    """A unified (S)FTP client for enumerating and downloading firmware.

    Construct directly, or with :meth:`from_url` to parse connection details
    (and a base path) out of an ``ftp://`` / ``ftps://`` / ``sftp://`` URL. Use
    as a context manager to connect and clean up automatically::

        client = FTPClient.from_url("ftp://user:pass@host/firmware")
        with client:
            for dirpath, dirs, files in client.walk(client.base_path):
                ...
    """

    def __init__(
        self,
        host: str,
        *,
        port: int | None = None,
        username: str = "anonymous",
        password: str = "",
        scheme: str = "ftp",
        timeout: int = 30,
        passive: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        scheme = scheme.lower()
        self._backend: _FTPBackend | _SFTPBackend
        if scheme in FTP_SCHEMES:
            port = port or DEFAULT_FTP_PORT
            self._backend = _FTPBackend(
                host,
                port,
                username,
                password,
                tls=scheme == "ftps",
                timeout=timeout,
                passive=passive,
            )
        elif scheme in SFTP_SCHEMES:
            port = port or DEFAULT_SFTP_PORT
            self._backend = _SFTPBackend(
                host, port, username, password, timeout=timeout
            )
        else:
            raise FTPError(
                f"Unsupported scheme: {scheme!r} (expected ftp, ftps, or sftp)"
            )

        self.scheme = scheme
        self.host = host
        self.port = port
        self.logger = logger
        #: Base path parsed from a URL by :meth:`from_url` (``/`` otherwise).
        self.base_path = "/"
        self._connected = False

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        **kwargs: object,
    ) -> FTPClient:
        """Build a client from a URL. Explicit ``username``/``password`` win
        over any embedded in the URL. The URL path becomes ``base_path``."""
        parsed = urlparse(url)
        if not parsed.hostname:
            raise FTPError(f"No host in URL: {url!r}")

        user = (
            username
            if username is not None
            else _unquote_or(parsed.username, "anonymous")
        )
        pw = password if password is not None else _unquote_or(parsed.password, "")

        client = cls(
            parsed.hostname,
            port=parsed.port,
            username=user,
            password=pw,
            scheme=parsed.scheme or "ftp",
            **kwargs,  # type: ignore[arg-type]
        )
        client.base_path = unquote(parsed.path) or "/"
        return client

    # -- lifecycle -------------------------------------------------------- #
    def connect(self) -> FTPClient:
        if not self._connected:
            self._backend.connect()
            self._connected = True
        return self

    def close(self) -> None:
        if self._connected:
            self._backend.close()
            self._connected = False

    def __enter__(self) -> FTPClient:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- enumeration ------------------------------------------------------ #
    def listdir(self, path: str = ".") -> list[FTPEntry]:
        """List a single remote directory (non-recursively)."""
        return self._backend.listdir(path)

    def walk(
        self, path: str = "."
    ) -> Iterator[tuple[str, list[FTPEntry], list[FTPEntry]]]:
        """Recursively walk ``path``, yielding ``(dirpath, dirs, files)`` like
        :func:`os.walk` (top-down)."""
        entries = self.listdir(path)
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if e.is_file]
        yield path, dirs, files
        for d in dirs:
            yield from self.walk(d.path)

    # -- transfer --------------------------------------------------------- #
    def download(
        self,
        remote_path: str,
        directory: str | None = None,
        filename: str | None = None,
        dry_run: bool = False,
        logger: logging.Logger | None = None,
    ) -> str | None:
        """Download ``remote_path`` and return the local filepath, or ``None``
        on failure (mirrors ``Scraper.http_download``)."""
        logger = logger or self.logger
        if filename is None:
            filename = posixpath.basename(remote_path.rstrip("/"))
            if not filename:
                if logger:
                    logger.warning(f"Unable to derive a filename from: {remote_path}")
                return None

        local_path = os.path.join(directory or "./", filename)
        if dry_run:
            return local_path

        if logger:
            logger.info(f"Downloading: {self._display(remote_path)}")
        try:
            self._backend.retrieve(remote_path, local_path)
        except FTPError as e:
            if logger:
                logger.warning(str(e))
            return None
        return local_path

    def read_bytes(self, remote_path: str) -> bytes:
        """Read a (small) remote file fully into memory."""
        return self._backend.read_bytes(remote_path)

    def read_text(self, remote_path: str, encoding: str = "utf-8") -> str:
        """Read a (small) remote text file fully into memory."""
        return self.read_bytes(remote_path).decode(encoding)

    def _display(self, remote_path: str) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{remote_path}"


def _unquote_or(value: str | None, default: str) -> str:
    return unquote(value) if value else default
