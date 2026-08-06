# monitor/monitor.py
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Any
from telethon import events
from utils.logger import setup_logger

logger = setup_logger("monitor")


class Monitor:
    """
    Monitor handles pause control (via control.json) and channel-join logging.

    Usage:
      monitor = Monitor(client, logs_dir="logs")
      monitor.start()

    It exposes an asyncio.Event `can_send` which is set when sending is allowed.
    A PausableSender wraps an existing Sender and awaits this event before delegating.
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
        self._handler = None
        # log file for channel joins
        self.join_log_path = os.path.join(self.logs_dir, "channel_joins.log")

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
        logger.info("Monitor started: control=%s join_log=%s", self.control_path, self.join_log_path)

    async def stop(self) -> None:
        if self._control_task:
            self._control_task.cancel()
            try:
                await self._control_task
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

    async def _on_chat_action(self, event: events.ChatAction.Event) -> None:
        """Handle new users joining a channel/group. Write a simple log entry per join."""
        try:
            # Only consider join/add events
            # Telethon's ChatAction has attributes: user_joined, user_added, users
            is_join = getattr(event, "user_joined", False) or getattr(event, "user_added", False)
            users = []
            if getattr(event, "user", None):
                users = [event.user]
            elif getattr(event, "users", None):
                users = list(event.users)
            # if no explicit flags, still treat presence of users as join
            if not is_join and not users:
                return
            # determine chat title
            try:
                chat = await event.get_chat()
            except Exception:
                chat = getattr(event, "chat", None)
            channel_title = getattr(chat, "title", None) or getattr(chat, "name", None) or str(getattr(chat, "id", ""))

            for u in users:
                username = getattr(u, "username", "") or ""
                user_id = getattr(u, "id", "")
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Write the fixed-format block. Use blank lines between lines to match example.
                entry = (
                    "CHANNEL_JOIN\n\n"
                    f"channel={channel_title}\n\n"
                    f"username={username}\n\n"
                    f"user_id={user_id}\n\n"
                    f"time={ts}\n\n"
                )
                # append to log file
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
