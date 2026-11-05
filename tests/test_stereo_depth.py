"""Stereo depth math on a synthetic rectified pair: a tilted table and a 9 cm block on it."""

import cv2
import numpy as np
import pytest

from stereo_depth.depth import DepthEngine, DepthError, _oriented

W, H = 800, 600
FX = 520.0
CX, CY = W / 2, H / 2
B = 0.06
K = np.array([[FX, 0, CX], [0, FX, CY], [0, 0, 1]], float)

# table: tilted 35 deg, about 0.5 m from the camera
TILT = np.radians(35)
N = np.array([0.0, -np.cos(TILT), -np.sin(TILT)])   # points back to the camera
P0 = np.array([0.0, 0.0, 0.55])
D0 = -float(N @ P0)
BLOCK = (330, 250, 140, 110)   # x0 y0 w h, pixels whose ray hits the block top
BLOCK_H = 0.09


def _depth_map() -> np.ndarray:
    u, v = np.meshgrid(np.arange(W, dtype=float), np.arange(H, dtype=float))
    rays = np.stack([(u - CX) / FX, (v - CY) / FX, np.ones_like(u)], axis=-1)
    denom = rays @ N
    z = -D0 / denom                        # table hit: N.(t*ray) + D0 = 0
    x0, y0, w, h = BLOCK
    zb = -(D0 - BLOCK_H) / denom           # top face: N.X + D0 = BLOCK_H
    z[y0:y0 + h, x0:x0 + w] = zb[y0:y0 + h, x0:x0 + w]
    return z


def _pair():
    rng = np.random.default_rng(1)
    tex = (rng.random((H // 2, W // 2 + 60)) * 255).astype(np.uint8)
    tex = cv2.resize(tex, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    tex = cv2.GaussianBlur(tex, (3, 3), 0)
    left = tex[:H, :W].copy()
    z = _depth_map()
    disp = FX * B / z
    # right(x) = left(x + d): remap with the disparity of the same pixel (smooth enough for a test)
    mx, my = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    right = cv2.remap(tex[:H, : W + 60], mx + disp.astype(np.float32), my, cv2.INTER_LINEAR)
    left = cv2.remap(tex[:H, : W + 60], mx, my, cv2.INTER_LINEAR)
    return left, right, z


@pytest.fixture(scope="module")
def scene():
    left, right, z = _pair()
    eng = DepthEngine(K, B, scale=0.5, num_disp=128)
    return eng, eng.disparity(left, right), z


def test_point_depth(scene):
    eng, disp, z = scene
    p = eng.point(disp, 600, 450)   # left ~num_disp columns have no match
    assert abs(p[2] - z[450, 600]) < 0.01


def test_ransac_plane_and_block_height(scene):
    eng, disp, _ = scene
    pts, px = eng.points(disp)
    x0, y0, w, h = BLOCK
    keep = ~((px[:, 0] > x0 - 20) & (px[:, 0] < x0 + w + 20) & (px[:, 1] > y0 - 20) & (px[:, 1] < y0 + h + 20))
    plane = eng.plane_ransac(pts[keep][::7])
    assert abs(abs(plane.n @ N) - 1) < 0.01
    res = eng.height_in_box(disp, plane, (x0 - 15, y0 - 15, w + 30, h + 30))
    assert abs(res["height_m"] - BLOCK_H) < 0.012, res


def test_empty_table_has_no_height(scene):
    eng, disp, _ = scene
    plane = _oriented(N, P0, "marker")
    with pytest.raises(DepthError):
        eng.height_in_box(disp, plane, (60, 420, 120, 100))


def test_plane_orientation_points_to_camera():
    plane = _oriented(-N, P0, "x")
    assert plane.d > 0 and plane.height(np.zeros((1, 3)))[0] > 0


def test_marker_plane_matches_table():
    from stereo_depth.depth import DepthEngine
    size = 0.05
    # marker frame: X along table, Y up the slope, Z = table normal (towards the camera)
    z_axis = N
    x_axis = np.array([1.0, 0.0, 0.0])
    y_axis = np.cross(z_axis, x_axis)
    R = np.column_stack([x_axis, y_axis, z_axis])
    t = P0
    s = size / 2
    obj = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]])
    img_pts = (K @ (R @ obj.T + t[:, None])).T
    img_pts = (img_pts[:, :2] / img_pts[:, 2:]).astype(np.float32)
    marker = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), 0, 200)
    src = np.array([[-0.5, -0.5], [199.5, -0.5], [199.5, 199.5], [-0.5, 199.5]], np.float32)
    canvas = np.full((H, W), 255, np.uint8)
    warped = cv2.warpPerspective(marker, cv2.getPerspectiveTransform(src, img_pts), (W, H), borderValue=255)
    canvas = np.minimum(canvas, warped)
    plane = DepthEngine(K, B).plane_from_marker(canvas, size)
    assert plane is not None and plane.source == "marker"
    assert abs(plane.n @ N - 1) < 0.02
    assert abs(plane.height(P0[None])[0]) < 0.005


def test_service_height_end_to_end():
    from stereo_depth.calib import StereoCalibration
    from stereo_depth.service import DepthService
    left, right, _ = _pair()

    class FakeCam:
        index, fps = 9, 30.0

        def read(self, max_age_s=1.0):
            return cv2.cvtColor(left, cv2.COLOR_GRAY2BGR), cv2.cvtColor(right, cv2.COLOR_GRAY2BGR)

    cal = StereoCalibration((W, H), K, np.zeros(5), K, np.zeros(5), np.eye(3), np.array([[-B], [0], [0]]), 0.2)
    svc = DepthService(FakeCam(), cal)
    st = svc.status()
    assert st["frame_size"] == [W, H] and abs(st["baseline_mm"] - 60) < 0.5
    x0, y0, w, h = BLOCK
    res = svc.height(x0 - 15, y0 - 15, w + 30, h + 30)
    assert res["ok"] and res["plane"] == "ransac" and abs(res["height_m"] - BLOCK_H) < 0.015, res
    assert svc.frame_jpeg()[:2] == b"\xff\xd8" and svc.depth_jpeg()[:2] == b"\xff\xd8"


def test_flat_screen_board_is_found_when_it_does_not_fill_the_frame():
    """A screen lying flat is sharp but small, and its pixel grid looks like a second board."""
    from stereo_depth.calib import BOARD, find_corners
    square = 18
    cols, rows = BOARD[0] + 1, BOARD[1] + 1
    board = np.full((rows * square + 40, cols * square + 40), 255, np.uint8)
    ox = oy = 20
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                board[oy + r * square:oy + (r + 1) * square, ox + c * square:ox + (c + 1) * square] = 0
    board[::2] = (board[::2].astype(np.uint16) * 3 // 5).astype(np.uint8)
    frame = np.full((900, 1400), 90, np.uint8)
    frame[40:200, 40:500] = 20
    frame[40:180, 800:1300] = 200
    bh, bw = board.shape
    frame[-bh - 30:-30, 500:500 + bw] = board
    got = find_corners(frame, fast=True)
    assert got is not None and len(got) == BOARD[0] * BOARD[1]


def test_opposite_eye_corner_order_is_turned_to_match():
    from stereo_depth.calib import BOARD, align_board_order
    cols, rows = BOARD
    ys, xs = np.mgrid[0:rows, 0:cols]
    left = (np.stack([xs, ys], axis=-1).reshape(-1, 1, 2) * 20 + 100).astype(np.float32)
    right = left.reshape(rows, cols, 2)[::-1, ::-1].reshape(-1, 1, 2).copy()
    right += np.array([30, -5], np.float32)
    _, aligned = align_board_order(left, right)
    gap = np.median(np.linalg.norm(aligned.reshape(-1, 2) - left.reshape(-1, 2) - np.array([30, -5]), axis=1))
    assert gap < 1
