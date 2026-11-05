"""Table-plane XY from the four corner tapes and a cup's footprint.

This follows the camera plan's useful part: a homography maps the table plane
only, the cup point is the base of the colour mask, and a missing tape is not
invented. The tapes on this table are orange. There is no gripper, so this
module never builds a grasp pose and never moves the robot.

Image right is robot left (negative Y). Image bottom is the front edge
(negative X). The robot coordinates are the taught tape touches, not values
read out of the picture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from grokarm_vision.calibration import Calibration, CalPoint

from .cups import COLOUR_SV, COLOURS

# Taught by touching each tape. Metres, robot base. Order is not image order.
TAUGHT_TAPE_M: dict[str, tuple[float, float]] = {
    "后右": (0.2749, 0.3065),
    "后左": (0.2758, -0.2593),
    "前右": (-0.2670, 0.2726),
    "前左": (-0.2699, -0.2750),
}
# Image corners under the fixed camera: top-left, top-right, bottom-left, bottom-right.
IMAGE_CORNER = {"后右": "tl", "后左": "tr", "前右": "bl", "前左": "br"}


def eye(image: np.ndarray) -> np.ndarray:
    """The left picture when the frame is a side-by-side stereo pair."""
    h, w = image.shape[:2]
    if w >= int(1.8 * h):
        return image[:, : w // 2]
    return image


def _orange_centers(image: np.ndarray) -> list[tuple[float, float, float]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 80, 80), (22, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
    area_img = float(image.shape[0] * image.shape[1])
    found = []
    for i in range(1, n):
        area = float(stats[i, cv2.CC_STAT_AREA])
        w, h = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        if not (0.00008 * area_img <= area <= 0.003 * area_img):
            continue
        if w < 2 or h < 2 or not (0.45 <= w / h <= 2.2):
            continue
        found.append((float(cent[i, 0]), float(cent[i, 1]), area))
    return found


def _quad_ok(pts: np.ndarray) -> tuple[float, float]:
    """(area, mean degrees off a right angle). (0, 0) when the quad is degenerate."""
    hull = cv2.convexHull(pts.astype(np.float32))
    if len(hull) != 4:
        return 0.0, 0.0
    area = float(cv2.contourArea(hull))
    if area < 1.0:
        return 0.0, 0.0
    edges = []
    angles = []
    for i in range(4):
        p0, p1, p2 = hull[i, 0], hull[(i + 1) % 4, 0], hull[(i + 2) % 4, 0]
        edges.append(float(np.linalg.norm(p1 - p0)))
        v1, v2 = p0 - p1, p2 - p1
        cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
        angles.append(float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))))
    if min(edges) < 1.0 or max(edges) / min(edges) > 3.0 or min(angles) < 55.0:
        return 0.0, 0.0
    return area, float(np.mean(np.abs(np.array(angles) - 90.0)))


def order_corners(centers: list[tuple[float, float, float]]) -> dict[str, tuple[float, float]] | None:
    """Four tape centres labelled in robot names, or None when the set is ambiguous."""
    if len(centers) < 4:
        return None
    pts = np.array([[c[0], c[1]] for c in centers], dtype=np.float64)
    best: tuple[float, np.ndarray] | None = None
    second = 0.0
    n = len(pts)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                for m in range(k + 1, n):
                    chosen = pts[[i, j, k, m]]
                    blob = np.array([centers[i][2], centers[j][2], centers[k][2], centers[m][2]])
                    if float(blob.min()) <= 0 or float(blob.max()) / float(blob.min()) > 6.0:
                        continue
                    area, skew = _quad_ok(chosen)
                    if area <= 0 or area < 0.12 * float(np.ptp(pts[:, 0]) * max(np.ptp(pts[:, 1]), 1.0)):
                        continue
                    ys = np.argsort(chosen[:, 1])
                    span = float(chosen[ys[-1], 1] - chosen[ys[0], 1])
                    if span < 10 or float(chosen[ys[2], 1] - chosen[ys[1], 1]) < 0.2 * span:
                        continue
                    # Lower skew wins. Similar tape sizes break a tie with a stray orange object.
                    score = skew / max(float(blob.min()) / float(blob.max()), 0.2)
                    if best is None or score < best[0]:
                        second = best[0] if best else 0.0
                        best = (score, chosen)
                    elif second == 0.0 or score < second:
                        second = score
    if best is None or (second > 0 and second < best[0] * 1.2):
        return None
    chosen = best[1]
    ys = np.argsort(chosen[:, 1])
    top = chosen[ys[:2]][np.argsort(chosen[ys[:2], 0])]
    bottom = chosen[ys[2:]][np.argsort(chosen[ys[2:], 0])]
    tl, tr, bl, br = top[0], top[1], bottom[0], bottom[1]
    by_place = {"tl": tl, "tr": tr, "bl": bl, "br": br}
    return {name: (float(by_place[place][0]), float(by_place[place][1])) for name, place in IMAGE_CORNER.items()}


def footprint(image: np.ndarray, colour: str) -> tuple[float, float] | None:
    """Centre of the lower part of the colour mask: the cup base, not the box bottom."""
    ranges = COLOURS.get(colour)
    mask = None
    if ranges:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        sat, val = COLOUR_SV.get(colour, (50, 40))
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in ranges:
            mask |= cv2.inRange(hsv, (lo, sat, val), (hi, 255, 255))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    px = _footprint_on(mask, image) if mask is not None else None
    if px is not None:
        return px
    from .cups import lab_mask
    pale = lab_mask(image, colour)
    if pale is None:
        return None
    pale = cv2.morphologyEx(pale, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return _footprint_on(pale, image)


def _footprint_on(mask: np.ndarray | None, image: np.ndarray) -> tuple[float, float] | None:
    if mask is None:
        return None
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    area_img = float(image.shape[0] * image.shape[1])
    hits = [i for i in range(1, n) if 0.0004 * area_img <= stats[i, cv2.CC_STAT_AREA] <= 0.05 * area_img]
    if not hits:
        return None
    hits.sort(key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=True)
    if len(hits) > 1:
        largest = int(stats[hits[0], cv2.CC_STAT_AREA])
        second = int(stats[hits[1], cv2.CC_STAT_AREA])
        if largest < second * 3:
            return None
    ys, xs = np.where(labels == hits[0])
    cut = np.percentile(ys, 65)
    keep = ys >= cut
    if int(keep.sum()) < 5:
        return None
    return float(xs[keep].mean()), float(ys[keep].mean())


@dataclass
class Plane:
    pairs: dict[str, tuple[tuple[float, float], tuple[float, float]]]
    frame_size: tuple[int, int]
    fit: Calibration

    @classmethod
    def build(cls, image: np.ndarray, taught: dict[str, tuple[float, float]] | None = None) -> "Plane | None":
        view = eye(image)
        corners = order_corners(_orange_centers(view))
        if corners is None:
            return None
        taught = taught or TAUGHT_TAPE_M
        if set(corners) != set(taught):
            return None
        h, w = view.shape[:2]
        points = [CalPoint(corners[name], (taught[name][0] * 1000, taught[name][1] * 1000)) for name in corners]
        return cls({name: (corners[name], taught[name]) for name in corners}, (w, h),
                   Calibration.fit(points, (w, h)))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": 1,
            "direction": "image_to_robot",
            "units": "m",
            "frame_size": list(self.frame_size),
            "eye": "left",
            "note": "Four orange tapes. Plane XY only. Not independently checked, so it only proposes a point.",
            "pairs": {name: {"px": list(px), "xy_m": list(xy)} for name, (px, xy) in self.pairs.items()},
        }
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Plane | None":
        if not path.exists():
            return None
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("version") != 1 or d.get("direction") != "image_to_robot":
            return None
        pairs = {name: (tuple(v["px"]), tuple(v["xy_m"])) for name, v in d["pairs"].items()}
        size = tuple(int(v) for v in d["frame_size"])
        points = [CalPoint(px, (xy[0] * 1000, xy[1] * 1000)) for px, xy in pairs.values()]
        return cls(pairs, size, Calibration.fit(points, size))

    def locate(self, image: np.ndarray, colour: str) -> dict:
        view = eye(image)
        h, w = view.shape[:2]
        if (w, h) != tuple(self.frame_size):
            return {"ok": False, "esito": "risoluzione", "messaggio": "相机分辨率和标定不一致。"}
        seen = order_corners(_orange_centers(view))
        if seen is not None:
            err = [float(np.hypot(seen[n][0] - self.pairs[n][0][0], seen[n][1] - self.pairs[n][0][1])) for n in seen]
            diag = float(np.hypot(*self.frame_size))
            if sum(err) / len(err) > 0.08 * diag:
                return {"ok": False, "esito": "camera_spostata", "messaggio": "胶带在画面里的位置变了，相机可能动过。"}
        px = footprint(view, colour)
        if px is None:
            return {"ok": False, "esito": "oggetto_non_trovato", "messaggio": f"没有看清{colour}杯子的底部。"}
        x, y = self.fit.to_mm(px[0], px[1])
        xy = (x / 1000.0, y / 1000.0)
        if not self.fit.inside_workspace((x, y), -8.0):
            return {"ok": False, "esito": "fuori_area", "messaggio": "杯子不在四块胶带围成的桌面上。"}
        return {"ok": True, "xy_m": xy, "px": px, "markers": "saved" if seen is None else "seen"}
