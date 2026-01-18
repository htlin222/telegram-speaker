"""Data models and types."""

from dataclasses import dataclass
from enum import Enum


class DeviceType(Enum):
    """Type of playback device."""

    GOOGLE_CAST = "googlecast"
    MACOS_SAY = "macos_say"


@dataclass
class Device:
    """Represents a playback device."""

    id: str
    name: str
    address: str | None
    device_type: DeviceType

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "device_type": self.device_type.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            address=data.get("address"),
            device_type=DeviceType(data["device_type"]),
        )
