# Telegram Speaker Bot - Makefile
# Usage: make <target>

SHELL := /bin/bash

PLIST_NAME := com.telegram-speaker
PLIST_PATH := ~/Library/LaunchAgents/$(PLIST_NAME).plist
PLIST_TEMPLATE := launchagent/$(PLIST_NAME).plist.template
LOG_FILE := /tmp/telegram-speaker.log
ERR_FILE := /tmp/telegram-speaker.err

.PHONY: help install setup run stop start restart status logs logs-out clean uninstall lint format

help:
	@echo "Telegram Speaker Bot"
	@echo ""
	@echo "Quick start:"
	@echo "  make setup      - First-time setup (install deps, create .env)"
	@echo "  make run        - Run bot in foreground"
	@echo ""
	@echo "Background service (macOS LaunchAgent):"
	@echo "  make start      - Install and start background service"
	@echo "  make stop       - Stop background service"
	@echo "  make restart    - Restart background service"
	@echo "  make status     - Check if service is running"
	@echo "  make logs       - Tail service logs"
	@echo "  make logs-out   - Tail stdout (usually empty)"
	@echo "  make uninstall  - Remove background service"
	@echo ""
	@echo "Development:"
	@echo "  make install    - Install dependencies"
	@echo "  make lint       - Run ruff linter"
	@echo "  make format     - Format code with ruff"
	@echo "  make clean      - Remove temp files and logs"

install:
	uv sync

setup: install
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from template"; \
		echo ">>> Edit .env with your TELEGRAM_BOT_TOKEN"; \
	else \
		echo ".env already exists"; \
	fi

run:
	@if [ -f .env ]; then \
		export $$(grep -v '^#' .env | xargs) && uv run python main.py; \
	else \
		echo "Error: .env not found. Run 'make setup' first."; \
		exit 1; \
	fi

lint:
	uv run ruff check .

format:
	uv run ruff format .

start:
	@if [ ! -f .env ]; then \
		echo "Error: .env not found. Run 'make setup' first."; \
		exit 1; \
	fi
	@echo "Installing LaunchAgent..."
	@mkdir -p ~/Library/LaunchAgents
	@export $$(grep -v '^#' .env | xargs) && \
	sed -e "s|UV_PATH|$$(command -v uv)|g" \
	    -e "s|WORKING_DIR|$$(pwd)|g" \
	    -e "s|your-bot-token-here|$${TELEGRAM_BOT_TOKEN}|g" \
	    $(PLIST_TEMPLATE) > $(PLIST_PATH)
	@echo "Created $(PLIST_PATH) with values from .env"
	@launchctl unload $(PLIST_PATH) 2>/dev/null || true
	@launchctl load $(PLIST_PATH)
	@echo "Service started. Check 'make logs' for output."

stop:
	@launchctl unload $(PLIST_PATH) 2>/dev/null || echo "Service not running"
	@echo "Service stopped"

restart:
	@launchctl kickstart -k gui/$$(id -u)/$(PLIST_NAME) 2>/dev/null || \
		(echo "Service not loaded. Run 'make start' first." && exit 1)
	@echo "Service restarted"

status:
	@if launchctl list | grep -q $(PLIST_NAME); then \
		echo "Service: RUNNING"; \
		launchctl list $(PLIST_NAME); \
	else \
		echo "Service: NOT RUNNING"; \
	fi

uninstall: stop
	@rm -f $(PLIST_PATH)
	@echo "Service uninstalled"

logs:
	@if [ -f $(ERR_FILE) ]; then \
		tail -f $(ERR_FILE); \
	else \
		echo "No log file yet. Start the service first."; \
	fi

logs-out:
	@if [ -f $(LOG_FILE) ]; then \
		tail -f $(LOG_FILE); \
	else \
		echo "No stdout log yet."; \
	fi

clean:
	rm -f $(LOG_FILE) $(ERR_FILE) 2>/dev/null || true
	rm -rf __pycache__ .ruff_cache 2>/dev/null || true
	@echo "Cleaned temp files"
