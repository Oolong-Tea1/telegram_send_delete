# managers/delete_manager.py
from __future__ import annotations

import asyncio
from telethon import TelegramClient, errors
from typing import Any, List, Optional
from utils.logger import setup_logger

logger = setup_logger("delete.manager")


class DeleteManager:
    """
    Background delete manager: schedules deletion tasks (non-blocking).
    """

    def __init__(self, client: TelegramClient, no_delete_groups: Optional[List[str]] = None):
        self.client = client
        self.no_delete_groups = set(no_delete_groups or [])

    def schedule_delete(self, chat: Any, message_id: int, delay_seconds: int) -> None:
        """
        Schedule deletion after delay_seconds. This returns immediately.
        """
        asyncio.create_task(self._delete_task(chat, message_id, delay_seconds))

    async def _delete_task(self, chat: Any, message_id: Any, delay_seconds: int) -> None:
        try:
            # check group whitelist
            title = getattr(chat, "title", None) or getattr(chat, "name", None) or ""
            if title in self.no_delete_groups:
                logger.info("No-delete group, skipping delete for %s", title)
                return
            await asyncio.sleep(delay_seconds)
            try:
                # normalize message_id (allow Message object or int)
                mid = message_id.id if hasattr(message_id, "id") else message_id
                # Telethon current API expects message id(s) as positional or message_ids=
                # Pass single id as positional argument
                await self.client.delete_messages(chat, mid)
                logger.info("Deleted message %s in chat %s", mid, title)
            except Exception as e:
                # Telethon may raise RPCError, FloodWaitError, etc.
                logger.warning("Failed to delete message %s in chat %s: %s", message_id, title, e)
        except asyncio.CancelledError:
            logger.info("Delete task cancelled for message %s", message_id)
        except Exception as e:
            logger.exception("Unexpected error in delete task: %s", e)