"""Disparity -> metric 3D points, table plane, and object height above the table.

All pixel coordinates are pixels of the rectified LEFT image (= /frame.jpg). Camera frame: X right,
Y down, Z forward, metres.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_HEIGHT_M = 0.01      # points lower than this count as table
MAX_HEIGHT_M = 0.40
MIN_POINTS = 40


class DepthError(ValueError):
    pass


@dataclass
class Plane:
    n: np.ndarray        # unit normal, pointing to the camera side (= up from the table)
    d: float             # n . X + d = 0
    source: str          # "marker" or "ransac"

    def height(self, pts: np.ndarray) -> np.ndarray:
        return pts @ self.n + self.d


class DepthEngine:
    def __init__(self, K: np.ndarray, baseline_m: float, scale: float = 0.5, num_disp: int = 128,
                 block: int = 5) -> None:
        self.K = np.asarray(K, dtype=np.float64)
        self.fx, self.fy = float(self.K[0, 0]), float(self.K[1, 1])
        self.cx, self.cy = float(self.K[0, 2]), float(self.K[1, 2])
        self.baseline = float(baseline_m)
        self.scale = float(scale)
        num_disp = int(np.ceil(num_disp / 16) * 16)
        self.matcher = cv2.StereoSGBM_create(minDisparity=0, numDisparities=num_disp, blockSize=block,
                                             P1=8 * block * block, P2=32 * block * block, disp12MaxDiff=1,
                                             uniquenessRatio=10, speckleWindowSize=100, speckleRange=2,
                                             mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)

    # -------------------------------------------------------------- disparity ---
    def disparity(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """Disparity in FULL-resolution pixels, sampled on the reduced grid (NaN = no match)."""
        gl = left if left.ndim == 2 else cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
        gr = right if right.ndim == 2 else cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
        if self.scale != 1.0:
            gl = cv2.resize(gl, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
            gr = cv2.resize(gr, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        disp = self.matcher.compute(gl, gr).astype(np.float32) / 16.0
        disp[disp <= 0.5] = np.nan
        return disp / self.scale

    def points(self, disp: np.ndarray, box: tuple[float, float, float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """3D points (N,3) and their full-res pixels (N,2) for valid disparities, optionally inside a box."""
        h, w = disp.shape
        if box is None:
            sx0, sy0, sx1, sy1 = 0, 0, w, h
        else:
            x0, y0, bw, bh = box
            sx0 = int(np.clip(np.floor(x0 * self.scale), 0, w))
            sy0 = int(np.clip(np.floor(y0 * self.scale), 0, h))
            sx1 = int(np.clip(np.ceil((x0 + bw) * self.scale), 0, w))
            sy1 = int(np.clip(np.ceil((y0 + bh) * self.scale), 0, h))
        sub = disp[sy0:sy1, sx0:sx1]
        ys, xs = np.nonzero(np.isfinite(sub))
        d = sub[ys, xs]
        u = (xs + sx0 + 0.5) / self.scale - 0.5
        v = (ys + sy0 + 0.5) / self.scale - 0.5
        z = self.fx * self.baseline / d
        pts = np.column_stack([(u - self.cx) * z / self.fx, (v - self.cy) * z / self.fy, z])
        return pts, np.column_stack([u, v])

    def point(self, disp: np.ndarray, u: float, v: float, radius_px: float = 6.0) -> np.ndarray:
        r = radius_px
        pts, _ = self.points(disp, (u - r, v - r, 2 * r, 2 * r))
        if len(pts) < 3:
            raise DepthError("no depth at that pixel (no texture, occluded, or too close)")
        return np.median(pts, axis=0)

    # ------------------------------------------------------------ table plane ---
    def plane_from_marker(self, image: np.ndarray, size_m: float, marker_id: int = 0) -> Plane | None:
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX   # plain contours sit ~0.7 px inside
        corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, params).detectMarkers(gray)
        if ids is None or marker_id not in ids.flatten():
            return None
        c = np.asarray(corners[int(np.where(ids.flatten() == marker_id)[0][0])][0], dtype=np.float64)
        s = size_m / 2.0
        obj = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float64)
        ok, rvec, tvec = cv2.solvePnP(obj, c, self.K, np.zeros(5), flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if not ok:
            return None
        R, _ = cv2.Rodrigues(rvec)
        return _oriented(R[:, 2], tvec.reshape(3), "marker")

    @staticmethod
    def plane_ransac(pts: np.ndarray, thresh_m: float = 0.008, iters: int = 300, seed: int = 0) -> Plane:
        if len(pts) < 50:
            raise DepthError("not enough depth points to find the table (put the printed marker in view)")
        rng = np.random.default_rng(seed)
        best, best_n = None, -1
        for _ in range(iters):
            a, b, c = pts[rng.choice(len(pts), 3, replace=False)]
            n = np.cross(b - a, c - a)
            norm = np.linalg.norm(n)
            if norm < 1e-9:
                continue
            n = n / norm
            inl = np.abs((pts - a) @ n) < thresh_m
            k = int(inl.sum())
            if k > best_n:
                best, best_n = inl, k
        if best is None or best_n < 30:
            raise DepthError("table plane not found")
        p = pts[best]
        centroid = p.mean(axis=0)
        _, _, vt = np.linalg.svd(p - centroid)
        return _oriented(vt[2], centroid, "ransac")

    # ---------------------------------------------------------------- height ---
    def height_in_box(self, disp: np.ndarray, plane: Plane, box: tuple[float, float, float, float],
                      percentile: float = 90.0) -> dict:
        pts, _ = self.points(disp, box)
        if len(pts) == 0:
            raise DepthError("no depth inside the box")
        h = plane.height(pts)
        above = h[(h > MIN_HEIGHT_M) & (h < MAX_HEIGHT_M)]
        if len(above) < MIN_POINTS:
            raise DepthError(f"only {len(above)} depth points above the table in the box")
        top = float(np.percentile(above, percentile))
        return {"height_m": round(top, 4), "points": int(len(above)), "plane": plane.source}


def _oriented(normal: np.ndarray, point: np.ndarray, source: str) -> Plane:
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.linalg.norm(n)
    p = np.asarray(point, dtype=np.float64)
    d = -float(n @ p)
    if d < 0:            # camera (origin) must be on the positive side: height(origin) = d > 0
        n, d = -n, -d
    return Plane(n, d, source)


def colorize(disp: np.ndarray, width: int | None = None) -> np.ndarray:
    v = np.nan_to_num(disp, nan=0.0)
    hi = np.nanpercentile(disp, 98) if np.isfinite(disp).any() else 1.0
    img = np.clip(v / max(hi, 1e-6) * 255, 0, 255).astype(np.uint8)
    img = cv2.applyColorMap(img, cv2.COLORMAP_JET)
    img[~np.isfinite(disp)] = 0
    if width:
        img = cv2.resize(img, (width, int(round(img.shape[0] * width / img.shape[1]))),
                         interpolation=cv2.INTER_NEAREST)
    return img
