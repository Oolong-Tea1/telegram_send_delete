# Telegram Broadcast Manager (Telethon)

A modular asyncio-based Telegram broadcast system using Telethon user sessions.

Requirements
- Python 3.11+
- Telethon
- PyYAML

Quickstart
1. Create a virtual environment and install requirements:
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

2. Configure config/config.yaml (API_ID, API_HASH, send options, filters, delete rules).

3. Place your Telethon session files under `accounts/` (e.g., account1.session). If no session file, the program will prompt for phone/code for interactive login.

4. Run:
   python main.py

Project layout
- main.py: application entrypoint
- core/telegram.py: ClientManager (session loading, reconnect)
- managers/config_manager.py: YAML config loader
- managers/group_manager.py: group discovery and filters
- managers/delete_manager.py: async delete tasks
- scheduler/scheduler.py: sending scheduler
- sender/sender.py: message/file sending primitives
- utils/logger.py: logging setup (daily rotated logs)
- utils/retry.py: retry helpers and policies

Design principles
- Modular, single-responsibility classes
- Asyncio-based non-blocking design
- YAML configuration handled via ConfigManager
- Robust error handling and logging