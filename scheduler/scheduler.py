# scheduler/scheduler.py
from __future__ import annotations

import asyncio
import random
from datetime import datetime,timedelta
from typing import List, Optional
from managers.config_manager import ConfigManager
from managers.group_manager import GroupManager, GroupInfo
from sender.sender import Sender
from managers.delete_manager import DeleteManager
from utils.logger import setup_logger

logger = setup_logger("scheduler")


class Scheduler:
    """
    Responsible for orchestration (not the low-level send).
    """

    def __init__(self, config: ConfigManager, client, group_manager: GroupManager, sender: Sender, delete_manager: DeleteManager, monitor=None):
        self.config = config.get()
        self.client = client
        self.group_manager = group_manager
        self.sender = sender
        self.delete_manager = delete_manager
        self.monitor = monitor
        # internal
        self._stop = False

    async def run_once(self):
        """
        One run: discover groups, apply filters, compute send list, and iterate sending.
        """
        try:
            await self.group_manager.reload_groups()
            groups = self.group_manager.filter_groups(
                blacklist=self.config.filter.blacklist,
                whitelist=self.config.filter.whitelist,
                skip_channels=self.config.filter.skip_channels,
                skip_no_permission=self.config.filter.skip_no_permission,
            )
            if not groups:
                logger.info("No groups to send to after filtering.")
                return
            # optional shuffle
            if self.config.send.shuffle:
                random.shuffle(groups)
            # daily re-shuffle could be implemented via seed from date

            sent_count = 0
            for idx, g in enumerate(groups, start=1):
                if self._stop:
                    logger.info("Scheduler stopped by request.")
                    break
                logger.info("Sending to %s (%s) [%d/%d]", g.title, g.id, idx, len(groups))
                try:
                    # call sender primitives - here we assume sending the same message; Sender is single-responsibility
                    # message content should be fetched/prepared outside Scheduler in a real system
                    message_text = "@haiwaifwqbot 1"  # placeholder
                    msg = await self.sender.send_message(g.raw, message_text)
                    # schedule delete if configured and group not in no_delete
                    if self.config.send.auto_delete:
                        if g.title not in self.config.delete.no_delete_groups:
                            self.delete_manager.schedule_delete(g.raw, msg.id, self.config.send.delete_after)
                    sent_count += 1
                except Exception as e:
                    logger.exception("Failed to send to %s: %s", g.title, e)
                    # continue to next group
                # rest logic
                if sent_count > 0 and (sent_count % self.config.send.rest_every) == 0:
                    logger.info("Resting for %s seconds after %s sends", self.config.send.rest_seconds, sent_count)
                    await asyncio.sleep(self.config.send.rest_seconds)
                    # After rest period, trigger monitor members check if available.
                    if self.monitor is not None:
                        try:
                            await self.monitor.run_members_check_once()
                        except Exception as e:
                            logger.exception("Monitor members check failed after rest: %s", e)
                else:
                    # random interval between sends
                    interval = random.randint(self.config.send.min_interval, self.config.send.max_interval)
                    await asyncio.sleep(interval)
        except Exception as e:
            logger.exception("Scheduler run_once unexpected error: %s", e)

    async def start(self):
        """
        Scheduler main loop: run_on_start and scheduled times.
        """
        if self.config.scheduler.run_on_start:
            await self.run_once()
        # schedule loop (simple)
        if not self.config.scheduler.enable:
            logger.info("Scheduler disabled in config.")
            return
        while not self._stop:
            # find next time to run
            # simple polling for demo; production use should use apscheduler or crontab
            now = datetime.now()
            # naive sleep until next scheduled time
            # compute seconds until the earliest configured time
            times = []
            for t in self.config.scheduler.schedule_times:
                hh, mm = map(int, t.split(":"))
                run_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if run_dt <= now:
                    run_dt = run_dt + timedelta(days=1)
                times.append(run_dt)
            next_run = min(times)
            wait_seconds = (next_run - now).total_seconds()
            logger.info("Scheduler sleeping until next run at %s", next_run.isoformat())
            # chunked sleep to be responsive
            slept = 0.0
            try:
                while slept < wait_seconds:
                    await asyncio.sleep(min(30.0, wait_seconds - slept))
                    slept += min(30.0, wait_seconds - slept)
                await self.run_once()
            except asyncio.CancelledError:
                logger.info("Scheduler cancelled.")
                break
            except Exception as e:
                logger.exception("Scheduler main loop error: %s", e)
                await asyncio.sleep(5)

    def stop(self):
        self._stop = True
