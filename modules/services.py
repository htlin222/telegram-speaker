"""Core services: Cast connection, audio server, playback."""

import asyncio
import logging
import os
import subprocess
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pychromecast

from .models import Device, DeviceType
from .utils import get_local_ip

logger = logging.getLogger(__name__)


class SilentHTTPHandler(SimpleHTTPRequestHandler):
    """HTTP handler that suppresses access logs."""

    def log_message(self, format, *args):
        """Suppress all HTTP access logs."""
        pass


class CastConnection:
    """Manage persistent connection to Google Cast device."""

    def __init__(self):
        self.cast = None
        self.browser = None
        self.connected = False
        self.device_id: str | None = None

    def connect(self, device: Device, timeout: int = 15) -> bool:
        """Connect to a Google Cast device."""
        if device.device_type != DeviceType.GOOGLE_CAST:
            return False

        # Disconnect existing connection if different device
        if self.device_id and self.device_id != device.id:
            self.disconnect()

        try:
            logger.info(f"Connecting to {device.name}...")
            chromecasts, self.browser = pychromecast.get_chromecasts(timeout=timeout)

            self.cast = None
            for cc in chromecasts:
                if str(cc.uuid) == device.id:
                    self.cast = cc
                    break

            if not self.cast:
                logger.error(f"Device not found: {device.name}")
                self.disconnect()
                return False

            self.cast.wait(timeout=10)
            time.sleep(1)  # Stabilize connection

            self.device_id = device.id
            self.connected = True
            logger.info(f"Connected to {self.cast.cast_info.friendly_name}")

            # Stop discovery but keep connection
            if self.browser:
                self.browser.stop_discovery()
                self.browser = None

            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        """Disconnect from the device."""
        if self.browser:
            self.browser.stop_discovery()
            self.browser = None
        if self.cast:
            self.cast.disconnect()
        self.cast = None
        self.connected = False
        self.device_id = None
        logger.info("Disconnected")

    def is_connected(self) -> bool:
        """Check if still connected."""
        if not self.cast or not self.connected:
            return False
        try:
            return self.cast.socket_client.is_connected
        except Exception:
            return False

    def get_cast(self) -> "pychromecast.Chromecast | None":
        """Get the cast device if connected."""
        if self.is_connected():
            return self.cast
        return None


# Global cast connection instance
cast_connection = CastConnection()


class AudioServer:
    """Simple HTTP server to serve audio files to Chromecast."""

    def __init__(self, directory: Path, port: int = 0):
        self.directory = directory
        self.port = port
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self._original_dir: str | None = None

    def start(self) -> int:
        """Start the HTTP server."""
        self._original_dir = os.getcwd()
        os.chdir(self.directory)

        self.server = HTTPServer(("", self.port), SilentHTTPHandler)
        self.port = self.server.server_address[1]

        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

        return self.port

    def stop(self):
        """Stop the HTTP server."""
        if self.server:
            self.server.shutdown()
        if self._original_dir:
            os.chdir(self._original_dir)


def play_on_googlecast(device: Device, audio_path: Path) -> bool:
    """Play audio file on Google Cast device."""
    server = None
    used_cached = False
    try:
        # Verify audio file exists and has content
        if not audio_path.exists():
            logger.error(f"Audio file not found: {audio_path}")
            return False
        file_size = audio_path.stat().st_size
        if file_size < 100:
            logger.error(f"Audio file too small: {file_size} bytes")
            return False
        logger.info(f"Audio file: {audio_path.name} ({file_size} bytes)")

        # Try to use cached connection first
        cast = cast_connection.get_cast()
        if cast and cast_connection.device_id == device.id:
            logger.info("Using cached connection")
            used_cached = True
        else:
            # No cached connection, establish new one
            if not cast_connection.connect(device):
                logger.error("Failed to connect to device")
                return False
            cast = cast_connection.get_cast()
            if not cast:
                logger.error("No cast device after connect")
                return False

        # Start HTTP server to serve the audio
        server = AudioServer(audio_path.parent)
        port = server.start()

        local_ip = get_local_ip()
        url = f"http://{local_ip}:{port}/{audio_path.name}"
        logger.info(f"Serving audio at {url}")

        mc = cast.media_controller

        # If not cached, give more time for connection to stabilize
        if not used_cached:
            time.sleep(1)

        # Play media
        logger.info("Sending play_media command...")
        mc.play_media(url, "audio/mpeg")

        # Wait for media to be accepted
        time.sleep(0.5)

        # Check if already finished (very short audio)
        state = mc.status.player_state if mc.status else None
        idle_reason = mc.status.idle_reason if mc.status else None
        logger.info(f"Initial state: {state}, idle_reason: {idle_reason}")

        if state == "IDLE" and idle_reason == "FINISHED":
            logger.info("Playback completed quickly (very short audio)")
            return True

        # Try to wait for active state (but don't retry play_media)
        try:
            mc.block_until_active(timeout=10)
        except Exception as e:
            logger.warning(f"block_until_active failed: {e}")
            # Check if it finished while waiting
            state = mc.status.player_state if mc.status else None
            idle_reason = mc.status.idle_reason if mc.status else None
            if state == "IDLE" and idle_reason == "FINISHED":
                logger.info("Playback completed during wait")
                return True

        # Check media status
        state = mc.status.player_state if mc.status else None
        idle_reason = mc.status.idle_reason if mc.status else None
        logger.info(f"Player state: {state}, idle_reason: {idle_reason}")

        # If already IDLE with FINISHED reason, playback completed quickly
        if state == "IDLE":
            if idle_reason == "FINISHED":
                logger.info("Playback completed quickly (short audio)")
                return True
            elif idle_reason and idle_reason not in ("FINISHED", "INTERRUPTED"):
                logger.error(f"Playback failed, reason: {idle_reason}")
                return False

        # Wait for playback to complete with timeout
        timeout = 60  # Max 1 minute for normal audio
        start_time = time.time()
        played = False

        while time.time() - start_time < timeout:
            state = mc.status.player_state if mc.status else None
            idle_reason = mc.status.idle_reason if mc.status else None

            if state == "PLAYING":
                played = True
            elif state == "IDLE":
                if played or idle_reason == "FINISHED":
                    # Finished playing successfully
                    break
                elif idle_reason and idle_reason not in ("FINISHED", "INTERRUPTED"):
                    logger.error(f"Playback error: {idle_reason}")
                    return False
                elif time.time() - start_time > 5:
                    # Short audio likely finished, don't wait forever
                    logger.info("Short audio likely finished")
                    break
            time.sleep(0.3)

        logger.info("Playback finished successfully")
        return True

    except Exception as e:
        logger.error(f"Error playing on Google Cast: {e}", exc_info=True)
        return False
    finally:
        if server:
            try:
                server.stop()
            except Exception:
                pass


def play_on_macos_say(audio_path: Path) -> bool:
    """Play audio using macOS afplay command."""
    try:
        subprocess.run(["afplay", str(audio_path)], check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error playing audio with afplay: {e}")
        return False


def speak_text_macos(text: str) -> bool:
    """Speak text using macOS say command."""
    try:
        subprocess.run(["say", text], check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error with macOS say: {e}")
        return False


async def play_audio(device: Device, audio_path: Path) -> bool:
    """Play audio on the specified device."""
    loop = asyncio.get_event_loop()
    if device.device_type == DeviceType.MACOS_SAY:
        return await loop.run_in_executor(None, play_on_macos_say, audio_path)
    elif device.device_type == DeviceType.GOOGLE_CAST:
        return await loop.run_in_executor(None, play_on_googlecast, device, audio_path)
    return False
