"""Small HTTP service around the stereo camera (stdlib only, no extra dependencies)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

from .calib import Rectifier, StereoCalibration
from .capture import StereoCamera
from .depth import DepthEngine, DepthError, colorize


class DepthService:
    def __init__(self, camera: StereoCamera, cal: StereoCalibration, marker_size_m: float = 0.05,
                 scale: float = 0.5, num_disp: int = 128) -> None:
        self.camera = camera
        self.rect = Rectifier(cal)
        self.engine = DepthEngine(self.rect.K, self.rect.baseline_m, scale=scale, num_disp=num_disp)
        self.marker_size_m = marker_size_m
        self._lock = threading.Lock()
        self._cache: tuple[float, np.ndarray, np.ndarray] | None = None   # (time, left_rect, disparity)

    # ------------------------------------------------------------------ frames ---
    def rectified(self) -> tuple[np.ndarray, np.ndarray]:
        pair = self.camera.read()
        if pair is None:
            raise DepthError("no fresh frame from the stereo camera")
        return self.rect.rectify(*pair)

    def frame_jpeg(self, quality: int = 85) -> bytes:
        left, _ = self.rectified()
        return cv2.imencode(".jpg", left, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()

    def disparity(self, max_age_s: float = 0.3) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < max_age_s:
                return self._cache[1], self._cache[2]
            left, right = self.rectified()
            disp = self.engine.disparity(left, right)
            self._cache = (time.monotonic(), left, disp)
            return left, disp

    def depth_jpeg(self) -> bytes:
        left, disp = self.disparity()
        img = colorize(disp, width=left.shape[1])
        return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()

    # ------------------------------------------------------------------- queries ---
    def status(self) -> dict:
        w, h = self.rect.cal.size
        return {"ok": True, "frame_size": [w, h], "intrinsics": self.rect.intrinsics(),
                "baseline_mm": round(self.rect.baseline_m * 1000, 1), "calib_rms": round(self.rect.cal.rms, 3),
                "camera_index": self.camera.index, "fps": round(self.camera.fps, 1),
                "marker_size_m": self.marker_size_m}

    def plane(self, left: np.ndarray, disp: np.ndarray, exclude_box=None):
        plane = self.engine.plane_from_marker(left, self.marker_size_m)
        if plane is not None:
            return plane
        pts, px = self.engine.points(disp)
        if exclude_box is not None:
            x0, y0, w, h = exclude_box
            keep = ~((px[:, 0] >= x0 - w) & (px[:, 0] <= x0 + 2 * w) & (px[:, 1] >= y0 - h) & (px[:, 1] <= y0 + 2 * h))
            pts = pts[keep]
        if len(pts) > 20000:
            pts = pts[np.random.default_rng(0).choice(len(pts), 20000, replace=False)]
        return self.engine.plane_ransac(pts)

    def point(self, u: float, v: float) -> dict:
        left, disp = self.disparity()
        p = self.engine.point(disp, u, v)
        out = {"ok": True, "xyz_m": [round(float(c), 4) for c in p], "distance_m": round(float(np.linalg.norm(p)), 4),
               "frame_size": [left.shape[1], left.shape[0]]}
        try:
            plane = self.plane(left, disp)
            out["height_m"] = round(float(plane.height(p[None])[0]), 4)
            out["plane"] = plane.source
        except DepthError:
            pass
        return out

    def height(self, x0: float, y0: float, w: float, h: float) -> dict:
        if w <= 2 or h <= 2:
            raise DepthError("box too small")
        left, disp = self.disparity()
        plane = self.plane(left, disp, exclude_box=(x0, y0, w, h))
        res = self.engine.height_in_box(disp, plane, (x0, y0, w, h))
        return {"ok": True, **res, "frame_size": [left.shape[1], left.shape[0]]}


def serve(service: DepthService, host: str = "127.0.0.1", port: int = 8720) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/frame.jpg", "/shot.jpg"):
                    return self._send(200, service.frame_jpeg(), "image/jpeg")
                if url.path == "/depth.jpg":
                    return self._send(200, service.depth_jpeg(), "image/jpeg")
                if url.path == "/api/status":
                    return self._json(200, service.status())
                if url.path == "/api/point":
                    return self._json(200, service.point(float(q["u"]), float(q["v"])))
                if url.path == "/api/height":
                    return self._json(200, service.height(float(q["x0"]), float(q["y0"]), float(q["w"]), float(q["h"])))
                if url.path == "/":
                    return self._send(200, INDEX.encode(), "text/html; charset=utf-8")
                return self._json(404, {"ok": False, "error": "not found"})
            except (KeyError, ValueError) as e:
                return self._json(400 if not isinstance(e, DepthError) else 200,
                                  {"ok": False, "error": str(e)})
            except Exception as e:  # keep serving
                return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"stereo depth service on http://{host}:{port}/  (frame: /frame.jpg, depth: /depth.jpg)")
    try:
        srv.serve_forever()
    finally:
        srv.server_close()


INDEX = """<!doctype html><meta charset=utf-8><title>Stereo depth</title>
<style>body{font-family:sans-serif;background:#111;color:#eee;margin:12px}img{max-width:49%;cursor:crosshair}
#o{font-size:20px;margin:8px 0}</style>
<div id=o>Click the left image to read distance / height above the table</div>
<img id=f src=/frame.jpg><img id=d src=/depth.jpg>
<script>
const f=document.getElementById('f'),d=document.getElementById('d'),o=document.getElementById('o');
setInterval(()=>{f.src='/frame.jpg?'+Date.now()},400);setInterval(()=>{d.src='/depth.jpg?'+Date.now()},1000);
f.onclick=async e=>{const r=f.getBoundingClientRect(),u=(e.clientX-r.left)*f.naturalWidth/r.width,
v=(e.clientY-r.top)*f.naturalHeight/r.height;const j=await (await fetch(`/api/point?u=${u}&v=${v}`)).json();
o.textContent=j.ok?`pixel (${u|0}, ${v|0})  distance ${(j.distance_m*100).toFixed(1)} cm`+
(j.height_m!==undefined?`  height above table ${(j.height_m*100).toFixed(1)} cm (${j.plane})`:''):j.error};
</script>"""
