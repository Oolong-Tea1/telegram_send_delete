# managers/config_manager.py
from __future__ import annotations

import yaml
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import os


@dataclass
class AccountConfig:
    session: str


@dataclass
class TelethonConfig:
    api_id: int
    api_hash: str


@dataclass
class SendConfig:
    min_interval: int
    max_interval: int
    rest_every: int
    rest_seconds: int
    shuffle: bool
    auto_delete: bool
    delete_after: int
    fetch_latest_each_run: bool
    message_id: Optional[int]


@dataclass
class FilterConfig:
    blacklist: List[str]
    whitelist: List[str]
    skip_channels: bool
    skip_no_permission: bool


@dataclass
class DeleteConfig:
    no_delete_groups: List[str]


@dataclass
class SchedulerConfig:
    enable: bool
    run_on_start: bool
    schedule_times: List[str]


@dataclass
class LoggingConfig:
    level: str
    logs_dir: str


@dataclass
class Config:
    account: AccountConfig
    telethon: TelethonConfig
    send: SendConfig
    filter: FilterConfig
    delete: DeleteConfig
    scheduler: SchedulerConfig
    logging: LoggingConfig


class ConfigManager:
    def __init__(self, path: str = "config/config.yaml"):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        self._data = data or {}
        self.config = self._parse()

    def _parse(self) -> Config:
        acc = self._data.get("account", {})
        tele = self._data.get("telethon", {})
        send = self._data.get("send", {})
        filt = self._data.get("filter", {})
        delete = self._data.get("delete", {})
        sched = self._data.get("scheduler", {})
        log = self._data.get("logging", {})

        # robust parsing with defaults
        account = AccountConfig(session=acc.get("session", "account1"))
        tele_conf = TelethonConfig(api_id=int(tele.get("api_id", 0)), api_hash=str(tele.get("api_hash", "")))
        send_conf = SendConfig(
            min_interval=int(send.get("min_interval", 8)),
            max_interval=int(send.get("max_interval", 15)),
            rest_every=int(send.get("rest_every", 20)),
            rest_seconds=int(send.get("rest_seconds", 300)),
            shuffle=bool(send.get("shuffle", True)),
            auto_delete=bool(send.get("auto_delete", True)),
            delete_after=int(send.get("delete_after", 30)),
            fetch_latest_each_run=bool(send.get("fetch_latest_each_run", True)),
            message_id=send.get("message_id", None),
        )
        filt_conf = FilterConfig(
            blacklist=list(filt.get("blacklist", [])),
            whitelist=list(filt.get("whitelist", [])),
            skip_channels=bool(filt.get("skip_channels", True)),
            skip_no_permission=bool(filt.get("skip_no_permission", True)),
        )
        delete_conf = DeleteConfig(no_delete_groups=list(delete.get("no_delete_groups", [])))
        sched_conf = SchedulerConfig(
            enable=bool(sched.get("enable", True)),
            run_on_start=bool(sched.get("run_on_start", True)),
            schedule_times=list(sched.get("schedule_times", [])),
        )
        log_conf = LoggingConfig(level=str(log.get("level", "INFO")), logs_dir=str(log.get("logs_dir", "logs")))
        return Config(account=account, telethon=tele_conf, send=send_conf, filter=filt_conf, delete=delete_conf, scheduler=sched_conf, logging=log_conf)

    def get(self) -> Config:
        return self.config