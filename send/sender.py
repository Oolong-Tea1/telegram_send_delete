# sender/sender.py
from __future__ import annotations

from telethon import TelegramClient
from telethon.tl.types import Message
from typing import Optional, Any
from utils.logger import setup_logger

logger = setup_logger("sender")


class Sender:
    """
    Sender exposes low-level sending primitives without sleeps or scheduling.
    Single responsibility: do not sleep, do not retry, just call Telethon API.
    """

    def __init__(self, client: TelegramClient):
        self.client = client

    async def send_message(self, entity: Any, text: str, **kwargs) -> Message:
        """
        Send a text message.
        Returns Message object on success.
        """
        msg = await self.client.send_message(entity, text, **kwargs)
        logger.info("send_message success to %s msg_id=%s", getattr(entity, "id", str(entity)), getattr(msg, "id", None))
        return msg

    async def send_file(self, entity: Any, file: Any, caption: Optional[str] = None, **kwargs) -> Message:
        """
        Send a file/photo/video/document. `file` can be a media object (Message.media) or path.
        """
        msg = await self.client.send_file(entity, file, caption=caption or "", **kwargs)
        logger.info("send_file success to %s msg_id=%s", getattr(entity, "id", str(entity)), getattr(msg, "id", None))
        return msg

    async def send_photo(self, entity: Any, photo: Any, caption: Optional[str] = None, **kwargs) -> Message:
        return await self.send_file(entity, photo, caption=caption, **kwargs)

    async def send_document(self, entity: Any, doc: Any, caption: Optional[str] = None, **kwargs) -> Message:
        return await self.send_file(entity, doc, caption=caption, **kwargs)