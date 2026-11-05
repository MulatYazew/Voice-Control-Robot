"""One RGB camera and the printed marker recover a standing cup's XYZ, including its height."""

import cv2
import numpy as np
import pytest

from app.vision.height import HeightError, pose_from_marker, standing_xyz
from app.vision.marker import object_xy


def _camera():
    position = np.array([0.05, -0.40, 0.55])
    target = np.array([0.12, 0.0, 0.05])
    forward = target - position
    forward /= np.linalg.norm(forward)
    image_x = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    image_x /= np.linalg.norm(image_x)
    image_y = np.cross(forward, image_x)
    world_from_camera = np.column_stack([image_x, image_y, forward])
    rotation = world_from_camera.T
    translation = -rotation @ position
    rvec, _ = cv2.Rodrigues(rotation)
    matrix = np.array([[900.0, 0.0, 320.0], [0.0, 900.0, 240.0], [0.0, 0.0, 1.0]])
    return rvec, translation, matrix


def _project(points, rvec, translation, matrix):
    img, _ = cv2.projectPoints(np.asarray(points, dtype=np.float64), rvec, translation, matrix, np.zeros(5))
    return img.reshape(-1, 2)


@pytest.mark.parametrize("height", [0.08, 0.20, 0.30])
def test_standing_cup_height_comes_back(height):
    rvec, translation, matrix = _camera()
    corners = _project(np.hstack([object_xy(0.05), np.zeros((4, 1))]), rvec, translation, matrix)
    got_r, got_t = pose_from_marker(corners, 0.05, matrix)
    base = np.array([0.18, 0.04, 0.0])
    rim = np.array([0.18, 0.04, height])
    pixels = _project(np.vstack([base, rim]), rvec, translation, matrix)
    x, y, z = standing_xyz(pixels[0], pixels[1], got_r, got_t, matrix)
    assert abs(x - 0.18) < 0.002
    assert abs(y - 0.04) < 0.002
    assert abs(z - height) < 0.003


def test_a_flat_mark_has_no_cup_height():
    rvec, translation, matrix = _camera()
    corners = _project(np.hstack([object_xy(0.05), np.zeros((4, 1))]), rvec, translation, matrix)
    got_r, got_t = pose_from_marker(corners, 0.05, matrix)
    pixel = _project([[0.18, 0.04, 0.0]], rvec, translation, matrix)[0]
    with pytest.raises(HeightError):
        standing_xyz(pixel, pixel, got_r, got_t, matrix)
