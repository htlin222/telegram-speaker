#!/usr/bin/env python3
"""Telegram Speaker Bot - Play voice messages to Google Home or macOS."""

import asyncio
import logging
import os
import socket
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pychromecast
import yaml
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.yml"
AUDIO_DIR = SCRIPT_DIR / "audio"
AUDIO_DIR.mkdir(exist_ok=True)

# Allowed user IDs (set via env or hardcode)
ALLOWED_USERS = {1212454889}


class DeviceType(Enum):
    GOOGLE_CAST = "googlecast"
    MACOS_SAY = "macos_say"


@dataclass
class Device:
    id: str
    name: str
    address: str | None
    device_type: DeviceType

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "device_type": self.device_type.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        return cls(
            id=data["id"],
            name=data["name"],
            address=data.get("address"),
            device_type=DeviceType(data["device_type"]),
        )


class Config:
    def __init__(self):
        self.selected_device: Device | None = None
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = yaml.safe_load(f) or {}
                if "selected_device" in data and data["selected_device"]:
                    self.selected_device = Device.from_dict(data["selected_device"])

    def save(self):
        data = {
            "selected_device": self.selected_device.to_dict()
            if self.selected_device
            else None
        }
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(data, f, default_flow_style=False)


config = Config()


class CastConnection:
    """Manage persistent connection to Google Cast device."""

    def __init__(self):
        self.cast = None
        self.browser = None
        self.connected = False
        self.device_id: str | None = None

    def connect(self, device: Device, timeout: int = 15) -> bool:
        """Connect to a Google Cast device."""
        import time

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
        # Check if cast is still responsive
        try:
            return self.cast.socket_client.is_connected
        except Exception:
            return False

    def get_cast(self) -> "pychromecast.Chromecast | None":
        """Get the cast device if connected."""
        if self.is_connected():
            return self.cast
        return None


cast_connection = CastConnection()


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


class AudioServer:
    """Simple HTTP server to serve audio files to Chromecast."""

    def __init__(self, directory: Path, port: int = 0):
        self.directory = directory
        self.port = port
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self._original_dir: str | None = None

    def start(self) -> int:
        self._original_dir = os.getcwd()
        os.chdir(self.directory)

        handler = SimpleHTTPRequestHandler
        self.server = HTTPServer(("", self.port), handler)
        self.port = self.server.server_address[1]

        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

        return self.port

    def stop(self):
        if self.server:
            self.server.shutdown()
        if self._original_dir:
            os.chdir(self._original_dir)


def play_on_googlecast(device: Device, audio_path: Path) -> bool:
    """Play audio file on Google Cast device."""
    import time

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
        time.sleep(1)

        # Try to activate with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                mc.block_until_active(timeout=10)
                break
            except Exception as e:
                logger.warning(f"block_until_active attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    mc.play_media(url, "audio/mpeg")
                    time.sleep(1)

        # Check media status
        logger.info(f"Player state: {mc.status.player_state}")
        if mc.status.player_state == "IDLE":
            # Check for errors
            if mc.status.idle_reason:
                logger.error(f"Playback failed, reason: {mc.status.idle_reason}")
                return False
            logger.warning("Player is IDLE, might have finished quickly")

        # Wait for playback to complete with timeout
        timeout = 120  # Max 2 minutes
        start_time = time.time()
        played = False

        while time.time() - start_time < timeout:
            state = mc.status.player_state
            if state == "PLAYING":
                played = True
            elif state == "IDLE" and played:
                # Finished playing
                break
            elif state == "IDLE" and not played:
                # Never started playing, wait a bit more
                if time.time() - start_time > 5:
                    logger.error("Playback never started")
                    return False
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


def get_chinese_time() -> str:
    """Get current time in Chinese format."""
    now = datetime.now()
    hour = now.hour
    minute = now.minute

    # Determine period of day
    if 5 <= hour < 12:
        period = "早上"
    elif 12 <= hour < 13:
        period = "中午"
    elif 13 <= hour < 18:
        period = "下午"
    elif 18 <= hour < 22:
        period = "晚上"
    else:
        period = "深夜"

    # Convert to 12-hour format for display
    display_hour = hour if hour <= 12 else hour - 12
    if display_hour == 0:
        display_hour = 12

    if minute == 0:
        return f"現在時間是{period}{display_hour}點整"
    else:
        return f"現在時間是{period}{display_hour}點{minute}分"


def expand_variables(text: str) -> str:
    """Expand variables like $TIME in text."""
    if "$TIME" in text:
        text = text.replace("$TIME", get_chinese_time())
    return text


def text_to_mp3(text: str, output_path: Path, voice: str = "Mei-Jia") -> bool:
    """Convert text to MP3 using macOS say command.

    Voices: Mei-Jia (Chinese), Samantha (English), etc.
    List voices with: say -v '?'
    """
    aiff_path = output_path.with_suffix(".aiff")
    try:
        logger.info(f"TTS: Converting '{text[:30]}...' with voice {voice}")

        # Generate AIFF with say (rate 150 = slower, default ~175-200)
        result = subprocess.run(
            ["say", "-v", voice, "-r", "150", "-o", str(aiff_path), text],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"say command failed: {result.stderr}")
            return False

        if not aiff_path.exists():
            logger.error("AIFF file not created")
            return False
        logger.info(f"AIFF created: {aiff_path.stat().st_size} bytes")

        # Convert to MP3 with ffmpeg (use full path to avoid alias issues)
        ffmpeg_path = "/opt/homebrew/bin/ffmpeg"
        if not Path(ffmpeg_path).exists():
            ffmpeg_path = "ffmpeg"  # Fallback to PATH

        result = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                str(aiff_path),
                "-acodec",
                "libmp3lame",
                "-b:a",
                "128k",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"ffmpeg failed: {result.stderr}")
            return False

        aiff_path.unlink(missing_ok=True)

        if not output_path.exists():
            logger.error("MP3 file not created")
            return False

        mp3_size = output_path.stat().st_size
        logger.info(f"MP3 created: {mp3_size} bytes")

        if mp3_size < 100:
            logger.error("MP3 file too small")
            return False

        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"TTS conversion failed: {e}")
        aiff_path.unlink(missing_ok=True)
        return False
    except FileNotFoundError as e:
        logger.error(f"Required tool not found: {e}")
        return False


async def play_audio(device: Device, audio_path: Path) -> bool:
    """Play audio on the specified device."""
    loop = asyncio.get_event_loop()
    if device.device_type == DeviceType.MACOS_SAY:
        return await loop.run_in_executor(None, play_on_macos_say, audio_path)
    elif device.device_type == DeviceType.GOOGLE_CAST:
        return await loop.run_in_executor(None, play_on_googlecast, device, audio_path)
    return False


# Telegram Bot Handlers
def is_authorized(update: Update) -> bool:
    """Check if user is authorized."""
    user_id = update.effective_user.id if update.effective_user else None
    if user_id not in ALLOWED_USERS:
        logger.warning(f"Unauthorized access attempt from user {user_id}")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not is_authorized(update):
        return
    welcome_text = (
        "Welcome to Telegram Speaker Bot!\n\n"
        "Send me a voice message or text and I'll play it on your device.\n\n"
        "Commands:\n"
        "/setup - Configure playback device\n"
        "/connect - Wake up and connect to device\n"
        "/status - Show current device\n"
        "/devices - List available devices\n"
        "/help - Show this help message"
    )
    await update.message.reply_text(welcome_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await start(update, context)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command - show current device."""
    if not is_authorized(update):
        return
    if config.selected_device:
        device = config.selected_device
        status_text = (
            f"Current device:\n"
            f"  Name: {device.name}\n"
            f"  Type: {device.device_type.value}\n"
        )
        if device.address:
            status_text += f"  Address: {device.address}\n"
    else:
        status_text = "No device selected. Use /setup to configure."

    await update.message.reply_text(status_text)


async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /connect command - wake up and connect to device."""
    if not is_authorized(update):
        return
    if not config.selected_device:
        await update.message.reply_text("No device configured. Use /setup first.")
        return

    device = config.selected_device

    if device.device_type != DeviceType.GOOGLE_CAST:
        await update.message.reply_text(
            f"Device {device.name} doesn't need connection (local playback)."
        )
        return

    # Check if already connected
    if cast_connection.is_connected() and cast_connection.device_id == device.id:
        await update.message.reply_text(f"Already connected to {device.name}")
        return

    status_msg = await update.message.reply_text(
        f"[ ◐ ] Connecting to {device.name}...\n\nThis may wake up the device."
    )

    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, cast_connection.connect, device)

    if success:
        await status_msg.edit_text(
            f"✓ Connected to {device.name}\n\n"
            f"Device is ready. You can now send text or voice messages."
        )
    else:
        await status_msg.edit_text(
            f"✗ Failed to connect to {device.name}\n\n"
            f"Make sure the device is powered on and on the same network."
        )


async def devices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /devices command - list available devices."""
    if not is_authorized(update):
        return
    await update.message.reply_text("Scanning for devices...")

    loop = asyncio.get_event_loop()
    found_devices = await loop.run_in_executor(None, discover_all_devices, 5)

    if not found_devices:
        await update.message.reply_text("No devices found.")
        return

    text = "Available devices:\n\n"
    for i, device in enumerate(found_devices, 1):
        text += f"{i}. {device.name} ({device.device_type.value})\n"

    await update.message.reply_text(text)


async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setup command - start device selection flow."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "Starting device setup...\n\n"
        "Step 1/3: Scanning for available devices...\n"
        "Please wait (~15 seconds) while I discover devices on your network."
    )

    loop = asyncio.get_event_loop()
    found_devices = await loop.run_in_executor(None, discover_all_devices, 15)

    if not found_devices:
        await update.message.reply_text(
            "No devices found!\n\n"
            "Make sure:\n"
            "- Google Home/Chromecast is on the same network\n"
            "- Or you're running on macOS for 'say' command\n\n"
            "Try /setup again after checking."
        )
        return

    # Store devices in context for callback
    context.user_data["setup_devices"] = found_devices

    # Create keyboard with device options
    keyboard = []
    for device in found_devices:
        button_text = f"{device.name}"
        if device.device_type == DeviceType.GOOGLE_CAST:
            button_text += " (Google)"
        elif device.device_type == DeviceType.MACOS_SAY:
            button_text += " (macOS)"

        keyboard.append(
            [InlineKeyboardButton(button_text, callback_data=f"select_{device.id}")]
        )

    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_setup")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Step 2/3: Select a device\n\n"
        f"Found {len(found_devices)} device(s).\n"
        f"Tap to select your playback device:",
        reply_markup=reply_markup,
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks from inline keyboards."""
    if not is_authorized(update):
        return
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_setup":
        await query.edit_message_text("Setup cancelled.")
        return

    if query.data.startswith("select_"):
        device_id = query.data[7:]  # Remove "select_" prefix

        devices_list = context.user_data.get("setup_devices", [])
        selected = None
        for device in devices_list:
            if device.id == device_id:
                selected = device
                break

        if not selected:
            await query.edit_message_text("Device not found. Please run /setup again.")
            return

        # Save selection
        config.selected_device = selected
        config.save()

        await query.edit_message_text(
            f"Step 3/3: Setup complete!\n\n"
            f"Selected device: {selected.name}\n"
            f"Type: {selected.device_type.value}\n\n"
            f"You can now send voice messages and they will play on this device.\n\n"
            f"Use /status to check current device\n"
            f"Use /setup to change device"
        )

    if query.data == "confirm_test":
        if (
            config.selected_device
            and config.selected_device.device_type == DeviceType.MACOS_SAY
        ):
            await query.edit_message_text("Playing test message...")
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None, speak_text_macos, "Telegram Speaker Bot is ready!"
            )
            if success:
                await query.edit_message_text("Test complete! Setup finished.")
            else:
                await query.edit_message_text(
                    "Test failed. Please check your audio settings."
                )


class ProgressAnimation:
    """Text-based progress animation for Telegram messages."""

    FRAMES = [
        "[ ◐ ] Processing",
        "[ ◓ ] Processing",
        "[ ◑ ] Processing",
        "[ ◒ ] Processing",
    ]

    PLAY_FRAMES = [
        "▶ Playing  ░░░░░░░░░░",
        "▶ Playing  ▓░░░░░░░░░",
        "▶ Playing  ▓▓░░░░░░░░",
        "▶ Playing  ▓▓▓░░░░░░░",
        "▶ Playing  ▓▓▓▓░░░░░░",
        "▶ Playing  ▓▓▓▓▓░░░░░",
        "▶ Playing  ▓▓▓▓▓▓░░░░",
        "▶ Playing  ▓▓▓▓▓▓▓░░░",
        "▶ Playing  ▓▓▓▓▓▓▓▓░░",
        "▶ Playing  ▓▓▓▓▓▓▓▓▓░",
        "▶ Playing  ▓▓▓▓▓▓▓▓▓▓",
    ]

    def __init__(self, message, device_name: str):
        self.message = message
        self.device_name = device_name
        self.running = False
        self.task = None

    async def start(self, phase: str = "process"):
        """Start the animation."""
        self.running = True
        self.task = asyncio.create_task(self._animate(phase))

    async def stop(self):
        """Stop the animation."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def switch_to_playing(self):
        """Switch to playing animation."""
        await self.stop()
        self.running = True
        self.task = asyncio.create_task(self._animate("play"))

    async def _animate(self, phase: str):
        """Run animation loop."""
        frames = self.PLAY_FRAMES if phase == "play" else self.FRAMES
        idx = 0
        while self.running:
            frame = frames[idx % len(frames)]
            text = f"{frame}\n\n📍 {self.device_name}"
            try:
                await self.message.edit_text(text)
            except Exception:
                pass  # Ignore edit errors
            idx += 1
            await asyncio.sleep(0.5 if phase == "play" else 0.3)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages."""
    if not is_authorized(update):
        return
    if not config.selected_device:
        await update.message.reply_text(
            "No device configured. Use /setup to select a playback device."
        )
        return

    # Send initial message and start animation
    status_msg = await update.message.reply_text(
        f"[ ◐ ] Processing\n\n📍 {config.selected_device.name}"
    )
    anim = ProgressAnimation(status_msg, config.selected_device.name)
    await anim.start("process")

    # Download voice message
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    await file.download_to_drive(tmp_path)

    # Convert OGG to MP3 for better compatibility
    mp3_path = tmp_path.with_suffix(".mp3")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp_path),
                "-acodec",
                "libmp3lame",
                str(mp3_path),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg conversion failed: {e}")
        mp3_path = tmp_path
    except FileNotFoundError:
        logger.warning("FFmpeg not found, trying to play OGG directly")
        mp3_path = tmp_path

    # Switch to playing animation
    await anim.switch_to_playing()

    # Play audio
    success = await play_audio(config.selected_device, mp3_path)

    # Stop animation
    await anim.stop()

    # Cleanup
    tmp_path.unlink(missing_ok=True)
    if mp3_path != tmp_path:
        mp3_path.unlink(missing_ok=True)

    if success:
        await status_msg.edit_text(
            f"✓ Playback complete\n\n📍 {config.selected_device.name}"
        )
    else:
        await update.message.reply_text(
            "Playback failed. Check the device connection and try again."
        )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming audio files."""
    if not is_authorized(update):
        return
    if not config.selected_device:
        await update.message.reply_text(
            "No device configured. Use /setup to select a playback device."
        )
        return

    # Send initial message and start animation
    status_msg = await update.message.reply_text(
        f"[ ◐ ] Processing\n\n📍 {config.selected_device.name}"
    )
    anim = ProgressAnimation(status_msg, config.selected_device.name)
    await anim.start("process")

    # Download audio file
    audio = update.message.audio
    file = await context.bot.get_file(audio.file_id)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    await file.download_to_drive(tmp_path)

    # Switch to playing animation
    await anim.switch_to_playing()

    # Play audio
    success = await play_audio(config.selected_device, tmp_path)

    # Stop animation
    await anim.stop()

    # Cleanup
    tmp_path.unlink(missing_ok=True)

    if success:
        await status_msg.edit_text(
            f"✓ Playback complete\n\n📍 {config.selected_device.name}"
        )
    else:
        await status_msg.edit_text(
            f"✗ Playback failed\n\n📍 {config.selected_device.name}"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages - convert to speech and play."""
    if not is_authorized(update):
        return
    if not config.selected_device:
        await update.message.reply_text(
            "No device configured. Use /setup to select a playback device."
        )
        return

    text = update.message.text
    if not text or text.startswith("/"):
        return  # Ignore commands

    # Expand variables like $TIME
    text = expand_variables(text)

    # Send initial message and start animation
    status_msg = await update.message.reply_text(
        f"[ ◐ ] Converting to speech\n\n📍 {config.selected_device.name}"
    )
    anim = ProgressAnimation(status_msg, config.selected_device.name)
    await anim.start("process")

    # Convert text to MP3
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        mp3_path = Path(tmp.name)

    loop = asyncio.get_event_loop()
    tts_success = await loop.run_in_executor(None, text_to_mp3, text, mp3_path)

    if not tts_success:
        await anim.stop()
        await status_msg.edit_text("✗ TTS conversion failed")
        return

    # Switch to playing animation
    await anim.switch_to_playing()

    # Play audio
    success = await play_audio(config.selected_device, mp3_path)

    # Stop animation
    await anim.stop()

    # Cleanup
    mp3_path.unlink(missing_ok=True)

    if success:
        await status_msg.edit_text(
            f"✓ Playback complete\n\n📍 {config.selected_device.name}\n💬 {text[:50]}{'...' if len(text) > 50 else ''}"
        )
    else:
        await status_msg.edit_text(
            f"✗ Playback failed\n\n📍 {config.selected_device.name}"
        )


def main():
    """Run the bot."""
    import signal

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set")
        logger.info("Please set it with: export TELEGRAM_BOT_TOKEN='your-token-here'")
        return

    # Create application
    application = Application.builder().token(token).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("setup", setup))
    application.add_handler(CommandHandler("connect", connect))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("devices", devices))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    # Signal handler for graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal, stopping...")
        os._exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Set up bot command menu
    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "Welcome & help"),
                BotCommand("setup", "Configure playback device"),
                BotCommand("connect", "Wake up & connect to device"),
                BotCommand("status", "Show current device"),
                BotCommand("devices", "List available devices"),
                BotCommand("help", "Show help message"),
            ]
        )

    application.post_init = post_init

    # Run bot with drop_pending_updates to avoid conflicts
    logger.info("Starting Telegram Speaker Bot...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
