"""Printed ArUco: four corners in the image become one table plane, then the cup is approached from above."""

import cv2
import numpy as np
import pytest

from app.vision.marker import MarkerFitError, board_bgr, detect, object_xy, pairs_for_plane, sheet_png
from app.vision.table_calib import TableCalibration


def test_generated_marker_is_detected_with_the_arrow_on_plus_x():
    board = board_bgr()
    found = detect(board)
    assert found is not None
    corners = found["corners"]
    right = (corners[1] + corners[2]) / 2
    assert right[0] > found["center_px"][0]
    png = sheet_png(0.05)
    sheet = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert detect(sheet) is not None


def test_corners_land_on_the_marker_square_and_the_view_is_inside(tmp_path):
    board = board_bgr()
    found = detect(board)
    origin = np.array([0.30, -0.10])
    axis = origin + np.array([0.025, 0.0])
    h, w = board.shape[:2]
    pairs = pairs_for_plane(found["corners"], origin, axis, 0.05)
    cal = TableCalibration(path=tmp_path / "table.json")
    cal.replace_plane(pairs, 0.02, np.eye(3).tolist(), (0, 0, 0, 0, 0, 0), (w, h), "sim")
    assert cal.ready, cal.error
    assert len(pairs) == 4
    vx, vy = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    for px, (mx, my) in zip(found["corners"], object_xy(0.05)):
        got = np.array(cal.to_xy(float(px[0]), float(px[1])))
        assert np.allclose(got, origin + mx * vx + my * vy, atol=1e-3)


def test_a_wrong_arrow_distance_is_refused():
    board = board_bgr()
    found = detect(board)
    with pytest.raises(MarkerFitError):
        pairs_for_plane(found["corners"], (0.2, 0.0), (0.2 + 0.08, 0.0), 0.05)


async def test_two_tip_samples_fit_the_plane_and_the_approach_comes_from_above(gate, sim):
    from app.skills.registry import SkillError
    from app.vision.camera import CameraHub

    board = board_bgr()
    ok, buf = cv2.imencode(".jpg", board, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    assert ok
    cam = CameraHub(gate.s)
    gate.attach_vision(cam, None)
    cam.push(buf.tobytes())
    assert gate.marker_status()["seen"]

    with sim._lock:
        sim._q = list(gate.s.poses["sinistra_sopra"]["joints"])
    q = sim.current_joints()
    real = gate.planner.kin.fk(q)
    origin = real[:2, 3].copy()
    axis = origin + np.array([0.025, 0.0])
    touch_cfg = gate.s.robot["touch"]
    hover = float(touch_cfg["object_height_m"]) + float(touch_cfg["touch_offset_m"]) + float(touch_cfg["approach_above_m"])
    gate.s.lang = "zh"
    first = await gate.marker_record("center")
    assert first["ok"] and first["marker"]["center"] and not first["ready"]
    z = float(real[2, 3] - hover)
    sample = lambda xy: {"xy": (float(xy[0]), float(xy[1])), "z": z, "rot": real[:3, :3].tolist(),
                         "joints": tuple(float(v) for v in q)}
    gate._marker["center"] = sample(origin)
    gate._marker["axis"] = sample(axis)
    second = await gate._marker_fit()
    assert second["ok"] and second["ready"], second
    found = detect(cam.store.latest().image)
    got = np.array(gate.table_cal.to_xy(float(found["center_px"][0]), float(found["center_px"][1])))
    assert np.allclose(got, origin, atol=2e-3)
    try:
        plan = gate.planner.touch(q, (float(origin[0]), float(origin[1])), gate.table_cal)
    except SkillError as e:
        pytest.fail(f"approach refused: {e}")
    above = gate.planner.kin.tip(plan.motion[0].joints)
    down = gate.planner.kin.tip(plan.motion[1].joints)
    assert above[2] > down[2] + 0.05
    assert abs(down[0] - origin[0]) < 3e-3 and abs(down[1] - origin[1]) < 3e-3
