# core/telegram.py
from __future__ import annotations

import asyncio
from telethon import TelegramClient, errors
from typing import Dict, Optional, List
import os
from utils.logger import setup_logger

logger = setup_logger("core.telegram")


class ClientManager:
    """
    Manage Telethon clients (user sessions) stored under accounts/ directory.
    Designed for one primary session, but structured to support multiple sessions later.
    """

    def __init__(self, accounts_dir: str = "accounts", api_id: int = 0, api_hash: str = ""):
        self.accounts_dir = accounts_dir
        os.makedirs(self.accounts_dir, exist_ok=True)
        self.api_id = api_id
        self.api_hash = api_hash
        self._clients: Dict[str, TelegramClient] = {}

    def discover_sessions(self) -> List[str]:
        names = []
        for fn in os.listdir(self.accounts_dir):
            base, ext = os.path.splitext(fn)
            if ext == ".session":
                names.append(base)
        return sorted(names)

    def get_primary_session_path(self, base_name: str) -> str:
        return os.path.join(self.accounts_dir, base_name)

    async def get_client(self, base_name: str, start_if_needed: bool = True) -> Optional[TelegramClient]:
        """
        Return a connected TelegramClient for the given session base name.
        If not connected, attempt to connect or start interactively.
        """
        if base_name in self._clients:
            client = self._clients[base_name]
            if not client.is_connected():
                try:
                    await client.connect()
                except Exception as e:
                    logger.exception("Failed to connect existing client %s: %s", base_name, e)
            return client
        # create new client
        session_path = self.get_primary_session_path(base_name)
        client = TelegramClient(session_path, self.api_id, self.api_hash)
        self._clients[base_name] = client
        if start_if_needed:
            try:
                await client.connect()
            except Exception as e:
                logger.exception("Failed to connect client %s: %s", base_name, e)
                # do not raise; return client object for possible interactive login later
        return client

    async def get_primary_client(self, base_name: str) -> Optional[TelegramClient]:
        return await self.get_client(base_name)

    async def ensure_connected(self, base_name: str) -> Optional[TelegramClient]:
        client = await self.get_client(base_name)
        if client is None:
            return None
        if not client.is_connected():
            try:
                await client.connect()
            except Exception as e:
                logger.exception("ensure_connected failed for %s: %s", base_name, e)
        return client

    async def list_active_clients(self) -> List[str]:
        return list(self._clients.keys())

    async def close_all(self) -> None:
        for name, c in list(self._clients.items()):
            try:
                await c.disconnect()
            except Exception:
                pass
        self._clients.clear()