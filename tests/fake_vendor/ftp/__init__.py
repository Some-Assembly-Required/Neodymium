"""Fake-vendor (S)FTP fixtures: lay out firmware on disk from device JSON, and
serve it in-process over SFTP for tests.

The directory layout produced by :func:`populate` is the same tree a real FTP
site might expose and is shared by both backends under test:

* the dockerised pure-ftpd server bind-mounts it and serves it over FTP;
* :class:`SFTPFixtureServer` serves the same tree in-process over SFTP.
"""

from tests.fake_vendor.ftp.populate import FTP_BASE, metadata, populate, remote_dir

__all__ = ["FTP_BASE", "metadata", "populate", "remote_dir"]
