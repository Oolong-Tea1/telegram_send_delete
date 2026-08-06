# monitor/monitor.py
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Any
from telethon import events, functions, types
from utils.logger import setup_logger

logger = setup_logger("monitor")


class Monitor:
    """
    Monitor handles pause control (via control.json) and channel-join logging.

    It only logs joins for channels that were created by the current session (the logged-in account).
    """

    def __init__(self, client: Any, control_path: str = "control.json", logs_dir: str = "logs", poll_interval: float = 3.0):
        self.client = client
        self.control_path = control_path
        self.logs_dir = logs_dir
        os.makedirs(self.logs_dir, exist_ok=True)
        self.poll_interval = poll_interval
        # Event that is set when sending is allowed. Start as set (not paused) until control.json says otherwise.
        self.can_send = asyncio.Event()
        self.can_send.set()
        self._control_task: Optional[asyncio.Task] = None
        self._init_task: Optional[asyncio.Task] = None
        self._handler = None
        # log file for channel joins
        self.join_log_path = os.path.join(self.logs_dir, "channel_joins.log")
        # tracked owned channels (ids) created by this session
        self._owned_channel_ids: set[int] = set()
        self._me_id: Optional[int] = None

    def start(self) -> None:
        # register event handler for ChatAction
        try:
            self._handler = self.client.add_event_handler(self._on_chat_action, events.ChatAction)
        except Exception:
            # older telethon versions accept add_event_handler returning None; we'll still keep reference
            self.client.add_event_handler(self._on_chat_action, events.ChatAction)
            self._handler = self._on_chat_action
        # start control loop
        self._control_task = asyncio.create_task(self._control_loop())
        # start background initialization to discover owned channels
        self._init_task = asyncio.create_task(self._init_owned_channels())
        logger.info("Monitor started: control=%s join_log=%s", self.control_path, self.join_log_path)

    async def stop(self) -> None:
        if self._control_task:
            self._control_task.cancel()
            try:
                await self._control_task
            except asyncio.CancelledError:
                pass
        if self._init_task:
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
        # remove event handler if possible
        try:
            self.client.remove_event_handler(self._on_chat_action, events.ChatAction)
        except Exception:
            # best-effort
            pass
        logger.info("Monitor stopped")

    async def _control_loop(self) -> None:
        """Periodic control.json poller. Updates self.can_send based on control.json pause flag."""
        last_seen = None
        while True:
            try:
                # Read file if exists
                if os.path.exists(self.control_path):
                    stat = os.stat(self.control_path)
                    mtime = stat.st_mtime
                    if last_seen is None or mtime != last_seen:
                        last_seen = mtime
                        try:
                            with open(self.control_path, "r", encoding="utf-8") as fh:
                                data = json.load(fh)
                            pause = bool(data.get("pause", False))
                        except Exception as e:
                            logger.warning("Failed to read control.json: %s", e)
                            pause = False
                        # apply
                        if pause:
                            if self.can_send.is_set():
                                self.can_send.clear()
                                logger.info("Sending paused by control.json")
                        else:
                            if not self.can_send.is_set():
                                self.can_send.set()
                                logger.info("Sending resumed by control.json")
                else:
                    # no control file: ensure sending allowed
                    if not self.can_send.is_set():
                        self.can_send.set()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                logger.info("Control loop cancelled")
                break
            except Exception as e:
                logger.exception("Unexpected error in control loop: %s", e)
                await asyncio.sleep(self.poll_interval)

    async def _init_owned_channels(self) -> None:
        """Discover channels that were created by the current session account.

        This task runs once at startup and populates self._owned_channel_ids. It uses
        channels.GetParticipantsRequest to inspect participant types and finds
        ChannelParticipantCreator entries that match the current account id.
        """
        try:
            me = await self.client.get_me()
            if me is None:
                logger.warning("Unable to get current user (get_me returned None)")
                return
            self._me_id = getattr(me, "id", None)
            dialogs = await self.client.get_dialogs()
            owned = set()
            for d in dialogs:
                entity = d.entity
                # consider only broadcast channels (not megagroups / supergroups)
                is_channel = getattr(entity, "broadcast", False)
                is_super = getattr(entity, "megagroup", False) if hasattr(entity, "megagroup") else False
                if not is_channel or is_super:
                    continue
                try:
                    # Use low-level GetParticipantsRequest to get participant objects (creator/admin types)
                    res = await self.client(functions.channels.GetParticipantsRequest(channel=entity, filter=types.ChannelParticipantsAdmins(), offset=0, limit=100, hash=0))
                    participants = getattr(res, "participants", [])
                    for p in participants:
                        # check for ChannelParticipantCreator type
                        if isinstance(p, types.ChannelParticipantCreator):
                            if getattr(p, "user_id", None) == self._me_id:
                                owned.add(getattr(entity, "id", None))
                                break
                except Exception as e:
                    # ignore channels we cannot inspect
                    logger.debug("Skipping channel %s during owner discovery: %s", getattr(entity, "title", getattr(entity, "id", None)), e)
            self._owned_channel_ids = {i for i in owned if i is not None}
            logger.info("Discovered %d owned channels", len(self._owned_channel_ids))
        except asyncio.CancelledError:
            logger.info("Owned channels init cancelled")
        except Exception as e:
            logger.exception("Failed to initialize owned channels: %s", e)

    async def _on_chat_action(self, event: events.ChatAction.Event) -> None:
        """Handle new users joining a channel/group. Write a simple log entry per join.

        Only logs when the chat is a channel created by this session's account.
        """
        try:
            # Only consider join/add events
            is_join = getattr(event, "user_joined", False) or getattr(event, "user_added", False)
            users = []
            if getattr(event, "user", None):
                users = [event.user]
            elif getattr(event, "users", None):
                users = list(event.users)
            # if no explicit flags, still treat presence of users as join
            if not is_join and not users:
                return
            # determine chat entity and id
            try:
                chat = await event.get_chat()
            except Exception:
                chat = getattr(event, "chat", None)
            channel_id = getattr(chat, "id", None)
            # ensure it's a broadcast channel (not a supergroup) and owned by this session
            is_channel = getattr(chat, "broadcast", False)
            is_super = getattr(chat, "megagroup", False) if hasattr(chat, "megagroup") else False
            if not is_channel or is_super:
                return
            # if owned channels not yet discovered, skip until discovery finishes
            if self._owned_channel_ids and channel_id not in self._owned_channel_ids:
                return
            if not self._owned_channel_ids:
                # still initializing: skip to avoid logging non-owned channels
                return

            channel_title = getattr(chat, "title", None) or getattr(chat, "name", None) or str(channel_id)

            for u in users:
                username = getattr(u, "username", "") or ""
                user_id = getattr(u, "id", "")
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry = (
                    "CHANNEL_JOIN\n\n"
                    f"channel={channel_title}\n\n"
                    f"username={username}\n\n"
                    f"user_id={user_id}\n\n"
                    f"time={ts}\n\n"
                )
                try:
                    with open(self.join_log_path, "a", encoding="utf-8") as fh:
                        fh.write(entry)
                    logger.info("Logged channel join: %s -> %s", channel_title, user_id)
                except Exception as e:
                    logger.exception("Failed to write join log: %s", e)
        except Exception as e:
            logger.exception("Error in chat action handler: %s", e)


class PausableSender:
    """Wraps an existing Sender and waits on monitor.can_send before delegating send methods."""

    def __init__(self, sender: Any, monitor: Monitor):
        self._sender = sender
        self._monitor = monitor

    def __getattr__(self, item: str):
        # Provide access to underlying attributes/methods. For send_* methods we wrap.
        attr = getattr(self._sender, item)
        if item.startswith("send_") and asyncio.iscoroutinefunction(attr):
            async def wrapper(*args, **kwargs):
                # wait until allowed
                await self._monitor.can_send.wait()
                return await attr(*args, **kwargs)
            return wrapper
        return attr

    # explicit convenience for send_message if underlying is not coroutinefunction
    async def send_message(self, *args, **kwargs):
        await self._monitor.can_send.wait()
        return await self._sender.send_message(*args, **kwargs)


def get_pausable_sender(sender: Any, monitor: Monitor) -> PausableSender:
    return PausableSender(sender, monitor)
