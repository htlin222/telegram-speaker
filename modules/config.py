"""Configuration and constants."""

import logging
from pathlib import Path

import yaml

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = Path(__file__).parent.parent
CONFIG_FILE = SCRIPT_DIR / "config.yml"
AUDIO_DIR = SCRIPT_DIR / "audio"
AUDIO_DIR.mkdir(exist_ok=True)

# Allowed user IDs
ALLOWED_USERS = {1212454889}


class Config:
    """Application configuration loaded from config.yml."""

    def __init__(self):
        # Import here to avoid circular imports
        from .models import Device

        self._device_class = Device
        self.selected_device: "Device | None" = None
        self.load()

    def load(self):
        """Load configuration from file."""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = yaml.safe_load(f) or {}
                if "selected_device" in data and data["selected_device"]:
                    self.selected_device = self._device_class.from_dict(
                        data["selected_device"]
                    )

    def save(self):
        """Save configuration to file."""
        data = {
            "selected_device": self.selected_device.to_dict()
            if self.selected_device
            else None
        }
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(data, f, default_flow_style=False)


# Global config instance
config = Config()
