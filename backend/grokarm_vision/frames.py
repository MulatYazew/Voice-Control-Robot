from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import httpx
import numpy as np

MAX_FRAME_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Frame:
    frame_id: int
    image: np.ndarray
    jpeg: bytes
    received_at: float

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.image.shape[:2]
        return w, h

    def age(self) -> float:
        return time.monotonic() - self.received_at


def decode_image(data: bytes) -> np.ndarray:
    if not data or len(data) > MAX_FRAME_BYTES:
        raise ValueError("empty or oversized image")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("not a decodable image")
    return image


def encode_jpeg(image: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("jpeg encoding failed")
    return buf.tobytes()


class FrameStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._next_id = 1

    def put_bytes(self, data: bytes) -> Frame:
        return self._store(decode_image(data), data)

    def put_image(self, image: np.ndarray) -> Frame:
        return self._store(image, encode_jpeg(image))

    def _store(self, image: np.ndarray, jpeg: bytes) -> Frame:
        with self._lock:
            frame = Frame(self._next_id, image, jpeg, time.monotonic())
            self._next_id += 1
            self._latest = frame
            return frame

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest


class UrlPoller:
    """Pulls snapshots from a phone app such as Android "IP Webcam" (`/shot.jpg`)."""

    def __init__(self, store: FrameStore, base_url: str, interval_s: float) -> None:
        self.store = store
        self.url = base_url if base_url.endswith(".jpg") else f"{base_url}/shot.jpg"
        self.interval_s = interval_s
        self.last_error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera-poller", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        with httpx.Client(timeout=3.0) as client:
            while not self._stop.is_set():
                try:
                    response = client.get(self.url)
                    response.raise_for_status()
                    self.store.put_bytes(response.content)
                    self.last_error = ""
                except Exception as exc:  # network hiccups must not kill the poller
                    self.last_error = f"{type(exc).__name__}: {exc}"
                self._stop.wait(self.interval_s)
