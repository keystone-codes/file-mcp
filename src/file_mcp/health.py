"""``GET /healthz``. The bucket probe is cached for 15 seconds."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

_INTERVAL_SECONDS = 15.0


class BucketHealth:
    """Cache the last ``head_bucket`` result so probes are not a load test."""

    def __init__(
        self,
        client,
        bucket: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        interval: float = _INTERVAL_SECONDS,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._clock = clock
        self._interval = interval
        self._checked_at: float | None = None
        self._ok = False

    async def reachable(self) -> bool:
        now = self._clock()
        if self._checked_at is not None and (now - self._checked_at) < self._interval:
            return self._ok
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except Exception:
            self._ok = False
        else:
            self._ok = True
        self._checked_at = self._clock()
        return self._ok
