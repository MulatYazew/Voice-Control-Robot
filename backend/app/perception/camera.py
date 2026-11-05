"""Station camera. A missing, stale or uncalibrated frame means nothing moves.

Sources documented in config/camera.yaml:
  opencv:N  — background thread, latest frame only
  folder:P  — replay images (development / evaluation)
  browser   — frames pushed by the UI (POST /api/camera/frame)
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml

from ..config import ROOT


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    """Ray cast. polygon is [[x, y], ...] in image pixels."""
    if len(polygon) < 3:
        return False
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1):
            inside = not inside
    return inside


class CameraService:
    def __init__(self, camera_cfg: dict[str, Any] | None = None, *, config_path: Path | None = None,
                 now: Callable[[], float] = time.time) -> None:
        self.cfg = dict(camera_cfg or {})
        self.path = config_path or (ROOT / "config" / "camera.yaml")
        self.now = now
        self.available = bool(self.cfg.get("available", False))
        self.source = str(self.cfg.get("source") or "browser")
        self.max_age_s = float(self.cfg.get("max_age_s") or 1.5)
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._ts: float | None = None
        self._bg: np.ndarray | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._folder_i = 0
        if self.available and self.source.startswith("opencv:"):
            self._thread = threading.Thread(target=self._opencv_loop, name="voce-camera", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def calibrated(self) -> bool:
        zones = self.cfg.get("zones_px") or {}
        return all(isinstance(zones.get(z), list) and len(zones[z]) >= 3 for z in ("sinistra", "centro", "destra"))

    def zone_at(self, x: float, y: float) -> str | None:
        if not self.calibrated():
            return None
        zones = self.cfg.get("zones_px") or {}
        for name in ("sinistra", "centro", "destra"):
            if point_in_polygon(x, y, zones[name]):
                return name
        return None

    def push_frame(self, img: np.ndarray, ts: float | None = None) -> None:
        with self._lock:
            self._frame = img
            self._ts = self.now() if ts is None else ts

    def push_background(self, img: np.ndarray) -> None:
        with self._lock:
            self._bg = img.copy()

    def background(self) -> np.ndarray | None:
        with self._lock:
            return None if self._bg is None else self._bg.copy()

    def get_frame(self, max_age_s: float | None = None) -> tuple[np.ndarray, float] | None:
        if not self.available:
            return None
        if self.source.startswith("folder:"):
            self._pull_folder()
        limit = self.max_age_s if max_age_s is None else max_age_s
        with self._lock:
            if self._frame is None or self._ts is None:
                return None
            if self.now() - self._ts > limit:
                return None
            return self._frame.copy(), self._ts

    def save_zones(self, zones: dict[str, list[list[float]]], *, when: str | None = None) -> None:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        cam = data.setdefault("camera", {})
        cam["zones_px"] = zones
        cam["calibrated_at"] = when or time.strftime("%Y-%m-%dT%H:%M:%S")
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.cfg["zones_px"] = zones
        self.cfg["calibrated_at"] = cam["calibrated_at"]

    def _pull_folder(self) -> None:
        folder = Path(self.source.split(":", 1)[1])
        if not folder.is_dir():
            return
        files = sorted([p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}])
        if not files:
            return
        import cv2
        img = cv2.imread(str(files[self._folder_i % len(files)]))
        self._folder_i += 1
        if img is not None:
            self.push_frame(img)

    def _opencv_loop(self) -> None:
        import cv2
        index = int(self.source.split(":", 1)[1] or "0")
        cap = None
        while not self._stop.is_set():
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                cap = cv2.VideoCapture(index)
                if not cap.isOpened():
                    time.sleep(1.0)
                    continue
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                cap = None
                time.sleep(0.5)
                continue
            self.push_frame(frame)
            time.sleep(0.05)
        if cap is not None:
            cap.release()
