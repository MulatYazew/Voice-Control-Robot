"""Paper cups seen by the camera -> which workspace zone each one is in.

Two detectors:
  colour      HSV segmentation of saturated colours; no background photo needed.
  background  difference against a photo of the empty table; finds cups of ANY colour
              (white, grey, black too). Used automatically once a background is captured.
Optional Grok Vision fallback when the named colour is not found locally. The image is split
into the configured zones by vertical lines, so the arm can point at a cup with
the existing taught `indica` poses: no new motion, no coordinates from the model.
Read-only: never moves the robot.
"""

from __future__ import annotations

import base64
import json
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

import cv2
import httpx
import numpy as np

from grokarm_vision.detect import find_candidates

# OpenCV hue is 0..179
COLOURS: dict[str, list[tuple[int, int]]] = {
    "red": [(0, 8), (170, 179)],
    "orange": [(9, 22)],
    "yellow": [(23, 42)],
    "green": [(43, 85)],
    "blue": [(90, 128)],
    "purple": [(129, 169)],
}
# Vivid cups only. Skin sits in the same hues as orange/yellow but with low saturation.
COLOUR_SV: dict[str, tuple[int, int]] = {
    "red": (80, 50), "orange": (150, 80), "yellow": (90, 80),
    "green": (45, 40), "blue": (45, 40), "purple": (45, 40),
}
ALL_COLOURS = list(COLOURS) + ["pink", "white", "grey", "black"]
BGR = {"red": (40, 40, 230), "orange": (0, 140, 255), "yellow": (0, 220, 240), "green": (60, 200, 60),
       "blue": (230, 120, 30), "purple": (200, 60, 170), "pink": (180, 105, 255), "white": (250, 250, 250),
       "grey": (160, 160, 160), "black": (20, 20, 20)}
LAB_MAX = 32.0
_LAB_REF = {name: cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2LAB).astype(np.float32)[0, 0]
            for name, bgr in BGR.items()}


def nearest_lab(median_lab: np.ndarray) -> tuple[str, float]:
    """Closest swatch in OpenCV Lab. Highlights with L above 245 are ignored by the caller."""
    best, dist = "grey", 1e9
    for name, ref in _LAB_REF.items():
        d = float(np.linalg.norm(np.asarray(median_lab, dtype=np.float32) - ref))
        if d < dist:
            best, dist = name, d
    return best, dist


def lab_mask(image: np.ndarray, colour: str, max_de: float = LAB_MAX) -> np.ndarray | None:
    """Pixels near this colour in Lab, excluding blown-out white."""
    if colour not in _LAB_REF:
        return None
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    dist = np.linalg.norm(lab - _LAB_REF[colour], axis=2)
    return ((dist <= max_de) & (lab[:, :, 0] < 245)).astype(np.uint8) * 255

COLOUR_WORDS: dict[str, list[str]] = {
    "red": ["red", "rosso", "rossa", "rossi", "rojo", "roja", "rouge", "rot", "rote", "roten", "红", "紅", "赤"],
    "orange": ["orange", "arancione", "arancio", "naranja", "orangen", "橙", "橘", "オレンジ"],
    "yellow": ["yellow", "giallo", "gialla", "amarillo", "amarilla", "jaune", "gelb", "gelbe", "gelben", "黄", "黃"],
    "green": ["green", "verde", "vert", "verte", "grun", "grune", "grunen", "绿", "綠", "緑"],
    "blue": ["blue", "blu", "azzurro", "azzurra", "azul", "bleu", "bleue", "blau", "blaue", "blauen", "蓝", "藍", "青"],
    "purple": ["purple", "viola", "morado", "morada", "violet", "violette", "lila", "紫"],
    "pink": ["pink", "rosa", "rose", "粉", "ピンク"],
    "white": ["white", "bianco", "bianca", "blanco", "blanca", "blanc", "blanche", "weiss", "weisse", "weissen", "白"],
    "grey": ["grey", "gray", "grigio", "grigia", "gris", "grise", "grau", "graue", "grauen", "灰", "グレー"],
    "black": ["black", "nero", "nera", "negro", "negra", "noir", "noire", "schwarz", "schwarze", "schwarzen", "黑",
              "黒"],
}
_CJK = "\u3040-\u30ff\u3400-\u9fff"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", str(text)).lower()
    return "".join("".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
                   if c < "\u0250" else c for c in t)


def colour_of(text: str | None) -> str | None:
    """'il bicchiere rosso' -> 'red'; '蓝色的纸杯' -> 'blue'. None if no (or more than one) colour is named."""
    if not text:
        return None
    t = _norm(text)
    words = set("".join(c if c.isalnum() else " " for c in t).split())
    found = set()
    for canon, names in COLOUR_WORDS.items():
        for n in names:
            if (n in t) if any("\u3040" <= ch <= "\u9fff" or "\u30a0" <= ch <= "\u30ff" for ch in n) else (n in words):
                found.add(canon)
    return found.pop() if len(found) == 1 else None


@dataclass
class Cup:
    colour: str
    px: tuple[float, float]
    bbox: tuple[int, int, int, int]
    area_frac: float
    zone: str | None

    def public(self) -> dict[str, Any]:
        return {"colore": self.colour, "zona": self.zone, "px": [round(self.px[0]), round(self.px[1])],
                "bbox": list(self.bbox), "area_frac": round(self.area_frac, 4)}


def zone_bounds(zones: list[str], cfg: dict[str, Any]) -> list[tuple[str, float, float]]:
    """Zones left-to-right *as seen in the image*, as (zone, x0, x1) fractions of the width."""
    order = list(cfg.get("image_zone_order") or zones)
    cuts = list(cfg.get("zone_cuts") or [i / len(order) for i in range(1, len(order))])
    edges = [0.0] + [float(c) for c in cuts] + [1.0]
    return [(z, edges[i], edges[i + 1]) for i, z in enumerate(order)]


def find_cups(image: np.ndarray, zones: list[str], cfg: dict[str, Any]) -> list[Cup]:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = cfg.get("roi") or [0.0, 0.0, 1.0, 1.0]
    rx0, ry0, rx1, ry1 = int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)
    roi = image[ry0:ry1, rx0:rx1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    ycrcb = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
    # Skin (face, hands) shares orange/yellow hue. Keep it out unless it is vividly saturated.
    skin = cv2.bitwise_and(
        cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127)),
        cv2.inRange(hsv, (0, 0, 40), (40, 190, 255)))
    s_min, v_min = int(cfg.get("min_saturation", 50)), int(cfg.get("min_value", 40))
    min_area = float(cfg.get("min_area_frac", 0.002)) * w * h
    max_area = float(cfg.get("max_area_frac", 0.25)) * w * h
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bounds = zone_bounds(zones, cfg)
    cups: list[Cup] = []
    masks: list[tuple[str, np.ndarray]] = []
    for colour, ranges in COLOURS.items():
        cs, cv = COLOUR_SV.get(colour, (s_min, v_min))
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in ranges:
            mask |= cv2.inRange(hsv, (lo, cs, cv), (hi, 255, 255))
        if colour in ("orange", "yellow", "red"):
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(skin))
        masks.append((colour, mask))
    # Black cups are dark, not saturated, so the colour ranges never see them.
    masks.append(("black", cv2.inRange(hsv, (0, 0, 0), (179, 110, 65))))
    for colour, mask in masks:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for i in range(1, n):
            bx, by, bw, bh, area = (int(v) for v in stats[i])
            if not (min_area <= area <= max_area):
                continue
            if bw / max(bh, 1) > 4 or bh / max(bw, 1) > 4:      # stripes, cables, table edges
                continue
            cx, cy = float(cents[i][0]) + rx0, float(cents[i][1]) + ry0
            fx = cx / w
            zone = next((z for z, a, b in bounds if a <= fx < b), bounds[-1][0])
            cups.append(Cup(colour, (cx, cy), (bx + rx0, by + ry0, bw, bh), area / (w * h), zone))
    cups.extend(_pale_cups(roi, hsv, skin, masks, rx0, ry0, w, h, min_area, max_area, bounds))
    cups.sort(key=lambda c: c.px[0])
    return cups


def _pale_cups(roi: np.ndarray, hsv: np.ndarray, skin: np.ndarray, masks: list[tuple[str, np.ndarray]],
               rx0: int, ry0: int, w: int, h: int, min_area: float, max_area: float,
               bounds: list[tuple[str, float, float]]) -> list[Cup]:
    """Name unsaturated patches by Lab, so a pale cup is white, grey or its hue instead of invisible."""
    taken = np.zeros(hsv.shape[:2], np.uint8)
    for _, mask in masks:
        taken = cv2.bitwise_or(taken, mask)
    free = cv2.bitwise_and(cv2.bitwise_not(taken), cv2.bitwise_not(skin))
    loose = cv2.bitwise_and(free, cv2.inRange(hsv, (0, 12, 35), (179, 49, 250)))
    found: list[Cup] = []
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    for colour, ranges in COLOURS.items():
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in ranges:
            mask |= cv2.inRange(hsv, (lo, 12, 35), (hi, 49, 250))
        mask = cv2.bitwise_and(cv2.morphologyEx(cv2.bitwise_and(mask, loose), cv2.MORPH_OPEN, kernel), free)
        found.extend(_cups_from_mask(mask, roi, colour, True, rx0, ry0, w, h, min_area, max_area, bounds))
    for colour in ("white", "grey", "pink"):
        named = lab_mask(roi, colour)
        if named is None:
            continue
        mask = cv2.bitwise_and(cv2.morphologyEx(cv2.bitwise_and(named, free), cv2.MORPH_OPEN, kernel), free)
        found.extend(_cups_from_mask(mask, roi, colour, False, rx0, ry0, w, h, min_area, max_area, bounds))
    found.extend(_rim_cups(roi, rx0, ry0, w, h, min_area, max_area, bounds, found))
    return found


def _rim_cups(roi: np.ndarray, rx0: int, ry0: int, w: int, h: int, min_area: float, max_area: float,
              bounds: list[tuple[str, float, float]], already: list[Cup]) -> list[Cup]:
    """A pale cup on a pale table has almost no colour. Its rim still has local contrast."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hp = cv2.absdiff(gray, cv2.GaussianBlur(gray, (31, 31), 0))
    _, th = cv2.threshold(hp, 10, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel)
    th[: int(th.shape[0] * 0.2), :] = 0
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cups: list[Cup] = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if not (min_area <= area <= max_area):
            continue
        x, y, bw, bh = (int(v) for v in cv2.boundingRect(cnt))
        if bw < 12 or bh < 12 or bw / max(bh, 1) > 2.2 or bh / max(bw, 1) > 2.2:
            continue
        cx, cy = x + bw / 2.0, y + bh / 2.0
        if any(abs(c.px[0] - (cx + rx0)) < bw and abs(c.px[1] - (cy + ry0)) < bh for c in already + cups):
            continue
        patch = roi[y:y + bh, x:x + bw]
        if patch.size == 0:
            continue
        med = np.median(cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).reshape(-1, 3), axis=0)
        if float(med[0]) > 245 or float(med[0]) < 160:
            continue
        sat = float(np.median(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1]))
        if sat > 40:
            continue
        name, dist = nearest_lab(med)
        if dist > 48:
            continue
        cups.append(Cup(name, (cx + rx0, cy + ry0), (x + rx0, y + ry0, bw, bh), area / (w * h),
                        _zone_of((cx + rx0) / w, bounds)))
    return cups


def _cups_from_mask(mask: np.ndarray, roi: np.ndarray, colour: str, check_lab: bool,
                    rx0: int, ry0: int, w: int, h: int, min_area: float, max_area: float,
                    bounds: list[tuple[str, float, float]]) -> list[Cup]:
    cups: list[Cup] = []
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, n):
        bx, by, bw, bh, area = (int(v) for v in stats[i])
        if not (min_area <= area <= max_area) or bw / max(bh, 1) > 4 or bh / max(bw, 1) > 4:
            continue
        if check_lab:
            patch = roi[by:by + bh, bx:bx + bw]
            if patch.size == 0:
                continue
            med = np.median(cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).reshape(-1, 3), axis=0)
            name, dist = nearest_lab(med)
            if name != colour or dist > LAB_MAX:
                continue
        cx, cy = float(cents[i][0]) + rx0, float(cents[i][1]) + ry0
        fx = cx / w
        zone = next((z for z, a, b in bounds if a <= fx < b), bounds[-1][0])
        cups.append(Cup(colour, (cx, cy), (bx + rx0, by + ry0, bw, bh), area / (w * h), zone))
    return cups


def annotate(image: np.ndarray, cups: list[Cup], zones: list[str], cfg: dict[str, Any],
             labels: dict[str, str] | None = None) -> np.ndarray:
    out = image.copy()
    h, w = out.shape[:2]
    for z, a, b in zone_bounds(zones, cfg):
        if a > 0:
            cv2.line(out, (int(a * w), 0), (int(a * w), h), (200, 200, 200), 1, cv2.LINE_AA)
        name = (labels or {}).get(z, z)
        cv2.putText(out, name, (int(a * w) + 8, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 230, 230), 2, cv2.LINE_AA)
    x0, y0, x1, y1 = cfg.get("roi") or [0.0, 0.0, 1.0, 1.0]
    if (x0, y0, x1, y1) != (0.0, 0.0, 1.0, 1.0):
        cv2.rectangle(out, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (120, 120, 120), 1)
    for c in cups:
        x, y, bw, bh = c.bbox
        col = BGR.get(c.colour, (255, 255, 255))
        cv2.rectangle(out, (x, y), (x + bw, y + bh), col, 3)
        cv2.putText(out, c.colour, (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2, cv2.LINE_AA)
    return out


def _zone_of(fx: float, bounds: list[tuple[str, float, float]]) -> str:
    return next((z for z, a, b in bounds if a <= fx < b), bounds[-1][0])


def find_cups_background(image: np.ndarray, background: np.ndarray, zones: list[str], cfg: dict[str, Any]) -> list[Cup]:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = cfg.get("roi") or [0.0, 0.0, 1.0, 1.0]
    bounds = zone_bounds(zones, cfg)
    cands = find_candidates(image, background, diff_threshold=int(cfg.get("diff_threshold", 30)),
                            min_area_frac=float(cfg.get("min_area_frac", 0.002)),
                            max_area_frac=float(cfg.get("max_area_frac", 0.25)))
    cups = []
    for c in cands:
        fx, fy = c.px[0] / w, c.px[1] / h
        if not (x0 <= fx <= x1 and y0 <= fy <= y1):
            continue
        cups.append(Cup(c.colour, c.px, c.bbox, c.area_px / (w * h), _zone_of(fx, bounds)))
    return cups


GROK_PROMPT = """You see a table split into vertical zones by thin grey lines, labelled at the bottom: {zones}.
List every paper cup (or cup-like container) standing on the table. For each give its main colour, one of
{colours}, and the zone label it stands in. Ignore the robot arm, cables and anything that is not a cup.
Return EXACTLY this JSON and nothing else: {{"cups": [{{"colour": "...", "zone": "..."}}]}}"""


def grok_find_cups(annotated: np.ndarray, zones: list[str], key: str, model: str, base_url: str,
                   transport: httpx.BaseTransport | None = None, timeout_s: float = 12.0) -> list[dict[str, str]]:
    """Grok Vision fallback. Returns [{'colore', 'zona'}] with zones and colours validated against the lists."""
    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
    body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode(),
                                            "detail": "high"}},
        {"type": "text", "text": GROK_PROMPT.format(zones=", ".join(zones), colours=", ".join(ALL_COLOURS))}]}]}
    with httpx.Client(timeout=timeout_s, transport=transport) as client:
        r = client.post(f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
        r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    text = str(text)
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    out = []
    for c in data.get("cups") or []:
        colour, zone = str(c.get("colour", "")).lower().strip(), str(c.get("zone", "")).lower().strip()
        colour = "grey" if colour == "gray" else colour
        if colour in ALL_COLOURS and zone in zones:
            out.append({"colore": colour, "zona": zone})
    return out


class CupFinder:
    def __init__(self, zones: list[str], cfg: dict[str, Any], labels: dict[str, str] | None = None) -> None:
        self.zones, self.cfg, self.labels = zones, cfg, labels or {}
        self.last: dict[str, Any] | None = None
        self.last_jpeg: bytes | None = None
        self.background: np.ndarray | None = None

    def capture_background(self, frame: Any) -> dict[str, Any]:
        self.background = frame.image.copy()
        return {"ok": True, "size": list(frame.size)}

    def clear_background(self) -> None:
        self.background = None

    @property
    def detector(self) -> str:
        return "background" if self.background is not None else "colour"

    def grok_lookup(self, frame: Any, key: str, model: str, base_url: str) -> list[dict[str, str]]:
        """Zones are drawn on the image with the internal zone ids as labels, so Grok answers in those ids."""
        annotated = annotate(frame.image, [], self.zones, self.cfg, {z: z for z in self.zones})
        return grok_find_cups(annotated, self.zones, key, model, base_url)

    def scan(self, frame: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        bg = self.background
        if bg is not None and bg.shape == frame.image.shape:
            cups = find_cups_background(frame.image, bg, self.zones, self.cfg)
        else:
            cups = find_cups(frame.image, self.zones, self.cfg)
        w, h = frame.size
        pubs = []
        for c in cups:
            d = c.public()
            x, y, bw, bh = c.bbox
            d["frac"] = [round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)]
            pubs.append(d)
        ok, buf = cv2.imencode(".jpg", annotate(frame.image, cups, self.zones, self.cfg, self.labels),
                               [cv2.IMWRITE_JPEG_QUALITY, 70])
        self.last_jpeg = buf.tobytes() if ok else None
        self.last = {"cups": pubs, "frame_id": frame.frame_id, "at": time.time(),
                     "ms": round((time.perf_counter() - t0) * 1000, 1), "detector": self.detector}
        return self.last
