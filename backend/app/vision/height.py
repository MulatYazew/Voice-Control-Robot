"""Cup XYZ above the table, from one RGB camera and the printed ArUco.

This is the OpenCV method used by the fixed-camera tabletop papers: ``solvePnP`` on the
marker gives the camera pose, the cup stands upright on the table, the bottom pixel's ray
meets the table, and the rim pixel's ray meets the vertical line through that point.
The result is metres in the marker frame: X, Y on the table, Z up from the table.

FoundationPose and Contact-GraspNet are not used here. They need a depth camera and an
NVIDIA GPU, which this phone camera does not have. A printed cup holder later uses the
same rim point plus a tool offset. Nothing here closes a gripper.
"""

from __future__ import annotations

import cv2
import numpy as np

from .marker import object_xy

MIN_HEIGHT_M = 0.03
MAX_HEIGHT_M = 0.35


class HeightError(ValueError):
    pass


def pose_from_marker(corners: np.ndarray, size_m: float, camera_matrix: np.ndarray,
                     dist: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    obj = np.hstack([object_xy(size_m), np.zeros((4, 1))]).astype(np.float64)
    dist = np.zeros(5) if dist is None else dist
    ok, rvec, tvec = cv2.solvePnP(obj, np.asarray(corners, dtype=np.float64), camera_matrix, dist,
                                  flags=cv2.SOLVEPNP_IPPE_SQUARE)
    if not ok:
        raise HeightError("pose")
    return rvec, tvec.reshape(3)


def _ray(px, camera_matrix: np.ndarray) -> np.ndarray:
    u, v = float(px[0]), float(px[1])
    direction = np.linalg.inv(camera_matrix) @ np.array([u, v, 1.0])
    return direction / np.linalg.norm(direction)


def standing_xyz(base_px, rim_px, rvec: np.ndarray, tvec: np.ndarray, camera_matrix: np.ndarray
                 ) -> tuple[float, float, float]:
    """Base pixel and rim pixel of an upright cup -> (x, y, height) metres in the marker frame."""
    rotation, _ = cv2.Rodrigues(rvec)
    t = np.asarray(tvec, dtype=float).reshape(3)
    base_ray = _ray(base_px, camera_matrix)
    base = np.column_stack([rotation[:, 0], rotation[:, 1], -base_ray])
    try:
        x, y, along = np.linalg.solve(base, -t)
    except np.linalg.LinAlgError as e:
        raise HeightError("ray") from e
    if along <= 0:
        raise HeightError("ray")
    rim_ray = _ray(rim_px, camera_matrix)
    target = -t - rotation[:, 0] * x - rotation[:, 1] * y
    design = np.column_stack([rotation[:, 2], -rim_ray])
    height, rim_along = np.linalg.lstsq(design, target, rcond=None)[0]
    if rim_along <= 0 or not MIN_HEIGHT_M <= float(height) <= MAX_HEIGHT_M:
        raise HeightError("height")
    return float(x), float(y), float(height)
