"""One printed ArUco is the table plane. This is the fixed-camera method used by the
open-source RGB pick pipelines (OpenCV ArUco corners, a homography onto the plane,
then a hover and a straight descent). No depth camera and no gripper.

DICT_4X4_50, id 0. The black square is ``size_m`` on a side. OpenCV lists the corners
as top-left, top-right, bottom-right, bottom-left of this generated image, which is
marker XY (-s/2, +s/2), (+s/2, +s/2), (+s/2, -s/2), (-s/2, -s/2). +X points at the
right edge (the arrow). +Y points at the top edge. Z comes up out of the table.
"""

from __future__ import annotations

import cv2
import numpy as np

MARKER_ID = 0
DICT = cv2.aruco.DICT_4X4_50


class MarkerFitError(ValueError):
    pass


def object_xy(size_m: float) -> np.ndarray:
    s = float(size_m) / 2.0
    return np.array([[-s, s], [s, s], [s, -s], [-s, -s]], dtype=np.float64)


def board_bgr(side: int = 400, quiet: int = 40) -> np.ndarray:
    dictionary = cv2.aruco.getPredefinedDictionary(DICT)
    marker = cv2.aruco.generateImageMarker(dictionary, MARKER_ID, side)
    canvas = np.full((side + 2 * quiet, side + 2 * quiet), 255, np.uint8)
    canvas[quiet:quiet + side, quiet:quiet + side] = marker
    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)


def write_print_png(path, size_m: float = 0.05, dpi: int = 300) -> None:
    """A4-ready sheet. Printed at actual size, the black square is ``size_m`` on a side."""
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont

    side = int(round(float(size_m) / 0.0254 * dpi))
    quiet = int(round(0.012 / 0.0254 * dpi))
    board = board_bgr(side, quiet)
    arrow = int(round(0.030 / 0.0254 * dpi))
    caption_h = int(round(0.016 / 0.0254 * dpi))
    h, w = board.shape[:2]
    canvas = np.full((h + caption_h, w + arrow, 3), 255, np.uint8)
    canvas[:h, :w] = board
    tip = (quiet + side, quiet + side // 2)
    start = (w + arrow - int(round(0.004 / 0.0254 * dpi)), quiet + side // 2)
    cv2.arrowedLine(canvas, start, tip, (30, 30, 210), max(3, dpi // 70), tipLength=0.22)
    image = Image.fromarray(canvas[:, :, ::-1])
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", int(round(0.0036 / 0.0254 * dpi)))
    mm = int(round(float(size_m) * 1000))
    draw.text((quiet, h + int(caption_h * 0.25)),
              f"黑方块边长 {mm} 毫米。按实际大小打印，不要铺满页面。箭头指向黑方块右边的中点。",
              fill=(20, 20, 20), font=font)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path, dpi=(dpi, dpi))


def sheet_png(size_m: float = 0.05) -> bytes:
    """Printable sheet. The arrow tip is the middle of the black square's right edge."""
    board = board_bgr()
    h, w = board.shape[:2]
    extra = 170
    canvas = np.full((h + 36, w + extra, 3), 255, np.uint8)
    canvas[:h, :w] = board
    tip = (w - 40, h // 2)          # quiet zone is 40 px, so this is the right edge
    start = (w + extra - 30, h // 2)
    cv2.arrowedLine(canvas, start, tip, (30, 30, 210), 4, tipLength=0.22)
    mm = int(round(float(size_m) * 1000))
    cv2.putText(canvas, f"{mm} mm", (40, h + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise RuntimeError("marker png")
    return buf.tobytes()


def detect(image: np.ndarray) -> dict | None:
    """The id-0 marker, or None. Corners follow ``object_xy`` order."""
    if image is None or image.size == 0:
        return None
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dictionary = cv2.aruco.getPredefinedDictionary(DICT)
    corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters()).detectMarkers(gray)
    if ids is None:
        return None
    hit = np.where(ids.flatten() == MARKER_ID)[0]
    if len(hit) == 0:
        return None
    c = np.asarray(corners[int(hit[0])][0], dtype=np.float64)
    return {"corners": c, "center_px": c.mean(axis=0)}


def _basis(origin_xy: np.ndarray, axis_xy: np.ndarray, size_m: float) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(axis_xy, dtype=np.float64) - np.asarray(origin_xy, dtype=np.float64)
    length = float(np.linalg.norm(v))
    expect = float(size_m) / 2.0
    if not (0.55 * expect <= length <= 1.45 * expect):
        raise MarkerFitError("length")
    vx = v / length
    vy = np.array([-vx[1], vx[0]])
    return vx, vy


def pairs_for_plane(corners: np.ndarray, origin_xy, axis_xy, size_m: float
                    ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The four marker corners only. They fix the pixel-to-table map.

    They are not the working area. The area is the arm's own reach on the table.
    """
    origin = np.asarray(origin_xy, dtype=np.float64)
    vx, vy = _basis(origin, axis_xy, size_m)
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for px, (mx, my) in zip(np.asarray(corners, dtype=np.float64), object_xy(size_m)):
        base = origin + mx * vx + my * vy
        pairs.append(((float(px[0]), float(px[1])), (float(base[0]), float(base[1]))))
    return pairs
