# monitor/monitor.py
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Any, Dict, Set
from telethon import events, functions, types
from utils.logger import setup_logger

logger = setup_logger("monitor")


class Monitor:
    """
    Monitor handles pause control (via control.json) and channel-join logging.

    Behavior additions:
    - On first startup, discovers "owned" broadcast channels and records a snapshot
      of their current member ids to a cache file (owned_members.json).
    - Periodically (check_interval) it re-checks members for owned channels and
      logs any newly added members, then updates the snapshot.

    This design avoids modifying Scheduler: Monitor runs its own background
    periodic checker and will notify (via log) when new members appear since
    the last snapshot.
    """

    def __init__(self, client: Any, control_path: str = "control.json", logs_dir: str = "logs", poll_interval: float = 3.0, check_interval: float = 30.0):
        self.client = client
        self.control_path = control_path
        self.logs_dir = logs_dir
        os.makedirs(self.logs_dir, exist_ok=True)
        self.poll_interval = poll_interval
        # how often to check members for new joins (seconds)
        self.check_interval = check_interval
        # Event that is set when sending is allowed. Start as set (not paused) until control.json says otherwise.
        self.can_send = asyncio.Event()
        self.can_send.set()
        self._control_task: Optional[asyncio.Task] = None
        self._init_task: Optional[asyncio.Task] = None
        self._members_check_task: Optional[asyncio.Task] = None
        self._handler = None
        # log file for channel joins
        self.join_log_path = os.path.join(self.logs_dir, "channel_joins.log")
        # cache of owned channels (ids) created by this session
        self._owned_channel_ids: Set[int] = set()
        self._me_id: Optional[int] = None
        # snapshot file for members
        self._members_snapshot_path = os.path.join(self.logs_dir, "owned_members.json")
        self._members_snapshot: Dict[str, Set[int]] = {}

    def start(self) -> None:
        # register event handler for ChatAction (kept for backward compatibility but not relied upon solely)
        try:
            self._handler = self.client.add_event_handler(self._on_chat_action, events.ChatAction)
        except Exception:
            self.client.add_event_handler(self._on_chat_action, events.ChatAction)
            self._handler = self._on_chat_action
        # start control loop
        self._control_task = asyncio.create_task(self._control_loop())
        # start background initialization to discover owned channels and take initial snapshot
        self._init_task = asyncio.create_task(self._init_and_snapshot())
        # start periodic members check (will wait until init completes)
        self._members_check_task = asyncio.create_task(self._members_check_loop())
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
        if self._members_check_task:
            self._members_check_task.cancel()
            try:
                await self._members_check_task
            except asyncio.CancelledError:
                pass
        # remove event handler if possible
        try:
            self.client.remove_event_handler(self._on_chat_action, events.ChatAction)
        except Exception:
            pass
        logger.info("Monitor stopped")

    async def _control_loop(self) -> None:
        last_seen = None
        while True:
            try:
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
                        if pause:
                            if self.can_send.is_set():
                                self.can_send.clear()
                                logger.info("Sending paused by control.json")
                        else:
                            if not self.can_send.is_set():
                                self.can_send.set()
                                logger.info("Sending resumed by control.json")
                else:
                    if not self.can_send.is_set():
                        self.can_send.set()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                logger.info("Control loop cancelled")
                break
            except Exception as e:
                logger.exception("Unexpected error in control loop: %s", e)
                await asyncio.sleep(self.poll_interval)

    async def _init_and_snapshot(self) -> None:
        try:
            await self._init_owned_channels()
            # after discovery, load or build initial snapshot
            await self._load_or_build_snapshot()
        except asyncio.CancelledError:
            logger.info("Init and snapshot cancelled")
        except Exception as e:
            logger.exception("Init and snapshot failed: %s", e)

    async def _init_owned_channels(self) -> None:
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
                is_channel = getattr(entity, "broadcast", False)
                is_super = getattr(entity, "megagroup", False) if hasattr(entity, "megagroup") else False
                if not is_channel or is_super:
                    continue
                try:
                    res = await self.client(functions.channels.GetParticipantsRequest(channel=entity, filter=types.ChannelParticipantsAdmins(), offset=0, limit=100, hash=0))
                    participants = getattr(res, "participants", [])
                    for p in participants:
                        if isinstance(p, types.ChannelParticipantCreator):
                            if getattr(p, "user_id", None) == self._me_id:
                                owned.add(getattr(entity, "id", None))
                                break
                except Exception as e:
                    logger.debug("Skipping channel %s during owner discovery: %s", getattr(entity, "title", getattr(entity, "id", None)), e)
                # gentle delay to avoid flooding RPC
                await asyncio.sleep(0.5)
            self._owned_channel_ids = {i for i in owned if i is not None}
            logger.info("Discovered %d owned channels", len(self._owned_channel_ids))
        except asyncio.CancelledError:
            logger.info("Owned channels init cancelled")
        except Exception as e:
            logger.exception("Failed to initialize owned channels: %s", e)

    async def _load_or_build_snapshot(self) -> None:
        # Try to load existing snapshot
        if os.path.exists(self._members_snapshot_path):
            try:
                with open(self._members_snapshot_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                # convert lists to sets
                self._members_snapshot = {k: set(v) for k, v in data.items()}
                logger.info("Loaded members snapshot for %d channels", len(self._members_snapshot))
                return
            except Exception as e:
                logger.warning("Failed to load members snapshot, rebuilding: %s", e)
        # Build snapshot for owned channels
        snap: Dict[str, Set[int]] = {}
        for cid in self._owned_channel_ids:
            try:
                ids = await self._fetch_all_member_ids(cid)
                snap[str(cid)] = set(ids)
                logger.info("Snapshot: channel %s has %d members", cid, len(ids))
            except Exception as e:
                logger.debug("Failed to snapshot channel %s: %s", cid, e)
            await asyncio.sleep(0.5)
        self._members_snapshot = snap
        await self._save_snapshot()

    async def _save_snapshot(self) -> None:
        try:
            # convert sets to lists
            data = {k: list(v) for k, v in self._members_snapshot.items()}
            with open(self._members_snapshot_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            logger.info("Saved members snapshot to %s", self._members_snapshot_path)
        except Exception as e:
            logger.exception("Failed to save members snapshot: %s", e)

    async def _fetch_all_member_ids(self, channel_id_or_entity: Any) -> Set[int]:
        ids = set()
        offset = 0
        limit = 200
        # channel can be id or entity
        entity = channel_id_or_entity
        # If an id was provided, leave it; Telethon accepts id via InputPeer/Channel resolution
        while True:
            try:
                res = await self.client(functions.channels.GetParticipantsRequest(channel=entity, filter=types.ChannelParticipantsRecent(), offset=offset, limit=limit, hash=0))
                parts = getattr(res, "participants", [])
                if not parts:
                    break
                for p in parts:
                    # participant types have 'user_id' attribute
                    uid = getattr(p, "user_id", None) or getattr(p, "user_id", None)
                    if uid:
                        ids.add(uid)
                if len(parts) < limit:
                    break
                offset += limit
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.debug("GetParticipants pagination failed for %s: %s", getattr(entity, "id", entity), e)
                break
        return ids

    async def _members_check_loop(self) -> None:
        # wait until initial discovery/snapshot attempted
        while self._init_task and not self._init_task.done():
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                return
        # periodic check
        while True:
            try:
                # for each owned channel, compare current members to snapshot
                for cid in list(self._owned_channel_ids):
                    key = str(cid)
                    try:
                        current_ids = await self._fetch_all_member_ids(cid)
                    except Exception as e:
                        logger.debug("Failed to fetch members for channel %s: %s", cid, e)
                        continue
                    old_ids = self._members_snapshot.get(key, set())
                    new_ids = current_ids - old_ids
                    if new_ids:
                        # For each new id, attempt to resolve username and log a notification entry
                        for uid in new_ids:
                            try:
                                ent = await self.client.get_entity(uid)
                                username = getattr(ent, "username", "") or ""
                            except Exception:
                                username = ""
                            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            # append to join log the same format used elsewhere
                            entry = (
                                "CHANNEL_JOIN\n\n"
                                f"channel={cid}\n\n"
                                f"username={username}\n\n"
                                f"user_id={uid}\n\n"
                                f"time={ts}\n\n"
                            )
                            try:
                                with open(self.join_log_path, "a", encoding="utf-8") as fh:
                                    fh.write(entry)
                                logger.info("Detected new member in channel %s: %s", cid, uid)
                            except Exception as e:
                                logger.exception("Failed to write join log for detected member: %s", e)
                        # update snapshot
                        self._members_snapshot[key] = current_ids
                        await self._save_snapshot()
                    # gentle delay to avoid rate limits
                    await asyncio.sleep(0.3)
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                logger.info("Members check loop cancelled")
                return
            except Exception as e:
                logger.exception("Unexpected error in members check loop: %s", e)
                await asyncio.sleep(self.check_interval)

    async def _on_chat_action(self, event: events.ChatAction.Event) -> None:
        # Keep original event-based logging for immediate cases, but we also rely on snapshot checks
        try:
            is_join = getattr(event, "user_joined", False) or getattr(event, "user_added", False)
            users = []
            if getattr(event, "user", None):
                users = [event.user]
            elif getattr(event, "users", None):
                users = list(event.users)
            if not is_join and not users:
                return
            try:
                chat = await event.get_chat()
            except Exception:
                chat = getattr(event, "chat", None)
            channel_id = getattr(chat, "id", None)
            is_channel = getattr(chat, "broadcast", False)
            is_super = getattr(chat, "megagroup", False) if hasattr(chat, "megagroup") else False
            if not is_channel or is_super:
                return
            # only log if channel is known owned
            if not self._owned_channel_ids:
                return
            if channel_id not in self._owned_channel_ids:
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
                    logger.info("Logged channel join (event): %s -> %s", channel_title, user_id)
                except Exception as e:
                    logger.exception("Failed to write join log: %s", e)
        except Exception as e:
            logger.exception("Error in chat action handler: %s", e)


class PausableSender:
    def __init__(self, sender: Any, monitor: Monitor):
        self._sender = sender
        self._monitor = monitor

    def __getattr__(self, item: str):
        attr = getattr(self._sender, item)
        if item.startswith("send_") and asyncio.iscoroutinefunction(attr):
            async def wrapper(*args, **kwargs):
                await self._monitor.can_send.wait()
                return await attr(*args, **kwargs)
            return wrapper
        return attr

    async def send_message(self, *args, **kwargs):
        await self._monitor.can_send.wait()
        return await self._sender.send_message(*args, **kwargs)


def get_pausable_sender(sender: Any, monitor: Monitor) -> PausableSender:
    return PausableSender(sender, monitor)
