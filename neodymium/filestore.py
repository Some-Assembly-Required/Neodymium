import os
from pathlib import Path
import logging
import shutil

import coloredlogs

from .firmware import Firmware

coloredlogs.install(level="INFO")
HEX_DIGITS = "0123456789abcdef"


class FileStore:
    def __init__(self, root: str):
        self.logger = logging.getLogger(self.__class__.__name__)

        self.root: Path = Path(root)
        self.filedata_path: Path = self.root / "firmware"
        self.by_product_path: Path = self.root / "by-vendor"

        for digit in HEX_DIGITS:
            hexpath = self.filedata_path / digit
            if not hexpath.exists():
                hexpath.mkdir(parents=True, exist_ok=True)

        if not self.by_product_path.exists():
            self.by_product_path.mkdir(parents=True, exist_ok=True)

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
                self.logger.error(f"Failed to copy {path} -> {store_path}: {e}")
                return False

        if firmware.filename is None:
            self.logger.error(f"Firmware has no filename, cannot store: {firmware!r}")
            return False

        product_dir = self.by_product_path / firmware.vendor / firmware.product
        if not product_dir.exists():
            product_dir.mkdir(parents=True, exist_ok=True)

        product_path = product_dir / firmware.filename

        # If a symlink at this path already exists, we need to check if the symlink points to the same path as the one just created
        # If so, then we are for sure adding a duplicate and can do nothing
        # If not, (same vendor, product, and filename, but diff hash) then we need to de-conflict this product path
        if product_path.exists():
            hash_path = product_path.resolve()
            checksum = str(hash_path.name)
            if checksum == firmware.checksum:
                return True

            # Different Checksum but vendor, product and filename are the same
            # Add the first 8 digits of the hash to the end of the filename to deconflict
            product_path = product_dir / f"{firmware.filename}_{firmware.checksum[:8]}"

        rel = Path(os.path.relpath(store_path.resolve(), product_path.parent.resolve()))
        try:
            product_path.symlink_to(rel)
        except OSError as e:
            self.logger.error(f"Failed to symlink {product_path} -> {store_path}: {e}")
            return False

        return True
