"""Deterministic object → zone decision. Grok vision is evidence, never a movement.

A candidate matches only when every attribute the operator stated is supported.
Zero matches, several zones, or a disagreement → no motion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import yaml

from ..config import ROOT

_CFG = ROOT / "config" / "perception.yaml"

# Reference swatches in BGR. Lab distance uses the same OpenCV conversion as the crop.
BGR_PALETTE: dict[str, tuple[int, int, int]] = {
    "rosso": (40, 40, 210),
    "arancione": (30, 120, 230),
    "giallo": (40, 210, 230),
    "verde": (40, 160, 40),
    "blu": (200, 60, 40),
    "viola": (160, 40, 140),
    "rosa": (180, 140, 230),
    "bianco": (240, 240, 240),
    "nero": (20, 20, 20),
    "grigio": (140, 140, 140),
    "marrone": (40, 70, 120),
    "trasparente": (200, 210, 220),
}
ZONES = ("sinistra", "centro", "destra")
COLOURS = tuple(BGR_PALETTE)


def thresholds() -> dict[str, Any]:
    if not _CFG.exists():
        return {"colour_max_de": 32, "ocr_fuzzy": 85, "background_diff": 28}
    raw = yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}
    return dict(raw.get("perception") or raw)


def _lab(bgr: np.ndarray) -> np.ndarray:
    pix = np.uint8([[bgr]]) if bgr.ndim == 1 else bgr
    return cv2.cvtColor(pix, cv2.COLOR_BGR2LAB).astype(np.float32)


def palette_lab() -> dict[str, np.ndarray]:
    return {name: _lab(np.uint8([[c]]))[0, 0] for name, c in BGR_PALETTE.items()}


def nearest_colour(median_lab: np.ndarray) -> tuple[str, float]:
    best, dist = "grigio", 1e9
    for name, ref in palette_lab().items():
        d = float(np.linalg.norm(median_lab - ref))
        if d < dist:
            best, dist = name, d
    return best, dist


@dataclass
class Candidate:
    zona: str | None
    colore: str | None = None
    colore_de: float = 999.0
    testo: str = ""
    testo_score: float = 0.0
    categoria: str | None = None
    grok_colore: str | None = None
    grok_testo: str | None = None
    grok_categoria: str | None = None
    su_vassoio: bool = True
    fonti: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"zona": self.zona, "colore": self.colore, "colore_de": round(self.colore_de, 1),
                "testo": self.testo, "testo_score": round(self.testo_score, 1), "categoria": self.categoria,
                "fonti": self.fonti, "su_vassoio": self.su_vassoio}


@dataclass
class Query:
    categoria: str | None = None
    colore: str | None = None
    testo: str | None = None
    prodotto_id: str | None = None
    zona: str | None = None

    @classmethod
    def from_args(cls, args: dict[str, Any]) -> "Query":
        def clean(k: str) -> str | None:
            v = args.get(k)
            if v in (None, ""):
                return None
            return str(v).strip().lower()
        return cls(categoria=clean("categoria"), colore=clean("colore"), testo=clean("testo"),
                   prodotto_id=args.get("prodotto_id") or None, zona=clean("zona"))


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    from rapidfuzz import fuzz
    return float(fuzz.partial_ratio(a.lower(), b.lower()))


def _supports(c: Candidate, q: Query, fuzzy_min: float) -> tuple[bool, bool]:
    """Returns (matches, disagrees). Disagreement is Grok vs the deterministic colour/text."""
    disagree = False
    if q.zona and c.zona != q.zona:
        return False, False
    if q.colore:
        if c.grok_colore and c.colore and c.grok_colore != c.colore:
            disagree = True
        if c.colore != q.colore:
            return False, disagree
    if q.testo:
        ocr_ok = _fuzzy(c.testo, q.testo) >= fuzzy_min
        grok_ok = bool(c.grok_testo) and _fuzzy(c.grok_testo, q.testo) >= fuzzy_min
        if c.grok_testo and c.testo and _fuzzy(c.grok_testo, c.testo) < 50:
            disagree = True
        if not (ocr_ok or grok_ok):
            return False, disagree
    if q.categoria:
        if c.grok_categoria and c.categoria and c.grok_categoria != c.categoria:
            disagree = True
        if (c.categoria or c.grok_categoria) != q.categoria and c.grok_categoria != q.categoria and c.categoria != q.categoria:
            return False, disagree
    if q.prodotto_id and q.prodotto_id not in (c.testo, c.grok_testo or ""):
        # prodotto_id is accepted when the text or grok reading contains it, or the caller already filtered.
        if q.prodotto_id not in (c.testo or "") and not (c.grok_testo and q.prodotto_id in c.grok_testo):
            return False, disagree
    return True, disagree


def decide(query: Query, candidates: list[Candidate], *, calibrated: bool, camera_available: bool,
           fuzzy_min: float | None = None) -> dict[str, Any]:
    if not camera_available:
        return {"ok": False, "esito": "nessuna_telecamera",
                "messaggio": "Questa stazione non ha telecamera. Non posso vedere gli oggetti."}
    if not calibrated:
        return {"ok": False, "esito": "zone_non_calibrate",
                "messaggio": "Le zone della telecamera non sono calibrate. Nessun movimento."}
    if query.zona and query.zona not in ZONES:
        return {"ok": False, "esito": "fuori_portata",
                "messaggio": f"La zona {query.zona} non è nel banco (sinistra, centro, destra)."}
    limit = float(fuzzy_min if fuzzy_min is not None else thresholds()["ocr_fuzzy"])
    de_max = float(thresholds()["colour_max_de"])
    usable = []
    for c in candidates:
        if c.colore_de > de_max and query.colore and c.colore == query.colore:
            continue
        ok, disagree = _supports(c, query, limit)
        if disagree and ok:
            return {"ok": False, "esito": "incerto",
                    "messaggio": "La visione e il controllo deterministico non concordano. Non muovo nulla.",
                    "candidati": [x.public() for x in candidates]}
        if ok:
            usable.append(c)
    off = [c for c in usable if not c.su_vassoio or not c.zona]
    if off and not [c for c in usable if c.su_vassoio and c.zona]:
        return {"ok": False, "esito": "non_su_vassoio",
                "messaggio": "Vedo qualcosa, ma non è su un vassoio che il braccio può spostare.",
                "candidati": [c.public() for c in off]}
    by_zone: dict[str, Candidate] = {}
    for c in usable:
        if c.zona and c.zona not in by_zone:
            by_zone[c.zona] = c
    visible = sorted({c.zona for c in candidates if c.zona})
    if len(by_zone) == 1:
        zona, c = next(iter(by_zone.items()))
        return {"ok": True, "esito": "ok", "zona": zona, "candidato": c.public(),
                "messaggio": f"Un solo candidato nella zona {zona}."}
    if len(by_zone) >= 2:
        opts = ", ".join(by_zone)
        return {"ok": False, "esito": "richiesta_ambigua", "opzioni": list(by_zone),
                "messaggio": f"Vedo più di una zona compatibile ({opts}). Quale?",
                "candidati": [c.public() for c in by_zone.values()]}
    return {"ok": False, "esito": "non_trovato", "visibili": visible,
            "messaggio": "Non trovo un oggetto che corrisponde." + (f" Zone con qualcosa: {', '.join(visible)}." if visible else ""),
            "candidati": [c.public() for c in candidates]}


def colour_of_patch(bgr: np.ndarray) -> tuple[str, float]:
    """Median Lab, ignoring near-white highlights."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    flat = lab.reshape(-1, 3).astype(np.float32)
    keep = flat[:, 0] < 245
    sample = flat[keep] if int(keep.sum()) > 10 else flat
    med = np.median(sample, axis=0)
    return nearest_colour(med)


def proposals_from_frame(frame: np.ndarray, zones: dict[str, list[list[float]]], background: np.ndarray | None,
                          *, diff: float = 28) -> list[dict[str, Any]]:
    """Foreground contours inside each calibrated zone. Fast, no network."""
    if background is None or background.shape != frame.shape:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        bg = np.full_like(gray, 180)
        delta = cv2.absdiff(gray, bg)
    else:
        delta = cv2.absdiff(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.cvtColor(background, cv2.COLOR_BGR2GRAY))
    mask = (delta > diff).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    out = []
    h, w = mask.shape
    for zona, poly in zones.items():
        if not isinstance(poly, list) or len(poly) < 3:
            continue
        zone_mask = np.zeros((h, w), np.uint8)
        pts = np.array(poly, np.int32)
        cv2.fillPoly(zone_mask, [pts], 255)
        region = cv2.bitwise_and(mask, zone_mask)
        contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw * bh < 80:
                continue
            crop = frame[y:y + bh, x:x + bw]
            name, de = colour_of_patch(crop)
            out.append({"zona": zona, "bbox": [int(x), int(y), int(bw), int(bh)], "colore": name,
                        "colore_de": de, "crop": crop, "fonti": ["CV"]})
    return out
