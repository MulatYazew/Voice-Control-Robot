"""Deterministic computer vision for label inspection (OpenCV).

Pipeline per photo of ONE pack face:
  1. find_face()      — locate the pack against the table background, deskew, normalise size
  2. quality()        — sharpness / exposure / coverage (bad image -> REVIEW_REQUIRED, never COMPLIANT)
  3. detect_labels()  — label regions = areas whose colour differs from the pack's base colour
  4. crop_label()     — deskewed crop of one label
  5. compare_crops()  — align product crop to the reference crop (ECC), residual -> creases / bubbles / damage

All geometry is RELATIVE to the normalised face (fractions of width/height, degrees), because the camera is
uncalibrated (config/camera.yaml). Nothing here is trained per product.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import cv2
import numpy as np

FACE_W = 800  # normalised face width (px)


@dataclass
class Face:
    image: np.ndarray          # BGR, normalised (FACE_W wide)
    found: bool
    area_frac: float           # pack area / frame area
    angle: float               # in-plane rotation of the pack in the photo (deg)
    bg_lab: np.ndarray | None = None   # table/background colour (Lab)


@dataclass
class LabelBox:
    cx: float                  # centre, fraction of face width
    cy: float                  # centre, fraction of face height
    w: float                   # fraction of face width (long side as seen upright)
    h: float
    angle: float               # deg, upright = 0, counter-clockwise positive
    area_frac: float           # of face area
    rect: tuple = field(repr=False, default=())  # raw cv2 minAreaRect in face px

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("rect", None)
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}


def load(path_or_bytes: str | bytes) -> np.ndarray:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(path_or_bytes, np.uint8), cv2.IMREAD_COLOR)
    else:
        img = cv2.imread(str(path_or_bytes), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("immagine non leggibile / unreadable image")
    return img


def _lab(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)


def _border_color(lab: np.ndarray, frac: float = 0.03) -> np.ndarray:
    h, w = lab.shape[:2]
    b = max(2, int(min(h, w) * frac))
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3), lab[:, :b].reshape(-1, 3),
                             lab[:, -b:].reshape(-1, 3)])
    return np.median(border, axis=0)


def find_face(img: np.ndarray, min_area_frac: float = 0.05) -> Face:
    """Locate the pack (largest region differing from the table/background) and warp it upright."""
    lab = _lab(img)
    bg = _border_color(lab)
    dist = cv2.GaussianBlur(np.linalg.norm(lab - bg, axis=2), (0, 0), 2)
    H0, W0 = img.shape[:2]
    k = max(3, int(min(H0, W0) * 0.01) | 1)
    # Several contrast thresholds: a pale pack on a similar table only separates at a low threshold.
    # Keep the LARGEST pack-shaped region (well-filled rectangle, not touching 2+ frame borders = background).
    best, best_area = None, 0.0
    for thr in (25, 16, 10, 7):
        mask = (dist > thr).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cand in cnts:
            a = cv2.contourArea(cand)
            x, y, w, h = cv2.boundingRect(cand)
            touches = (x <= 2) + (y <= 2) + (x + w >= W0 - 2) + (y + h >= H0 - 2)
            (_, _), (rw, rh), _ = cv2.minAreaRect(cand)
            fill = a / max(rw * rh, 1)
            if touches >= 2 or fill < 0.85 or a <= best_area:
                continue
            best, best_area = cand, a
    if best is None:
        return Face(cv2.resize(img, (FACE_W, int(FACE_W * img.shape[0] / img.shape[1]))), False, 0.0, 0.0)
    c = best
    area_frac = cv2.contourArea(c) / float(img.shape[0] * img.shape[1])
    if area_frac < min_area_frac:
        return Face(cv2.resize(img, (FACE_W, int(FACE_W * img.shape[0] / img.shape[1]))), False, area_frac, 0.0)
    box = cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32)
    # Corner order from geometry (valid for packs placed within ±45° of upright):
    # the two highest points are the top edge, then left/right by x.
    by_y = box[np.argsort(box[:, 1])]
    top, bot = by_y[:2][np.argsort(by_y[:2, 0])], by_y[2:][np.argsort(by_y[2:, 0])]
    tl, tr, bl, br = top[0], top[1], bot[0], bot[1]
    src = np.array([tl, tr, br, bl], np.float32)
    w = float(np.linalg.norm(tr - tl))
    h = float(np.linalg.norm(bl - tl))
    ang = float(np.degrees(np.arctan2(tr[1] - tl[1], tr[0] - tl[0])))
    out_h = int(round(FACE_W * h / max(w, 1)))
    dst = np.array([[0, 0], [FACE_W - 1, 0], [FACE_W - 1, out_h - 1], [0, out_h - 1]], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    face = cv2.warpPerspective(img, M, (FACE_W, out_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    # trim 1.5% so no strip of table/background remains at the edges, then restore the normalised width
    t = int(0.015 * FACE_W)
    face = cv2.resize(face[t:out_h - t, t:FACE_W - t], (FACE_W, int(round((out_h - 2 * t) * FACE_W / (FACE_W - 2 * t)))),
                      interpolation=cv2.INTER_CUBIC)
    return Face(face, True, area_frac, float(ang), bg)


def quality(face: Face, cfg: dict[str, Any], photo: np.ndarray | None = None) -> dict[str, Any]:
    g = cv2.cvtColor(face.image, cv2.COLOR_BGR2GRAY)
    # exposure on the WHOLE photo: a black pack is not an underexposed image
    pg = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY) if photo is not None else g
    clipped_hi, clipped_lo = float((pg >= 250).mean()), float((pg <= 5).mean())
    p5, p95 = np.percentile(pg, 5), np.percentile(pg, 95)
    sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
    bright = float(g.mean())
    issues = []
    if not face.found:
        issues.append("pack_not_found")
    if face.area_frac < cfg["min_face_area_frac"]:
        issues.append("pack_too_small")
    # NOTE: sharpness is NOT judged on the whole face (a plain face has little texture even when sharp);
    # it is judged on each label crop, where text must be read: see label_sharpness().
    if clipped_hi > 0.25 or clipped_lo > 0.25 or not (cfg["min_brightness"] <= float(pg.mean()) <= cfg["max_brightness"]):
        issues.append("bad_exposure")
    return {"ok": not issues, "issues": issues, "sharpness": round(sharp, 1), "brightness": round(bright, 1),
            "pack_area_frac": round(face.area_frac, 3)}


def label_sharpness(crop: np.ndarray) -> float:
    """Variance of the Laplacian on a label crop (text present -> meaningful blur measure)."""
    g = cv2.cvtColor(cv2.resize(crop, (600, max(40, int(600 * crop.shape[0] / max(crop.shape[1], 1))))), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def _pack_color(lab: np.ndarray, bg_lab: np.ndarray | None = None) -> np.ndarray:
    """Base colour of the pack = median of a ring just inside the face border, ignoring background-like pixels."""
    h, w = lab.shape[:2]
    b = max(2, int(min(h, w) * 0.05))
    ring = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3), lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    if bg_lab is not None:
        keep = np.linalg.norm(ring - bg_lab, axis=1) > 20
        if keep.mean() > 0.3:
            ring = ring[keep]
    return np.median(ring, axis=0)


def _upright(box: np.ndarray) -> dict[str, float]:
    """Geometry of a rotated rectangle seen 'upright' (valid within ±45°): top edge = width."""
    box = np.asarray(box, np.float32)
    by_y = box[np.argsort(box[:, 1])]
    top, bot = by_y[:2][np.argsort(by_y[:2, 0])], by_y[2:][np.argsort(by_y[2:, 0])]
    tl, tr, bl = top[0], top[1], bot[0]
    return {"cx": float(box[:, 0].mean()), "cy": float(box[:, 1].mean()), "w": float(np.linalg.norm(tr - tl)),
            "h": float(np.linalg.norm(bl - tl)), "angle": float(np.degrees(np.arctan2(tr[1] - tl[1], tr[0] - tl[0])))}


def detect_labels(face_img: np.ndarray, min_area_frac: float, color_distance: float,
                  bg_lab: np.ndarray | None = None) -> list[LabelBox]:
    lab = _lab(face_img)
    base = _pack_color(lab, bg_lab)
    dist = np.linalg.norm(lab - base, axis=2)
    mask = (dist > color_distance).astype(np.uint8) * 255
    h, w = mask.shape
    # labels are solid regions: close text gaps, remove noise
    k = max(5, int(w * 0.02) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[LabelBox] = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area_frac * w * h:
            continue
        # labels are quadrilateral-ish: fill ratio vs their min-area rectangle
        rect = cv2.minAreaRect(c)
        (_, _), (rw, rh), _ = rect
        if rw * rh <= 0 or a / (rw * rh) < 0.75:
            continue
        g = _upright(cv2.boxPoints(rect))
        # OpenCV y axis points down: report counter-clockwise positive
        out.append(LabelBox(g["cx"] / w, g["cy"] / h, g["w"] / w, g["h"] / h, -g["angle"], a / (w * h), rect))
    out.sort(key=lambda b: -b.area_frac)
    return out


def crop_label(face_img: np.ndarray, box: LabelBox, pad: float = 0.0) -> np.ndarray:
    """Deskewed upright crop of a detected label (rotation removed, original orientation kept)."""
    H, W = face_img.shape[:2]
    cx, cy = box.cx * W, box.cy * H
    M = cv2.getRotationMatrix2D((cx, cy), -box.angle, 1.0)   # undo the label's CCW rotation
    rot = cv2.warpAffine(face_img, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    w, h = int(box.w * W * (1 + pad)), int(box.h * H * (1 + pad))
    return cv2.getRectSubPix(rot, (max(w, 2), max(h, 2)), (cx, cy))


def ocr_crop(face_img: np.ndarray, box: LabelBox) -> np.ndarray:
    """Crop slightly INSIDE the label edge so the cut/printed border is not read as text."""
    return crop_label(face_img, box, pad=-0.05)


def _norm_gray(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    g = cv2.cvtColor(cv2.resize(img, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return (g - g.mean()) / (g.std() + 1e-6)


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised cross-correlation of two label crops (structure similarity, -1..1)."""
    size = (240, max(24, int(240 * a.shape[0] / max(a.shape[1], 1))))
    return float((_norm_gray(a, size) * _norm_gray(b, size)).mean())


def compare_crops(ref: np.ndarray, prod: np.ndarray, cfg: dict[str, Any]) -> dict[str, Any]:
    """Align the product label crop to the reference crop and look for local differences
    (creases = long thin residual structures; bubbles = compact blobs; other = damage/print)."""
    H, W = ref.shape[:2]
    p = cv2.resize(prod, (W, H), interpolation=cv2.INTER_AREA)
    rg = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)
    pg = cv2.cvtColor(p, cv2.COLOR_BGR2GRAY).astype(np.float32)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(rg, pg, warp, cv2.MOTION_AFFINE,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-4), None, 5)
        pg = cv2.warpAffine(pg, warp, (W, H), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
        aligned = True
    except cv2.error:
        aligned = False
    # photometric normalisation (lighting differences)
    pg = (pg - pg.mean()) / (pg.std() + 1e-6) * rg.std() + rg.mean()
    resid = np.abs(cv2.GaussianBlur(pg, (5, 5), 0) - cv2.GaussianBlur(rg, (5, 5), 0))
    m = int(min(H, W) * 0.05)                     # ignore the cut edge of the label
    resid[:m], resid[-m:], resid[:, :m], resid[:, -m:] = 0, 0, 0, 0
    bw = (resid > cfg["residual_threshold"]).astype(np.uint8) * 255
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw)
    area_lbl = float(H * W)
    defects = []
    total = 0.0
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < cfg["min_blob_area_frac"] * area_lbl:
            continue
        ys, xs = np.where(lab == i)
        pts = np.column_stack([xs, ys]).astype(np.float32)
        (_, _), (rw, rh), _ = cv2.minAreaRect(pts)
        elong = max(rw, rh) / max(1.0, min(rw, rh))
        kind = "crease" if elong >= cfg["crease_min_elongation"] else ("bubble" if elong < 2.2 else "damage")
        total += a
        defects.append({"kind": kind, "area_frac": round(a / area_lbl, 5), "elongation": round(elong, 1),
                        "bbox": [int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]), int(stats[i, 3])]})
    return {"aligned": aligned, "defect_area_frac": round(total / area_lbl, 5), "defects": defects,
            "ncc": round(ncc(ref, prod), 3)}


def encode_jpeg(img: np.ndarray, quality_: int = 88) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality_])
    if not ok:
        raise ValueError("jpeg encode failed")
    return buf.tobytes()
