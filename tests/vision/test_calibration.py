import numpy as np
import pytest

import scene
from grokarm_vision.calibration import CalPoint, Calibration, CalibrationError, estimate_drift_px


def _points(raw):
    return [CalPoint(tuple(p["px"]), tuple(p["robot_mm"])) for p in raw]


def test_fit_maps_pixels_to_robot_mm():
    cal = Calibration.fit(_points(scene.calibration_points()), (scene.W, scene.H))
    for x, y in [(200, 100), (400, -150), (300, 0)]:
        got = cal.to_mm(*scene.mm_to_px(x, y))
        assert np.hypot(got[0] - x, got[1] - y) < 0.5
    assert cal.loo_max_mm is not None and cal.loo_max_mm < 1.0


def test_four_points_fit_but_accuracy_is_unmeasured():
    cal = Calibration.fit(_points(scene.calibration_points()[:4]), (scene.W, scene.H))
    assert cal.loo_max_mm is None
    assert cal.summary()["accuracy_measured"] is False


def test_mistyped_point_is_rejected():
    raw = scene.calibration_points()
    raw[4]["robot_mm"] = [300, 60]
    with pytest.raises(CalibrationError, match="leave-one-out"):
        Calibration.fit(_points(raw), (scene.W, scene.H))


def test_too_few_or_collinear_points_rejected():
    raw = scene.calibration_points()
    with pytest.raises(CalibrationError):
        Calibration.fit(_points(raw[:3]), (scene.W, scene.H))
    line = [{"px": [100 + 50 * i, 100 + 20 * i], "robot_mm": [100 + 10 * i, 4 * i]} for i in range(5)]
    with pytest.raises(CalibrationError, match="collinear"):
        Calibration.fit(_points(line), (scene.W, scene.H))


def test_workspace_is_hull_with_margin():
    cal = Calibration.fit(_points(scene.calibration_points()), (scene.W, scene.H))
    assert cal.inside_workspace((300, 0), 10)
    assert not cal.inside_workspace((430, 215), 10)
    assert not cal.inside_workspace((500, 0), 10)


def test_save_and_load_roundtrip(tmp_path):
    cal = Calibration.fit(_points(scene.calibration_points()), (scene.W, scene.H))
    cal.save(tmp_path / "c.json")
    again = Calibration.load(tmp_path / "c.json")
    assert again is not None
    assert np.allclose(again.homography, cal.homography)


def test_drift_detects_camera_shift_but_not_new_objects():
    bg = scene.table()
    with_objects = scene.with_objects(bg, [("red", 300, 0), ("blue", 250, 150)])
    still = estimate_drift_px(bg, with_objects)
    assert still is not None and still < 3
    moved = estimate_drift_px(bg, scene.shifted(bg, 18, -9))
    assert moved is not None and moved > 12
