# utils/retry.py
from __future__ import annotations

import asyncio
import time
from typing import Callable, Any
import logging

logger = logging.getLogger("retry")


async def retry_async(func: Callable[..., Any], attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, *args, **kwargs):
    """
    Generic async retry helper. Returns result or raises last exception.
    """
    exc = None
    cur_delay = delay
    for i in range(attempts):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            exc = e
            logger.warning("Retry attempt %s failed: %s", i + 1, e)
            if i + 1 < attempts:
                await asyncio.sleep(cur_delay)
                cur_delay *= backoff
    raise exc