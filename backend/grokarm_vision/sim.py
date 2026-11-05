"""Synthetic top-down table scenes with a known pixel -> robot mm mapping.

Also a fake phone camera for rehearsing without hardware:
    python -m grokarm_vision.sim --setup --objects red:350:120,red:350:-120,blue:240:0
"""

from __future__ import annotations

import argparse
import time

import cv2
import httpx
import numpy as np

W, H = 1280, 720
PX_CORNERS = np.float32([[160, 80], [1120, 100], [1180, 660], [100, 640]])
# Robot base frame: x forward (away from base), y to the robot's left.
MM_CORNERS = np.float32([[450, 250], [450, -250], [150, -250], [150, 250]])
H_TRUE = cv2.getPerspectiveTransform(PX_CORNERS, MM_CORNERS)
H_INV = np.linalg.inv(H_TRUE)

BGR = {
    "red": (40, 40, 210),
    "blue": (200, 90, 30),
    "green": (60, 170, 50),
    "yellow": (40, 210, 230),
    "black": (25, 25, 25),
}


def mm_to_px(x: float, y: float) -> tuple[float, float]:
    u, v = cv2.perspectiveTransform(np.float64([[[x, y]]]), H_INV)[0, 0]
    return float(u), float(v)


def px_to_mm(u: float, v: float) -> tuple[float, float]:
    x, y = cv2.perspectiveTransform(np.float64([[[u, v]]]), H_TRUE)[0, 0]
    return float(x), float(y)


def table(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (H // 8, W // 8), dtype=np.uint8)
    texture = cv2.resize(noise, (W, H), interpolation=cv2.INTER_CUBIC)
    base = np.dstack([texture * 0.25 + 150, texture * 0.25 + 140, texture * 0.25 + 125]).astype(np.uint8)
    for i in range(0, W, 80):
        cv2.line(base, (i, 0), (i, H), (120, 115, 105), 1)
    for j in range(0, H, 80):
        cv2.line(base, (0, j), (W, j), (120, 115, 105), 1)
    for corner in PX_CORNERS:
        cv2.drawMarker(base, tuple(int(c) for c in corner), (30, 30, 30), cv2.MARKER_TILTED_CROSS, 30, 3)
    return base


def with_objects(background: np.ndarray, objects: list[tuple[str, float, float]], radius: int = 28) -> np.ndarray:
    img = background.copy()
    for colour, x, y in objects:
        u, v = mm_to_px(x, y)
        cv2.circle(img, (int(round(u)), int(round(v))), radius, BGR[colour], -1)
    return img


def shifted(image: np.ndarray, dx: float, dy: float) -> np.ndarray:
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(image, m, (image.shape[1], image.shape[0]), borderMode=cv2.BORDER_REFLECT)


def calibration_points() -> list[dict]:
    mm = [(430, 220), (430, -220), (170, -220), (170, 220), (300, 0), (360, 120)]
    return [{"px": list(mm_to_px(x, y)), "robot_mm": [x, y]} for x, y in mm]


def _parse_objects(text: str) -> list[tuple[str, float, float]]:
    out = []
    for item in filter(None, text.split(",")):
        colour, x, y = item.split(":")
        if colour not in BGR:
            raise SystemExit(f"unknown colour {colour!r}; use one of {sorted(BGR)}")
        out.append((colour, float(x), float(y)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m grokarm_vision.sim")
    parser.add_argument("--url", default="http://127.0.0.1:8710")
    parser.add_argument("--objects", default="red:350:120,red:350:-120,blue:240:0",
                        help="colour:x_mm:y_mm,... in the robot base frame")
    parser.add_argument("--setup", action="store_true", help="calibrate and capture the background first")
    parser.add_argument("--fps", type=float, default=2.0)
    args = parser.parse_args()
    from .frames import encode_jpeg

    bg = table()
    scene = with_objects(bg, _parse_objects(args.objects))
    with httpx.Client(base_url=args.url, timeout=10) as client:
        if args.setup:
            client.post("/api/frame", content=encode_jpeg(bg, 95)).raise_for_status()
            client.post("/api/calibration", json={"points": calibration_points()}).raise_for_status()
            client.post("/api/background").raise_for_status()
            print("calibrated and captured the empty table")
        jpeg = encode_jpeg(scene, 95)
        print(f"sending a fake scene to {args.url} at {args.fps} fps; Ctrl+C to stop")
        while True:
            client.post("/api/frame", content=jpeg).raise_for_status()
            time.sleep(1.0 / args.fps)


if __name__ == "__main__":
    main()
