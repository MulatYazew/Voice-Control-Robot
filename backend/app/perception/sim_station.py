"""Simulated station camera. URSim has no camera and no objects.

The picture is rendered here and labelled SIMULATED CAMERA · SYNTHETIC PRODUCTS.
A pack moves only after a motion the gate reports as completato.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from ..config import ROOT

W, H, TOP = 1500, 900, 560
ZONES = ("sinistra", "centro", "destra")
BANNER = "SIMULATED CAMERA · SYNTHETIC PRODUCTS"


def mirror_view(image: np.ndarray) -> np.ndarray:
    return cv2.flip(image, 1)


class SimStation:
    def __init__(self, root: Path | None = None, *, scenes: Path | None = None) -> None:
        self.root = root or ROOT
        self.scenes = scenes or (self.root / "config" / "scenes")
        self.dataset = self.root / "data" / "datasets" / "synthetic"
        self.lock = threading.Lock()
        self.zones: dict[str, dict[str, str] | None] = {z: None for z in ZONES}
        self.on_fork: dict[str, str] | None = None
        self.reject: dict[str, str] | None = None
        self.uncertain = False
        self.mirror = True
        self.difficulty = "normal"
        self.known_pose: str | None = None
        self._rng = np.random.default_rng(20261105)
        self.load_preset("demo")

    def on_robot(self, skill: str, params: dict | None, outcome: str) -> None:
        params = params or {}
        with self.lock:
            if outcome != "completato":
                self.uncertain = True
                return
            self.uncertain = False
            if skill == "solleva_vassoio":
                zone = params.get("posizione")
                self.on_fork = self.zones.get(zone)
                if zone in self.zones:
                    self.zones[zone] = None
            elif skill == "appoggia_vassoio":
                zone = params.get("posizione")
                if zone in self.zones:
                    self.zones[zone] = self.on_fork
                self.on_fork = None
            elif skill == "spingi" and params.get("direzione") == "scarto":
                zone = params.get("posizione")
                self.reject = self.zones.get(zone) or self.on_fork
                if zone in self.zones:
                    self.zones[zone] = None
                self.on_fork = None
            elif skill == "sposta_vassoio":
                pack = self.zones.get(params.get("origine"))
                if params.get("origine") in self.zones:
                    self.zones[params["origine"]] = None
                if params.get("destinazione") in self.zones:
                    self.zones[params["destinazione"]] = pack

    def set_zone(self, zone: str, product: str | None, variant: str | None) -> None:
        if zone not in self.zones:
            raise KeyError(zone)
        with self.lock:
            self.zones[zone] = None if not product else {"product": product, "variant": variant or "correct_1", "uid": f"{product}:{variant or 'correct_1'}"}

    def load_preset(self, name: str) -> dict[str, Any]:
        path = self.scenes / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(name)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        with self.lock:
            self.zones = {z: None for z in ZONES}
            self.on_fork = None
            self.reject = None
            self.uncertain = False
            self.mirror = bool(data.get("mirror_view", True))
            for zone, pack in (data.get("zones") or {}).items():
                if zone in self.zones and pack:
                    self.zones[zone] = {"product": pack["product"], "variant": pack.get("variant", "correct_1"),
                                        "uid": f"{pack['product']}:{pack.get('variant', 'correct_1')}"}
        return self.public()

    def reset(self) -> dict[str, Any]:
        return self.load_preset("empty")

    def public(self) -> dict[str, Any]:
        with self.lock:
            return {"label": BANNER, "zones": {z: (None if p is None else dict(p)) for z, p in self.zones.items()},
                    "on_fork": None if self.on_fork is None else dict(self.on_fork),
                    "reject": None if self.reject is None else dict(self.reject),
                    "uncertain": self.uncertain, "mirror_view": self.mirror, "known_pose": self.known_pose,
                    "difficulty": self.difficulty}

    def note_pose(self, pose: str | None) -> None:
        with self.lock:
            self.known_pose = pose

    def render(self) -> np.ndarray:
        with self.lock:
            zones = {z: (None if p is None else dict(p)) for z, p in self.zones.items()}
            fork, reject, uncertain, pose, mirror, difficulty = self.on_fork, self.reject, self.uncertain, self.known_pose, self.mirror, self.difficulty
        img = np.full((H, W, 3), (186, 178, 166), np.uint8)
        for i, zone in enumerate(ZONES):
            x0 = i * (W // 3)
            cv2.rectangle(img, (x0 + 8, 8), (x0 + W // 3 - 8, TOP - 8), (150, 142, 132), 2)
            cv2.putText(img, zone, (x0 + 20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 2)
            pack = zones[zone]
            if pack:
                self._paste(img, pack, x0 + 40, 70, W // 3 - 80, TOP - 120, "front")
            if mirror and pack:
                back = self._load(pack, "back")
                band = mirror_view(back if back is not None else self._card(pack, "back"))
                self._fit(img, band, x0 + 40, TOP + 30, W // 3 - 80, H - TOP - 70)
        if fork:
            cv2.putText(img, "on fork", (W // 2 - 60, TOP - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 140), 2)
        if reject:
            cv2.putText(img, "reject", (W - 160, H - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 160), 2)
        if pose:
            cv2.putText(img, f"pose {pose}", (20, H - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 30, 30), 1)
        if uncertain:
            cv2.putText(img, "uncertain", (W // 2 - 80, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 180), 2)
        cv2.rectangle(img, (0, 0), (W, 28), (30, 30, 30), -1)
        cv2.putText(img, BANNER, (12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1)
        return self._disturb(img, difficulty)

    def _paste(self, canvas, pack, x, y, w, h, face: str) -> None:
        src = self._load(pack, face)
        self._fit(canvas, src if src is not None else self._card(pack, face), x, y, w, h)

    def _load(self, pack: dict[str, str], face: str) -> np.ndarray | None:
        path = self.dataset / pack["product"] / "test" / f"{pack['variant']}_{face}.jpg"
        if not path.exists():
            path = self.root / "data" / "datasets" / "unseen" / pack["product"] / "test" / f"{pack['variant']}_{face}.jpg"
        if not path.exists():
            return None
        return cv2.imread(str(path))

    def _card(self, pack: dict[str, str], face: str) -> np.ndarray:
        card = np.full((320, 240, 3), (230, 226, 214), np.uint8)
        cv2.putText(card, pack["product"][:18], (8, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 2)
        cv2.putText(card, face, (8, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2)
        return card

    def _fit(self, canvas, src, x, y, w, h) -> None:
        if src is None or w < 4 or h < 4:
            return
        thumb = cv2.resize(src, (w, h))
        canvas[y:y + h, x:x + w] = thumb

    def _disturb(self, img: np.ndarray, difficulty: str) -> np.ndarray:
        scale = {"easy": 0.4, "normal": 1.0, "hard": 2.2}.get(difficulty, 1.0)
        out = img.astype(np.float32)
        out *= 1 + float(self._rng.uniform(-0.03, 0.03)) * scale
        out += self._rng.normal(0, 2.5 * scale, out.shape)
        out = np.clip(out, 0, 255).astype(np.uint8)
        if difficulty == "hard":
            out = cv2.GaussianBlur(out, (5, 5), 0)
            cv2.line(out, (0, 200), (W, 230), (255, 255, 255), 2)
        return out


def catalog_from_dataset(root: Path) -> list[dict[str, str]]:
    """Development products only. A held-out or unseen pack stays unknown."""
    out = []
    base = root / "data" / "datasets" / "synthetic"
    if not base.exists():
        return out
    for truth_path in sorted(base.glob("*/truth.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        if truth.get("split") == "unseen":
            continue
        out.append({"id": truth["product_id"], "nome": truth.get("product_name") or truth.get("name") or "",
                    "marca": truth.get("brand") or ""})
    return out
