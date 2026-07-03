# managers/group_manager.py
from __future__ import annotations

import asyncio
from telethon import TelegramClient, errors
from telethon.tl.types import Dialog, Channel, Chat
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from utils.logger import setup_logger

logger = setup_logger("group.manager")


@dataclass
class GroupInfo:
    id: int
    title: str
    username: Optional[str]
    is_channel: bool
    is_group: bool
    is_supergroup: bool
    is_admin: bool
    can_send_messages: bool
    raw: Any


class GroupManager:
    """
    Discover and manage groups/channels for a given Telethon client.
    """

    def __init__(self, client: TelegramClient):
        self.client = client
        self.groups: Dict[int, GroupInfo] = {}

    async def reload_groups(self) -> None:
        """
        Load dialogs and build group index.
        """
        self.groups.clear()
        try:
            dialogs = await self.client.get_dialogs()
            for d in dialogs:
                entity = d.entity
                # consider Channel and Chat
                if isinstance(entity, Channel) or isinstance(entity, Chat):
                    gid = getattr(entity, "id", None)
                    title = getattr(entity, "title", "") or getattr(entity, "name", "")
                    username = getattr(entity, "username", None)
                    is_channel = getattr(entity, "broadcast", False)
                    is_group = not is_channel
                    is_super = getattr(entity, "megagroup", False) if isinstance(entity, Channel) else True
                    # admin info requires get_permissions or get_participant; simplified assumption here
                    # Check if we can send messages: check 'default_banned_rights' or try to call
                    can_send = True
                    is_admin = False
                    try:
                        # Attempt to use get_permissions or check participant; for simplicity, we attempt to get me's participant and infer admin
                        me_part = await self.client.get_permissions(entity, "me")
                        # if no error, we have some access; check if can_send
                        # permission fields differ; for safety, set True
                    except errors.RPCError:
                        pass
                    info = GroupInfo(
                        id=gid,
                        title=title,
                        username=username,
                        is_channel=is_channel,
                        is_group=is_group,
                        is_supergroup=is_super,
                        is_admin=is_admin,
                        can_send_messages=can_send,
                        raw=entity,
                    )
                    self.groups[gid] = info
            logger.info("Loaded %d groups/channels", len(self.groups))
        except Exception as e:
            logger.exception("Failed to reload groups: %s", e)

    def get_all_groups(self) -> List[GroupInfo]:
        return list(self.groups.values())

    def get_group_by_id(self, id_: int) -> Optional[GroupInfo]:
        return self.groups.get(id_)

    def get_group_by_name(self, name: str) -> Optional[GroupInfo]:
        for g in self.groups.values():
            if g.title == name or (g.username and g.username == name):
                return g
        return None

    def filter_groups(self, blacklist: List[str], whitelist: List[str], skip_channels: bool, skip_no_permission: bool) -> List[GroupInfo]:
        """
        Apply filters:
        - remove groups in blacklist
        - if whitelist not empty, only keep groups in whitelist
        - skip channels if skip_channels
        - skip groups where can_send_messages is False (if skip_no_permission)
        """
        out = []
        for g in self.groups.values():
            if blacklist and g.title in blacklist:
                continue
            if whitelist and g.title not in whitelist and (g.username not in whitelist):
                continue
            if skip_channels and g.is_channel:
                continue
            if skip_no_permission and not g.can_send_messages:
                continue
            out.append(g)
        return out