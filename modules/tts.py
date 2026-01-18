"""Text-to-speech conversion."""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


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
