#!/usr/bin/env python3
"""Telegram Speaker Bot - Entry point."""

import logging
import os
import signal

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from modules.handlers import (
    button_callback,
    connect,
    devices,
    handle_audio,
    handle_text,
    handle_voice,
    help_command,
    setup,
    start,
    status,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    """Run the bot."""
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
