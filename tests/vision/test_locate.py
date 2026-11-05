import numpy as np
import pytest

import scene
from grokarm_vision.detect import find_candidates
from grokarm_vision.service import VisionService


def put(service: VisionService, objects):
    service.store.put_image(scene.with_objects(scene.table(), objects))


def test_detects_colours():
    bg = scene.table()
    img = scene.with_objects(bg, [(c, 300, y) for c, y in
                                  [("red", 180), ("blue", 90), ("green", 0), ("yellow", -90), ("black", -180)]])
    found = find_candidates(img, bg)
    assert sorted(c.colour for c in found) == sorted(["red", "blue", "green", "yellow", "black"])


@pytest.mark.parametrize(
    "query,lang",
    [
        ("point at the red object", "en"),
        ("indica l'oggetto rosso", "it"),
        ("指向红色的那个", "zh"),
        ("señala el objeto rojo", "es"),
        ("montre l'objet rouge", "fr"),
        ("zeig auf das rote Objekt", "de"),
        ("赤い物を指して", "ja"),
    ],
)
def test_finds_red_object_in_seven_languages(ready_service, query, lang):
    put(ready_service, [("red", 350, 100), ("blue", 250, -120)])
    result = ready_service.locate(query, lang)
    assert result.ok, result.message
    assert result.status == "found"
    assert np.hypot(result.target_mm[0] - 350, result.target_mm[1] - 100) < 5
    assert result.lang == lang
    assert result.message


def test_single_object_resolves_without_colour(ready_service):
    put(ready_service, [("green", 300, 50)])
    result = ready_service.locate("point at that object", "en")
    assert result.ok
    assert abs(result.target_mm[0] - 300) < 5


def test_two_same_colour_objects_ask_which(ready_service):
    put(ready_service, [("red", 320, 140), ("red", 320, -140)])
    result = ready_service.locate("indica quello rosso", "it")
    assert not result.ok
    assert result.status == "ambiguous"
    assert len(result.resolution["ids"]) == 2
    assert "sinistra" in result.message and "destra" in result.message


def test_position_word_breaks_the_tie(ready_service):
    put(ready_service, [("red", 320, 140), ("red", 320, -140)])
    left = ready_service.locate("the red one on the left", "en")
    right = ready_service.locate("右边红色的", "zh")
    assert left.ok and left.target_mm[1] > 100
    assert right.ok and right.target_mm[1] < -100


def test_missing_colour_is_not_found(ready_service):
    put(ready_service, [("blue", 300, 0)])
    result = ready_service.locate("point at the yellow one", "en")
    assert result.status == "not_found"
    assert not result.ok


def test_empty_table(ready_service):
    ready_service.store.put_image(scene.table())
    assert ready_service.locate("the red one", "en").status == "empty_table"


def test_outside_calibrated_area_is_refused(ready_service):
    put(ready_service, [("red", 470, 0)])
    result = ready_service.locate("the red one", "en")
    assert result.status == "outside_workspace"
    assert not result.ok
    assert result.target_mm is not None


def test_camera_moved_is_refused(ready_service):
    img = scene.with_objects(scene.table(), [("red", 300, 0)])
    ready_service.store.put_image(scene.shifted(img, 20, 10))
    result = ready_service.locate("the red one", "en")
    assert result.status == "camera_moved"
    assert not result.ok and result.target_mm is None


def test_target_moved_between_steps_is_refused(ready_service):
    put(ready_service, [("red", 300, 0)])
    first = ready_service.locate("the red one", "en")
    put(ready_service, [("red", 300, 40)])
    second = ready_service.locate("the red one", "en", expect_mm=tuple(first.target_mm))
    assert second.status == "target_moved"
    assert second.moved_mm > 30


def test_not_ready_without_calibration(settings):
    service = VisionService(settings)
    service.store.put_image(scene.table())
    result = service.locate("the red one", "zh")
    assert result.status == "not_ready"
    assert "not calibrated" in result.message


def test_no_frame(settings):
    result = VisionService(settings).locate("the red one", "en")
    assert result.status == "no_frame"


def test_resolution_change_invalidates_calibration(ready_service):
    import cv2

    small = cv2.resize(scene.with_objects(scene.table(), [("red", 300, 0)]), (640, 360))
    ready_service.store.put_image(small)
    result = ready_service.locate("the red one", "en")
    assert result.status == "not_ready"


def test_state_survives_restart(ready_service, settings):
    again = VisionService(settings)
    assert again.calibration is not None and again.background is not None and again.reference is not None
    again.store.put_image(scene.with_objects(scene.table(), [("blue", 260, -60)]))
    assert again.locate("blue", "en").ok
