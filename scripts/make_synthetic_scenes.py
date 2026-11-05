"""Draw fixed-seed tray scenes for the object finder. No camera, no network.

    python scripts/make_synthetic_scenes.py
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synthetic_scenes"
ZONES = {
    "sinistra": [[0, 0], [200, 0], [200, 200], [0, 200]],
    "centro": [[200, 0], [400, 0], [400, 200], [200, 200]],
    "destra": [[400, 0], [600, 0], [600, 200], [400, 200]],
}


def _base() -> np.ndarray:
    img = np.full((200, 600, 3), 180, np.uint8)
    return img


def draw(name: str, blobs: list[tuple[str, tuple[int, int, int]]]) -> None:
    img = _base()
    for zone, color in blobs:
        poly = np.array(ZONES[zone], np.int32)
        x, y, w, h = cv2.boundingRect(poly)
        cx, cy = x + w // 2, y + h // 2
        cv2.circle(img, (cx, cy), 28, color, -1)
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / f"{name}.png"), img)


def main() -> None:
    red = (40, 40, 210)
    blue = (200, 60, 40)
    draw("one_red_left", [("sinistra", red)])
    draw("two_red", [("sinistra", red), ("centro", red)])
    draw("blue_right", [("destra", blue)])
    print(f"wrote 3 scenes in {OUT}")


if __name__ == "__main__":
    main()
