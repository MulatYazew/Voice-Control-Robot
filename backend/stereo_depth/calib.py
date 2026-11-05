"""Checkerboard stereo calibration and rectification.

Board: 9 x 6 inner corners (10 x 7 squares). Print it at actual size and MEASURE one square: the
square size sets the metric scale of every depth value.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

BOARD = (9, 6)  # inner corners (cols, rows)


def board_png(path: str | Path, square_mm: float = 25.0, dpi: int = 300) -> Path:
    """A4 landscape sheet with the checkerboard at true size (25 mm squares fit with margin)."""
    px_per_mm = dpi / 25.4
    sq = int(round(square_mm * px_per_mm))
    cols, rows = BOARD[0] + 1, BOARD[1] + 1
    page_w, page_h = int(round(297 * px_per_mm)), int(round(210 * px_per_mm))
    img = np.full((page_h, page_w), 255, np.uint8)
    ox, oy = (page_w - cols * sq) // 2, (page_h - rows * sq) // 2
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                img[oy + r * sq: oy + (r + 1) * sq, ox + c * sq: ox + (c + 1) * sq] = 0
    cv2.putText(img, f"{BOARD[0]}x{BOARD[1]} inner corners - square {square_mm:g} mm - print at 100% (no fit to page)"
                     " - MEASURE one square", (ox, oy + rows * sq + int(8 * px_per_mm)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3, cv2.LINE_AA)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


_PLAIN = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
_SB = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY | cv2.CALIB_CB_NORMALIZE_IMAGE


def _gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _screen(gray: np.ndarray) -> np.ndarray:
    """Blur an LCD pixel grid. The squares stay; the moiré that appears when a
    screen faces the camera does not."""
    return cv2.GaussianBlur(gray, (5, 5), 0)


def find_corners(image: np.ndarray, fast: bool = False) -> np.ndarray | None:
    raw = _gray(image)
    if fast:
        # No CALIB_CB_FAST_CHECK. That shortcut rejects a sharp board that only
        # fills part of the frame: a screen lying flat, or held lower, farther
        # from the camera.
        ok, corners = cv2.findChessboardCorners(_screen(raw), BOARD, flags=_PLAIN)
        return corners.astype(np.float32) if ok else None
    ok, corners = cv2.findChessboardCornersSB(raw, BOARD, flags=_SB)
    if ok:
        return corners.astype(np.float32)
    blurred = _screen(raw)
    ok, corners = cv2.findChessboardCornersSB(blurred, BOARD, flags=_SB)
    if not ok:
        ok, corners = cv2.findChessboardCorners(blurred, BOARD, flags=_PLAIN)
        if not ok:
            return None
    corners = cv2.cornerSubPix(raw, corners, (11, 11), (-1, -1),
                               (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-4))
    return corners.astype(np.float32)


def align_board_order(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Make both eyes number the same physical corner as corner 0.

    Each eye can start at either end of the board. A 180 degree flip on only
    one eye still looks like a valid grid, and one such pair pulls the stereo
    error from under a pixel to tens of pixels.
    """
    def axis(corners: np.ndarray) -> np.ndarray:
        grid = corners.reshape(BOARD[1], BOARD[0], 2)
        return grid[0, -1] - grid[0, 0]

    if float(np.dot(axis(left), axis(right))) >= 0:
        return left, right
    flipped = right.reshape(BOARD[1], BOARD[0], 2)[::-1, ::-1].reshape(right.shape).copy()
    return left, np.ascontiguousarray(flipped)


def board_points(square_m: float) -> np.ndarray:
    obj = np.zeros((BOARD[0] * BOARD[1], 3), np.float32)
    obj[:, :2] = np.mgrid[0:BOARD[0], 0:BOARD[1]].T.reshape(-1, 2) * square_m
    return obj


@dataclass
class StereoCalibration:
    size: tuple[int, int]          # (w, h) of ONE eye
    K1: np.ndarray
    D1: np.ndarray
    K2: np.ndarray
    D2: np.ndarray
    R: np.ndarray
    T: np.ndarray
    rms: float

    @property
    def baseline_m(self) -> float:
        return float(np.linalg.norm(self.T))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, size=np.array(self.size), K1=self.K1, D1=self.D1, K2=self.K2, D2=self.D2,
                 R=self.R, T=self.T, rms=np.array(self.rms))

    @classmethod
    def load(cls, path: str | Path) -> "StereoCalibration":
        d = np.load(Path(path))
        return cls(tuple(int(v) for v in d["size"]), d["K1"], d["D1"], d["K2"], d["D2"], d["R"], d["T"],
                   float(d["rms"]))


def calibrate(left_corners: list[np.ndarray], right_corners: list[np.ndarray], size: tuple[int, int],
              square_m: float) -> tuple[StereoCalibration, dict]:
    if len(left_corners) < 8:
        raise ValueError("need at least 8 board views (15-20 is better)")
    obj = [board_points(square_m)] * len(left_corners)
    rms1, K1, D1, _, _ = cv2.calibrateCamera(obj, left_corners, size, None, None)
    rms2, K2, D2, _, _ = cv2.calibrateCamera(obj, right_corners, size, None, None)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-6)
    rms, K1, D1, K2, D2, R, T, _, _ = cv2.stereoCalibrate(obj, left_corners, right_corners, K1, D1, K2, D2, size,
                                                          criteria=crit, flags=cv2.CALIB_FIX_INTRINSIC)
    cal = StereoCalibration(size, K1, D1, K2, D2, R, T, float(rms))
    return cal, {"rms_left": float(rms1), "rms_right": float(rms2), "rms_stereo": float(rms),
                 "baseline_mm": round(cal.baseline_m * 1000, 1), "views": len(left_corners)}


class Rectifier:
    """Rectified images share rows; LEFT rectified intrinsics are those of /frame.jpg."""

    def __init__(self, cal: StereoCalibration) -> None:
        self.cal = cal
        w, h = cal.size
        R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(cal.K1, cal.D1, cal.K2, cal.D2, (w, h), cal.R, cal.T,
                                                    flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
        self.P1, self.P2, self.Q = P1, P2, Q
        self.map1 = cv2.initUndistortRectifyMap(cal.K1, cal.D1, R1, P1, (w, h), cv2.CV_16SC2)
        self.map2 = cv2.initUndistortRectifyMap(cal.K2, cal.D2, R2, P2, (w, h), cv2.CV_16SC2)

    @property
    def K(self) -> np.ndarray:
        return self.P1[:3, :3].copy()

    @property
    def fx(self) -> float:
        return float(self.P1[0, 0])

    @property
    def baseline_m(self) -> float:
        return float(abs(self.P2[0, 3] / self.P2[0, 0]))

    def intrinsics(self) -> dict[str, float]:
        return {"fx": round(float(self.P1[0, 0]), 2), "fy": round(float(self.P1[1, 1]), 2),
                "cx": round(float(self.P1[0, 2]), 2), "cy": round(float(self.P1[1, 2]), 2)}

    def rectify(self, left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if (left.shape[1], left.shape[0]) != tuple(self.cal.size):
            raise ValueError(f"camera gives {left.shape[1]}x{left.shape[0]} per eye, calibration is "
                             f"{self.cal.size[0]}x{self.cal.size[1]}: use the same resolution or recalibrate")
        l = cv2.remap(left, *self.map1, cv2.INTER_LINEAR)
        r = cv2.remap(right, *self.map2, cv2.INTER_LINEAR)
        return l, r
