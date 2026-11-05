"""Grab side-by-side frames from the ELP stereo camera and split them into left / right."""

from __future__ import annotations

import sys
import threading
import time

import cv2
import numpy as np

# Combined frame = left | right. MJPG is required: the uncompressed mode is 2 fps at these sizes.
RESOLUTIONS = [(3200, 1200), (2560, 720), (1600, 600), (1280, 480)]


def split(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    w = frame.shape[1]
    return frame[:, : w // 2], frame[:, w // 2:]


def _backends() -> list[int]:
    if sys.platform.startswith("win"):
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    return [cv2.CAP_ANY]


def _try_open(index: int, backend: int, size: tuple[int, int] | None):
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    for w, h in ([size] if size else RESOLUTIONS):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        ok, frame = cap.read()
        if ok and frame is not None and frame.shape[1] == w and frame.shape[0] == h:
            return cap
    ok, frame = cap.read()
    if ok and frame is not None and frame.shape[1] >= 2 * frame.shape[0]:
        return cap
    cap.release()
    return None


def open_stereo(index: int | None = None, size: tuple[int, int] | None = None) -> tuple[cv2.VideoCapture, int]:
    """First camera whose frame is at least twice as wide as tall (the stereo one)."""
    for idx in ([index] if index is not None else range(6)):
        for be in _backends():
            cap = _try_open(idx, be, size)
            if cap is not None:
                return cap, idx
    raise RuntimeError("stereo camera not found: check the USB cable, close the Camera app / Teams / Zoom, "
                       "or pass --cam N")


class StereoCamera:
    """Keeps only the newest frame (a background thread drains the driver buffer)."""

    def __init__(self, index: int | None = None, size: tuple[int, int] | None = None) -> None:
        self.cap, self.index = open_stereo(index, size)
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._stamp = 0.0
        self._stop = threading.Event()
        self.fps = 0.0
        self._thread = threading.Thread(target=self._run, name="stereo-grab", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        n, t0 = 0, time.monotonic()
        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue
            with self._lock:
                self._frame, self._stamp = frame, time.monotonic()
            n += 1
            if time.monotonic() - t0 >= 1.0:
                self.fps, n, t0 = n / (time.monotonic() - t0), 0, time.monotonic()

    def read(self, max_age_s: float = 1.0) -> tuple[np.ndarray, np.ndarray] | None:
        with self._lock:
            frame, stamp = self._frame, self._stamp
        if frame is None or time.monotonic() - stamp > max_age_s:
            return None
        return split(frame)

    def wait_first(self, timeout_s: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            pair = self.read()
            if pair is not None:
                return pair
            time.sleep(0.05)
        raise RuntimeError("stereo camera opened but sends no frames")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.cap.release()
