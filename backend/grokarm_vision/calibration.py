"""Pixel -> robot base XY (mm) on the table plane, via a homography.

Calibration points are pairs of an image pixel and the robot TCP XY read from
the pendant (or `getActualTCPPose`) with the tool tip touching a table mark.
The service never talks to the robot; the operator types the XY values in.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MIN_POINTS = 4
MAX_LOO_ERROR_MM = 8.0


class CalibrationError(ValueError):
    pass


@dataclass
class CalPoint:
    px: tuple[float, float]
    robot_mm: tuple[float, float]


@dataclass
class Calibration:
    points: list[CalPoint]
    homography: np.ndarray
    frame_size: tuple[int, int]
    fit_rms_mm: float
    loo_max_mm: float | None
    created_at: float = field(default_factory=time.time)

    @classmethod
    def fit(cls, points: list[CalPoint], frame_size: tuple[int, int]) -> "Calibration":
        if len(points) < MIN_POINTS:
            raise CalibrationError(f"need at least {MIN_POINTS} points, got {len(points)}")
        px = np.array([p.px for p in points], dtype=np.float64)
        mm = np.array([p.robot_mm for p in points], dtype=np.float64)
        if _degenerate(px) or _degenerate(mm):
            raise CalibrationError("points are (nearly) collinear; spread them over the table")
        h = _homography(px, mm)
        fit_err = np.linalg.norm(_apply(h, px) - mm, axis=1)
        loo = _leave_one_out(px, mm)
        cal = cls(points, h, frame_size, float(np.sqrt(np.mean(fit_err**2))), loo)
        if loo is not None and loo > MAX_LOO_ERROR_MM:
            raise CalibrationError(
                f"leave-one-out error {loo:.1f} mm > {MAX_LOO_ERROR_MM} mm; "
                "a point is probably mistyped or clicked wrong"
            )
        return cal

    def to_mm(self, u: float, v: float) -> tuple[float, float]:
        x, y = _apply(self.homography, np.array([[u, v]], dtype=np.float64))[0]
        return float(x), float(y)

    def workspace_polygon(self) -> np.ndarray:
        mm = np.array([p.robot_mm for p in self.points], dtype=np.float32)
        return cv2.convexHull(mm).reshape(-1, 2)

    def inside_workspace(self, xy: tuple[float, float], margin_mm: float) -> bool:
        poly = self.workspace_polygon().reshape(-1, 1, 2)
        distance = cv2.pointPolygonTest(poly, (float(xy[0]), float(xy[1])), True)
        return distance >= margin_mm

    def summary(self) -> dict:
        return {
            "points": len(self.points),
            "frame_size": list(self.frame_size),
            "fit_rms_mm": round(self.fit_rms_mm, 2),
            "loo_max_mm": None if self.loo_max_mm is None else round(self.loo_max_mm, 2),
            "accuracy_measured": self.loo_max_mm is not None,
            "workspace_mm": self.workspace_polygon().round(1).tolist(),
            "created_at": self.created_at,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "points": [{"px": list(p.px), "robot_mm": list(p.robot_mm)} for p in self.points],
            "frame_size": list(self.frame_size),
            "created_at": self.created_at,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Calibration | None":
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        points = [CalPoint(tuple(p["px"]), tuple(p["robot_mm"])) for p in data["points"]]
        cal = cls.fit(points, tuple(data["frame_size"]))
        cal.created_at = data.get("created_at", cal.created_at)
        return cal


def _homography(px: np.ndarray, mm: np.ndarray) -> np.ndarray:
    h, _ = cv2.findHomography(px, mm, 0)
    if h is None:
        raise CalibrationError("homography could not be computed")
    return h


def _apply(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(pts.reshape(-1, 1, 2), h).reshape(-1, 2)


def _degenerate(pts: np.ndarray) -> bool:
    centered = pts - pts.mean(axis=0)
    s = np.linalg.svd(centered, compute_uv=False)
    return s[1] < 1e-3 * max(s[0], 1e-9) or s[0] < 1e-6


def _leave_one_out(px: np.ndarray, mm: np.ndarray) -> float | None:
    """With exactly 4 points the fit is exact, so accuracy cannot be measured."""
    if len(px) <= MIN_POINTS:
        return None
    worst = 0.0
    for i in range(len(px)):
        keep = np.arange(len(px)) != i
        if _degenerate(px[keep]):
            continue
        h = _homography(px[keep], mm[keep])
        worst = max(worst, float(np.linalg.norm(_apply(h, px[i : i + 1])[0] - mm[i])))
    return worst


def estimate_drift_px(reference: np.ndarray, current: np.ndarray) -> float | None:
    """Pixel shift of the camera since calibration; None if it cannot be judged."""
    if reference.shape[:2] != current.shape[:2]:
        return None
    ref = _prep(reference)
    cur = _prep(current)
    scale = reference.shape[1] / ref.shape[1]
    orb = cv2.ORB_create(1500)
    k1, d1 = orb.detectAndCompute(ref, None)
    k2, d2 = orb.detectAndCompute(cur, None)
    if d1 is not None and d2 is not None and len(k1) >= 20 and len(k2) >= 20:
        matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
        if len(matches) >= 15:
            src = np.float32([k1[m.queryIdx].pt for m in matches])
            dst = np.float32([k2[m.trainIdx].pt for m in matches])
            m, inliers = cv2.estimateAffinePartial2D(src, dst, ransacReprojThreshold=3.0)
            if m is not None and inliers is not None and int(inliers.sum()) >= 12:
                h, w = ref.shape[:2]
                corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
                moved = corners @ m[:, :2].T + m[:, 2]
                return float(np.max(np.linalg.norm(moved - corners, axis=1)) * scale)
    (dx, dy), response = cv2.phaseCorrelate(np.float32(ref), np.float32(cur))
    if response < 0.05:
        return None
    return float(np.hypot(dx, dy) * scale)


def _prep(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    target_w = 640
    if gray.shape[1] > target_w:
        f = target_w / gray.shape[1]
        gray = cv2.resize(gray, (target_w, int(round(gray.shape[0] * f))), interpolation=cv2.INTER_AREA)
    return gray
