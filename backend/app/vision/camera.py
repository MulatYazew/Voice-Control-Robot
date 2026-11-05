"""Inspection camera frames.

Sources:
  browser  the operator page streams the laptop webcam (getUserMedia works on http://localhost)
  url      a snapshot URL is polled: Android "IP Webcam" (http://<phone>:8080/shot.jpg) or the
           phone page of the vision service (http://127.0.0.1:8710/api/frame.jpg)
"""

from __future__ import annotations

from grokarm_vision.frames import Frame, FrameStore, UrlPoller

from ..config import Settings


class CameraHub:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.store = FrameStore()
        self.poller: UrlPoller | None = None

    def start(self) -> None:
        if self.s.camera_source == "url" and self.s.camera_url and self.poller is None:
            self.poller = UrlPoller(self.store, self.s.camera_url, 0.5)
            self.poller.start()

    def stop(self) -> None:
        if self.poller:
            self.poller.stop()
            self.poller = None

    def push(self, data: bytes) -> Frame:
        return self.store.put_bytes(data)

    def fresh(self, max_age_s: float) -> Frame | None:
        frame = self.store.latest()
        if frame is None or frame.age() > max_age_s:
            return None
        return frame

    def status(self) -> dict:
        frame = self.store.latest()
        return {
            "source": self.s.camera_source,
            "frame": None if frame is None else {"id": frame.frame_id, "age_s": round(frame.age(), 2),
                                                 "size": list(frame.size)},
            "error": self.poller.last_error if self.poller else "",
        }
