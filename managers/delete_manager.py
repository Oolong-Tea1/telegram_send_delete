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

    async def _delete_task(self, chat: Any, message_id: int, delay_seconds: int) -> None:
        try:
            # check group whitelist
            title = getattr(chat, "title", None) or getattr(chat, "name", None) or ""
            if title in self.no_delete_groups:
                logger.info("No-delete group, skipping delete for %s", title)
                return
            await asyncio.sleep(delay_seconds)
            try:
                await self.client.delete_messages(chat, ids=message_id)
                logger.info("Deleted message %s in chat %s", message_id, title)
            except errors.RPCError as e:
                logger.warning("Failed to delete message %s in chat %s: %s", message_id, title, e)
        except asyncio.CancelledError:
            logger.info("Delete task cancelled for message %s", message_id)
        except Exception as e:
            logger.exception("Unexpected error in delete task: %s", e)