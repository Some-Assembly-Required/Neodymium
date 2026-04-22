from pydantic import BaseModel, Field, field_serializer
from typing import Optional, Set
from datetime import datetime
from enum import Enum
import hashlib


# Add to this Enum as needed
class Tags(str, Enum):
    SOFTWARE = "Software"
    ROUTER = "Router"
    MODEM = "Modem"
    ACCESS_POINT = "Access Point"
    SWITCH = "Network Switch"
    FIREWALL = "Firewall"
    CAMERA = "Camera"
    NAS = "NAS"  # Network Attached Storage
    SERVER = "Server"
    PHONE = "Phone"
    TABLET = "Tablet"
    WATCH = "Watch"
    IOT = "IoT"
    HEADUNIT = "Head Unit"
    ECU = "ECU"  # Electronic Control Unit
    HARDDRIVE = "Hard Drive"
    PLC = "PLC"  # Programmable Logic Controller
    AUDIO_VIDEO = "A/V"
    KVM = "KVM"  # KVM Switch (Keyboard, Video, Mouse)
    UPS = "UPS"  # Uninterruptable Power Supply
    PSU = "PSU"  # Power Supply Unit
    USB_DONGLE = "USB Dongle"
    RANGE_EXTENDER = "Range Extender"
    POWER_LINE_COMM = "Powerline Communication"
    PCI_ADAPTER = "PCI Adapter"
    WALL_PLUG = "Wall Plug"
    SOLAR_PANEL = "Solar Panel"
    ANTENNA = "Antenna"
    MFP = "Multifunctional Peripheral"
    GPON = "Gigabit Passive Optical Network"


class Firmware(BaseModel):
    """Represents a single firmware image and information about how we obtained it"""

    # Properties pulled from online
    vendor: str = Field(description="Firmware vendor/manufacturer")
    product: str = Field(description="product name")
    version: str = Field(description="Software/Firmware version")
    hw_rev: Optional[str] = Field(None, description="Hardware revision/version")
    extra: Optional[dict] = Field(
        None, description="Scraper-specific metadata that does not belong in the universal schema"
    )
    description: Optional[str] = Field(
        None,
        description="Description of the firmware or product. Usually this is the product description or any useful details",
    )
    upload_date: Optional[datetime] = Field(
        None,
        description="When this firmware was published or uploaded to the website it was scraped from",
    )
    region: Optional[str] = Field(
        None,
        description='The region in the world this firmware is intended to run in. Ideally use ISO3166-1 alpha2 (2 char country code) for a country or "Global" if its specifically for all regions',
    )
    filename: Optional[str] = Field(None, description="firmware filename")

    # Scraping Notes
    download_date: Optional[datetime] = Field(
        None, description="When this particular file was downloaded"
    )
    url: Optional[str] = Field(
        None, description="The URL that this firmware was downloaded from"
    )
    dynamic_url: Optional[bool] = Field(
        None,
        description="Whether or not the download URL is a dynamically generated/empheral URL",
    )
    notes: Optional[str] = Field(None, description="Notes about the scrape itself")

    # Calculated values
    file_size: Optional[int] = Field(
        None, description="Firmware file size in bytes", ge=0
    )
    checksum: Optional[str] = Field(None, description="Firmware checksum hexdigest")
    checksum_type: Optional[str] = Field(
        None, description="Type of checksum (MD5, SHA256, etc.)"
    )

    # Additional metadata
    tags: Optional[Set[Tags]] = Field(
        default_factory=set, description="Tags for categorization"
    )

    @field_serializer("tags")
    def serialize_tags(self, tags: Optional[Set[Tags]]) -> Optional[list]:
        if tags is None:
            return None
        return list(tags)

    def __str__(self) -> str:
        return f"{self.vendor} {self.product} {self.version} {self.description}"

    def set_dl_now(self):
        self.download_date = datetime.now()

    def calc_file_metadata(self, filepath: str) -> bool:
        """Calculates and sets the file_size and checksum properies of self"""
        size = 0
        h = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
                    size += len(chunk)

            self.checksum_type = "SHA256"
            self.checksum = h.hexdigest()
            self.file_size = size
        except OSError as e:
            # TODO use a logger
            print(e)
            return False

        return True


class FailedDownload(BaseModel):
    """Records a firmware download that failed, for later retry."""

    url: str
    scraper: str
    firmware: Firmware
    failed_at: datetime = Field(default_factory=datetime.now)
    attempts: int = 1
