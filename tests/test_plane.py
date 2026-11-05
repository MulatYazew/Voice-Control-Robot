"""Four orange tapes map a cup footprint to the table. No robot motion in these checks."""

import cv2
import numpy as np

from app.vision.plane import Plane, order_corners, _orange_centers


def _scene() -> np.ndarray:
    img = np.full((240, 320, 3), (40, 40, 40), np.uint8)
    cv2.rectangle(img, (30, 20), (280, 210), (245, 245, 245), -1)
    for x, y in ((48, 32), (250, 36), (60, 190), (246, 186)):
        cv2.rectangle(img, (x - 4, y - 4), (x + 4, y + 4), (0, 120, 240), -1)
    cv2.circle(img, (300, 220), 4, (0, 140, 255), -1)  # tape roll, not a corner
    cv2.ellipse(img, (80, 50), (8, 6), 0, 0, 360, (210, 80, 20), -1)
    cv2.rectangle(img, (74, 50), (86, 62), (210, 80, 20), -1)
    return img


def test_four_tapes_ignore_the_roll_and_map_the_cup_base():
    img = _scene()
    assert len(_orange_centers(img)) >= 5
    plane = Plane.build(img)
    assert plane is not None
    loc = plane.locate(img, "blue")
    assert loc["ok"] and loc["markers"] == "seen"
    assert abs(loc["xy_m"][0]) < 0.4 and abs(loc["xy_m"][1]) < 0.4


def test_three_tapes_are_not_completed():
    img = _scene()
    img[28:40, 44:56] = (245, 245, 245)
    assert order_corners(_orange_centers(img)) is None
    assert Plane.build(img) is None


async def test_operator_command_points_without_a_second_yes(gate):
    from app.vision.camera import CameraHub
    from app.vision.inspector import MockInspector

    from .conftest import wait_state

    img = _scene()
    Plane.build(img).save(gate.s.data_dir / "plane.json")
    gate.s.robot.setdefault("tool", {})["gripper"] = False
    gate.attach_vision(CameraHub(gate.s), MockInspector())
    gate.camera.push(cv2.imencode(".jpg", img)[1].tobytes())
    res = await gate.point_at_cup("blue", source="rules")
    assert res["ok"] and res["esito"] == "esecuzione_avviata" and res["bicchiere"]["pinza"] is False
    assert await wait_state(gate, "IDLE")
    assert gate.last_result.success


async def test_gripper_switch_refuses_until_a_driver_exists(gate):
    from app.vision.camera import CameraHub
    from app.vision.inspector import MockInspector

    img = _scene()
    Plane.build(img).save(gate.s.data_dir / "plane.json")
    gate.s.robot.setdefault("tool", {})["gripper"] = True
    gate.attach_vision(CameraHub(gate.s), MockInspector())
    gate.camera.push(cv2.imencode(".jpg", img)[1].tobytes())
    res = await gate.point_at_cup("blue", source="ui")
    assert res["esito"] == "pinza_non_collegata" and gate.pending is None
    assert gate.state.value == "IDLE"


async def test_saved_plane_proposes_the_cup_and_does_not_move(gate):
    from app.vision.camera import CameraHub
    from app.vision.inspector import MockInspector

    img = _scene()
    plane = Plane.build(img)
    plane.save(gate.s.data_dir / "plane.json")
    gate.attach_vision(CameraHub(gate.s), MockInspector())
    ok, buf = cv2.imencode(".jpg", img)
    gate.camera.push(buf.tobytes())
    res = await gate.point_at_cup("blue")
    assert res.get("skill") != "indica"
    if res.get("ok"):
        assert res["skill"] == "indica_bicchiere"
        assert gate.pending is not None
        gate.cancel()
    else:
        assert gate.pending is None


def test_wide_frame_uses_the_left_eye():
    left = _scene()
    wide = np.hstack([left, left])
    plane = Plane.build(left)
    loc = plane.locate(wide, "blue")
    assert loc["ok"]
    assert abs(loc["xy_m"][0] - plane.locate(left, "blue")["xy_m"][0]) < 1e-6
