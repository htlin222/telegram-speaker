"""Utility functions."""

import logging
import os
import socket

import pychromecast

from .models import Device, DeviceType

logger = logging.getLogger(__name__)


def get_local_ip() -> str:
    """Get local IP address for serving audio to Chromecast."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def discover_googlecast_devices(timeout: int = 10) -> list[Device]:
    """Discover Google Cast devices on the network."""
    devices = []
    try:
        logger.info(f"Scanning for Google Cast devices (timeout={timeout}s)...")
        chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        logger.info(f"Found {len(chromecasts)} Google Cast device(s)")
        for cc in chromecasts:
            logger.info(f"  - {cc.cast_info.friendly_name} @ {cc.cast_info.host}")
            devices.append(
                Device(
                    id=str(cc.uuid),
                    name=cc.cast_info.friendly_name,
                    address=cc.cast_info.host,
                    device_type=DeviceType.GOOGLE_CAST,
                )
            )
        browser.stop_discovery()
    except Exception as e:
        logger.error(f"Error discovering Google Cast devices: {e}")
    return devices


def get_macos_say_device() -> Device:
    """Return macOS say as a virtual device."""
    return Device(
        id="macos_say",
        name="macOS Say (Local)",
        address=None,
        device_type=DeviceType.MACOS_SAY,
    )


def discover_all_devices(timeout: int = 10) -> list[Device]:
    """Discover all available playback devices."""
    devices = []

    # Add macOS say if on Darwin
    if os.uname().sysname == "Darwin":
        devices.append(get_macos_say_device())

    # Add Google Cast devices
    devices.extend(discover_googlecast_devices(timeout))

    return devices
