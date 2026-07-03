# utils/logger.py
from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

DEFAULT_LOG_DIR = "logs"


def setup_logger(name: str = "tbm", level: str = "INFO", log_dir: Optional[str] = None) -> logging.Logger:
    """
    Setup a logger with console handler and daily rotating file handler.
    """
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s - %(name)s - %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(ch)

    # Daily rotating file handler
    fh = TimedRotatingFileHandler(filename=os.path.join(log_dir, f"{name}.log"), when="midnight", backupCount=30, encoding="utf-8")
    fh.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)

    return logger