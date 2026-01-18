"""Telegram bot handlers."""

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .config import ALLOWED_USERS, config
from .models import DeviceType
from .services import cast_connection, play_audio, speak_text_macos
from .tts import expand_variables, text_to_mp3
from .utils import discover_all_devices

logger = logging.getLogger(__name__)


def is_authorized(update: Update) -> bool:
    """Check if user is authorized."""
    user_id = update.effective_user.id if update.effective_user else None
    if user_id not in ALLOWED_USERS:
        logger.warning(f"Unauthorized access attempt from user {user_id}")
        return False
    return True


class ProgressAnimation:
    """Text-based progress animation for Telegram messages."""

    FRAMES = [
        "[ o ] Processing",
        "[ o ] Processing",
        "[ o ] Processing",
        "[ o ] Processing",
    ]

    PLAY_FRAMES = [
        "> Playing  ░░░░░░░░░░",
        "> Playing  ▓░░░░░░░░░",
        "> Playing  ▓▓░░░░░░░░",
        "> Playing  ▓▓▓░░░░░░░",
        "> Playing  ▓▓▓▓░░░░░░",
        "> Playing  ▓▓▓▓▓░░░░░",
        "> Playing  ▓▓▓▓▓▓░░░░",
        "> Playing  ▓▓▓▓▓▓▓░░░",
        "> Playing  ▓▓▓▓▓▓▓▓░░",
        "> Playing  ▓▓▓▓▓▓▓▓▓░",
        "> Playing  ▓▓▓▓▓▓▓▓▓▓",
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
            text = f"{frame}\n\n{self.device_name}"
            try:
                await self.message.edit_text(text)
            except Exception:
                pass  # Ignore edit errors
            idx += 1
            await asyncio.sleep(0.5 if phase == "play" else 0.3)


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
        f"[ o ] Connecting to {device.name}...\n\nThis may wake up the device."
    )

    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, cast_connection.connect, device)

    if success:
        await status_msg.edit_text(
            f"Connected to {device.name}\n\n"
            f"Device is ready. You can now send text or voice messages."
        )
    else:
        await status_msg.edit_text(
            f"Failed to connect to {device.name}\n\n"
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
        f"[ o ] Processing\n\n{config.selected_device.name}"
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
            f"Playback complete\n\n{config.selected_device.name}"
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
        f"[ o ] Processing\n\n{config.selected_device.name}"
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
            f"Playback complete\n\n{config.selected_device.name}"
        )
    else:
        await status_msg.edit_text(f"Playback failed\n\n{config.selected_device.name}")


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
        f"[ o ] Converting to speech\n\n{config.selected_device.name}"
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
        await status_msg.edit_text("TTS conversion failed")
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
            f"Playback complete\n\n{config.selected_device.name}\n{text[:50]}{'...' if len(text) > 50 else ''}"
        )
    else:
        await status_msg.edit_text(f"Playback failed\n\n{config.selected_device.name}")
