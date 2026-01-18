# Telegram Speaker Bot

A Telegram bot that plays voice messages and text-to-speech on Google Home or macOS.

## Features

- **Voice Messages**: Send voice recordings → plays on your device
- **Text-to-Speech**: Type text → converts to speech and plays
- **Google Cast**: Stream to Google Home, Nest Mini/Hub, Chromecast
- **macOS Local**: Play via `afplay` on your Mac
- **Persistent Connection**: `/connect` keeps device awake for instant playback
- **Progress Animation**: Real-time status updates in Telegram
- **Background Service**: Run as macOS LaunchAgent

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- ffmpeg (`brew install ffmpeg`)
- macOS (for TTS and LaunchAgent)

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/htlin222/telegram-speaker.git
cd telegram-speaker
make setup

# 2. Edit .env with your bot token from @BotFather
# TELEGRAM_BOT_TOKEN=your-token-here

# 3. Run the bot
make run
```

## Bot Commands

| Command    | Description                   |
| ---------- | ----------------------------- |
| `/start`   | Welcome message and help      |
| `/setup`   | Configure playback device     |
| `/connect` | Wake up and connect to device |
| `/status`  | Show current device           |
| `/devices` | List available devices        |
| `/help`    | Show help message             |

## Usage

1. Start the bot: `make run`
2. In Telegram: `/setup` → select your device (e.g., Google Home)
3. `/connect` → wake up the device
4. Send a voice message OR type text → it plays on your device!

### Text-to-Speech

Just type any text message and it will:

1. Convert to speech using macOS `say` (Mei-Jia voice for Chinese)
2. Stream to your selected device

### Progress Animation

```
[ ◐ ] Converting to speech
📍 客廳

▶ Playing  ▓▓▓▓░░░░░░
📍 客廳

✓ Playback complete
📍 客廳
💬 你好世界
```

## Background Service

Run as a macOS LaunchAgent for persistent operation:

```bash
make start      # Install and start service
make stop       # Stop service
make restart    # Restart service
make status     # Check if running
make logs       # View stdout logs
make logs-err   # View error logs
make uninstall  # Remove service
```

## Development

```bash
make install    # Install dependencies
make lint       # Run ruff linter
make format     # Format code with ruff
make clean      # Remove temp files
```

## Configuration

Configuration is stored in `config.yml` after running `/setup`:

```yaml
selected_device:
  id: device-uuid
  name: 客廳
  address: 192.168.1.100
  device_type: googlecast
```

## Supported Devices

- **Google Cast**: Google Home, Nest Mini/Hub, Chromecast
- **macOS Say**: Local playback via `afplay`

## How It Works

1. **Voice Message**: Download OGG → convert to MP3 → stream to device
2. **Text Message**: TTS with `say` → convert to MP3 → stream to device
3. **Google Cast**: Starts local HTTP server, tells device to fetch audio

## License

MIT
