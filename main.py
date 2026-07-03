# main.py
from __future__ import annotations

import asyncio
from managers.config_manager import ConfigManager
from utils.logger import setup_logger
from core.telegram import ClientManager
from managers.group_manager import GroupManager
from sender.sender import Sender
from managers.delete_manager import DeleteManager
from scheduler.scheduler import Scheduler
import os

async def main():
    # Load config
    cfg_mgr = ConfigManager("config/config.yaml")
    cfg = cfg_mgr.get()

    # Setup logging with configured log dir/level
    logger = setup_logger("tbm", level=cfg.logging.level, log_dir=cfg.logging.logs_dir)
    logger.info("Starting Telegram Broadcast Manager")

    # Ensure accounts dir exists
    os.makedirs("accounts", exist_ok=True)

    # Client manager
    client_mgr = ClientManager(accounts_dir="accounts", api_id=cfg.telethon.api_id, api_hash=cfg.telethon.api_hash)

    # Primary session name from config
    primary_session = cfg.account.session
    client = await client_mgr.get_primary_client(primary_session)
    if client is None:
        logger.error("No client available for session %s. Exiting.", primary_session)
        return

    # Initialize managers
    group_mgr = GroupManager(client)
    sender = Sender(client)
    delete_mgr = DeleteManager(client, no_delete_groups=cfg.delete.no_delete_groups)

    scheduler = Scheduler(cfg_mgr, client, group_mgr, sender, delete_mgr)

    try:
        await scheduler.start()
    finally:
        logger.info("Shutting down and closing clients")
        await client_mgr.close_all()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user.")