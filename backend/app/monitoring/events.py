"""Event bus (UI broadcast), action history, latency metrics, structured logging."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..config import LOG_DIR

_SECRET_RE = re.compile(r"xai-[A-Za-z0-9]{8,}")


class _JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": round(r.created, 3), "level": r.levelname, "logger": r.name, "msg": r.getMessage()}
        if hasattr(r, "data"):
            d["data"] = r.data
        return _SECRET_RE.sub("xai-***", json.dumps(d, ensure_ascii=False, default=str))


def setup_logging(log_dir: Path = LOG_DIR, level: int = logging.INFO) -> None:
    import os
    if os.getenv("PYTEST_CURRENT_TEST"):   # tests must not write into the real operator logs
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("voce")
    if any(getattr(h, "_voce", False) for h in root.handlers):
        return
    fh = logging.FileHandler(log_dir / "voce.jsonl", encoding="utf-8")
    fh.setFormatter(_JsonFormatter())
    fh._voce = True  # type: ignore[attr-defined]
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    sh._voce = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(level)
    root.propagate = False


def log_event(logger: logging.Logger, msg: str, **data: Any) -> None:
    logger.info(msg, extra={"data": data})


class EventBus:
    """Fan-out of JSON events to every /ws/events subscriber (and tests)."""

    def __init__(self, maxlen: int = 300) -> None:
        self._subs: set[asyncio.Queue] = set()
        self.recent: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, type_: str, **data: Any) -> dict[str, Any]:
        ev = {"type": type_, "ts": time.time(), **data}
        if type_ not in ("joints", "audio_level"):
            self.recent.append(ev)
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return ev


class Metrics:
    """Latency samples per metric name; persisted to logs/metrics.jsonl."""

    def __init__(self, path: Path | None = LOG_DIR / "metrics.jsonl") -> None:
        import os
        self.samples: dict[str, list[float]] = {}
        self.path = None if os.getenv("PYTEST_CURRENT_TEST") else path

    def record(self, name: str, ms: float, **ctx: Any) -> None:
        if ms is None:
            return
        self.samples.setdefault(name, []).append(float(ms))
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(json.dumps({"ts": time.time(), "metric": name, "ms": round(ms, 1), **ctx}, default=str) + "\n")
            except OSError:
                pass

    def summary(self) -> dict[str, Any]:
        return {k: {"n": len(v), "median_ms": round(statistics.median(v), 1), "max_ms": round(max(v), 1),
                    "last_ms": round(v[-1], 1)} for k, v in self.samples.items() if v}


class History:
    def __init__(self, maxlen: int = 100) -> None:
        self.items: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def add(self, **item: Any) -> dict[str, Any]:
        it = {"ts": time.time(), **item}
        self.items.appendleft(it)
        return it

    def list(self) -> list[dict[str, Any]]:
        return list(self.items)
