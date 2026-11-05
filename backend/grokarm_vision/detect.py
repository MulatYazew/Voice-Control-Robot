"""Find objects on the table by difference against an empty-table background."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

COLOURS = ("red", "orange", "yellow", "green", "blue", "purple", "pink", "white", "grey", "black")


@dataclass
class Candidate:
    id: int
    px: tuple[float, float]
    bbox: tuple[int, int, int, int]
    area_px: int
    colour: str
    robot_mm: tuple[float, float] | None = None
    inside_workspace: bool = True

    def public(self) -> dict:
        return {
            "id": self.id,
            "px": [round(self.px[0], 1), round(self.px[1], 1)],
            "bbox": list(self.bbox),
            "area_px": self.area_px,
            "colour": self.colour,
            "robot_mm": None if self.robot_mm is None else [round(v, 1) for v in self.robot_mm],
            "inside_workspace": self.inside_workspace,
        }


def find_candidates(
    image: np.ndarray,
    background: np.ndarray,
    *,
    diff_threshold: int = 35,
    min_area_frac: float = 0.0006,
    max_area_frac: float = 0.2,
) -> list[Candidate]:
    if image.shape != background.shape:
        raise ValueError("frame and background differ in size")
    h, w = image.shape[:2]
    a = cv2.GaussianBlur(image, (5, 5), 0)
    b = cv2.GaussianBlur(background, (5, 5), 0)
    diff = cv2.absdiff(cv2.cvtColor(a, cv2.COLOR_BGR2LAB), cv2.cvtColor(b, cv2.COLOR_BGR2LAB))
    score = diff.max(axis=2)
    mask = (score > diff_threshold).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_area, max_area = min_area_frac * w * h, max_area_frac * w * h
    found: list[Candidate] = []
    for i in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if area < min_area or area > max_area:
            continue
        # The arm enters the picture from an edge; objects must be fully inside.
        if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
            continue
        blob = labels == i
        found.append(
            Candidate(
                id=0,
                px=(float(centroids[i][0]), float(centroids[i][1])),
                bbox=(x, y, bw, bh),
                area_px=area,
                colour=colour_name(image, blob),
            )
        )
    found.sort(key=lambda c: (c.px[0], c.px[1]))
    for n, c in enumerate(found, start=1):
        c.id = n
    return found


def colour_name(image: np.ndarray, mask: np.ndarray) -> str:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[mask]
    if len(hsv) == 0:
        return "grey"
    s = np.median(hsv[:, 1])
    v = np.median(hsv[:, 2])
    if v < 50:
        return "black"
    if s < 45:
        return "white" if v > 170 else "grey"
    saturated = hsv[hsv[:, 1] >= 45]
    hue = saturated[:, 0].astype(np.float64) * (np.pi / 90.0)
    mean = np.degrees(np.arctan2(np.sin(hue).mean(), np.cos(hue).mean())) / 2.0 % 180.0
    if mean < 8 or mean >= 168:
        return "red"
    if mean < 20:
        return "orange"
    if mean < 35:
        return "yellow"
    if mean < 85:
        return "green"
    if mean < 128:
        return "blue"
    if mean < 150:
        return "purple"
    return "pink"


def annotate(image: np.ndarray, candidates: list[Candidate], chosen: list[int]) -> np.ndarray:
    out = image.copy()
    for c in candidates:
        x, y, w, h = c.bbox
        colour = (0, 200, 0) if c.id in chosen else (0, 200, 255) if c.inside_workspace else (0, 0, 255)
        cv2.rectangle(out, (x, y), (x + w, y + h), colour, 3 if c.id in chosen else 2)
        label = f"{c.id}"
        cv2.putText(out, label, (x, max(18, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(out, label, (x, max(18, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
        if c.id in chosen:
            cv2.drawMarker(out, (int(c.px[0]), int(c.px[1])), (0, 255, 0), cv2.MARKER_CROSS, 24, 2)
    return out
