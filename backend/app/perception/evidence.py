"""Short-lived perception evidence. The gate accepts it only if it is fresh and the zone matches."""

from __future__ import annotations

import itertools
import time
from typing import Any, Callable


class EvidenceStore:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl_s: float = 15.0) -> None:
        self.clock = clock
        self.ttl_s = ttl_s
        self._n = itertools.count(1)
        self._items: dict[str, dict[str, Any]] = {}

    def put(self, ev: dict[str, Any]) -> str:
        eid = f"E{next(self._n)}"
        self._items[eid] = {**ev, "id": eid, "t": self.clock()}
        return eid

    def get(self, eid: str | None) -> dict[str, Any] | None:
        if not eid:
            return None
        ev = self._items.get(str(eid))
        if not ev:
            return None
        if self.clock() - ev["t"] > self.ttl_s:
            return None
        return ev
