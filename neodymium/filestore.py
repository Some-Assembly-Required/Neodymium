import abc
import logging
import os
import shutil
from pathlib import Path
from typing import Type

import coloredlogs

from .firmware import Firmware

coloredlogs.install(level="INFO")

HEX_DIGITS = "0123456789abcdef"


class FileStore(abc.ABC):
    """
    Abstract base class for all firmware stores.

    Concrete implementations are registered by name and selected at runtime
    via the FILESTORE environment variable (default: "local").
    """

    _REGISTRY: dict[str, Type["FileStore"]] = {}

    @classmethod
    def register(cls, name: str):
        """Class decorator that registers a FileStore implementation by name."""
        def decorator(subclass: Type["FileStore"]) -> Type["FileStore"]:
            cls._REGISTRY[name] = subclass
            return subclass
        return decorator

    @classmethod
    def from_env(cls, root: str) -> "FileStore":
        """Construct this store from environment variables. Override in subclasses."""
        return cls(root)

    @abc.abstractmethod
    def add(self, firmware: Firmware, path: str) -> bool:
        """
        Store the firmware file. Returns True on success (including deduplication),
        False on failure.
        """
        ...


@FileStore.register("local")
class LocalFileStore(FileStore):
    """
    Stores firmware files by content hash with human-readable symlinks:

        {root}/
          firmware/
            {hex}/
              {sha256}          ← real file, named by hash
          by-vendor/
            {vendor}/
              {product}/
                {filename}      → symlink to ../../firmware/{hex}/{sha256}

    Deduplication: if a file with the same SHA256 already exists the binary
    is not written again; only the symlink is created.
    """

    def __init__(self, root: str):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.root = Path(root)
        self.filedata_path = self.root / "firmware"
        self.by_product_path = self.root / "by-vendor"

        for digit in HEX_DIGITS:
            (self.filedata_path / digit).mkdir(parents=True, exist_ok=True)

        self.by_product_path.mkdir(parents=True, exist_ok=True)

    def _dest_dir(self, firmware: Firmware) -> Path:
        """
        Return the directory under by-vendor/ where this firmware's symlink lives.
        Override in a subclass to customise the layout.

        Default: {by_vendor}/{vendor}/{product}/
        """
        return self.by_product_path / firmware.vendor / firmware.product

    def add(self, firmware: Firmware, path: str) -> bool:
        if firmware.checksum is None:
            self.logger.error(
                f"Must call Firmware.calc_file_metadata() before storing Firmware File: {firmware!r}"
            )
            return False

        store_path = self.filedata_path / firmware.checksum[0] / firmware.checksum
        
        # Copy if the file does not already exist, since this is by hash, we know for sure if its a dup or not
        if not store_path.exists():
            try:
                shutil.copy2(path, store_path)
            except OSError as e:
                self.logger.error(f"Failed to copy {path} → {store_path}: {e}")
                return False

        if firmware.filename is None:
            self.logger.error(f"Firmware has no filename, cannot store: {firmware!r}")
            return False

        product_dir = self._dest_dir(firmware)
        product_dir.mkdir(parents=True, exist_ok=True)
        product_path = product_dir / firmware.filename

        if product_path.exists():
            # Symlink already exists — check if it points to the same hash
            if product_path.resolve().name == firmware.checksum:
                return True  # exact duplicate, nothing to do
            # Same name, different content — deconflict with hash suffix
            product_path = product_dir / f"{firmware.filename}_{firmware.checksum[:8]}"

        rel = Path(os.path.relpath(store_path.resolve(), product_path.parent.resolve()))
        try:
            product_path.symlink_to(rel)
        except OSError as e:
            self.logger.error(f"Failed to symlink {product_path} → {store_path}: {e}")
            return False

        return True
