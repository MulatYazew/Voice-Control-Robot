"""Read a station frame. Perception never decides to move the robot."""

from __future__ import annotations

from typing import Any

import yaml

from ..config import ROOT
from ..labelling import ocr
from .finder import BGR_PALETTE
from .sim_station import BANNER, TOP, ZONES

_TYPES = ROOT / "config" / "perception.yaml"


def type_lexicon() -> dict[str, list[str]]:
    data = yaml.safe_load(_TYPES.read_text(encoding="utf-8")) if _TYPES.exists() else {}
    block = ((data.get("perception") or {}).get("types") or {})
    return {name: [str(w).lower() for w in words] for name, words in block.items()}


def match_product(text: str, catalog: list[dict[str, str]], min_ratio: float = 85) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for item in catalog:
        score = 0.0
        for field in ("nome", "marca", "id"):
            value = item.get(field) or ""
            if value:
                score = max(score, ocr.ratio(text, value), ocr.contains(text, value))
        if best is None or score > best["ratio"]:
            best = {"id": item.get("id"), "nome": item.get("nome"), "marca": item.get("marca"), "ratio": round(score, 1)}
    if best and best["ratio"] >= min_ratio:
        best["fonte"] = "ocr+catalogo"
        return best
    return None


def match_type(text: str, lexicon: dict[str, list[str]] | None = None) -> dict[str, Any] | None:
    low = (text or "").lower()
    for name, words in (lexicon if lexicon is not None else type_lexicon()).items():
        for word in words:
            if word and word in low:
                return {"valore": name, "fonte": "testo", "parola": word}
    return None


def nearest_colour(bgr: tuple[int, int, int], max_delta: float = 32) -> dict[str, Any]:
    import cv2
    import numpy as np
    lab = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2LAB)[0, 0].astype(float)
    best_name, best_d = "unclassified", 1e9
    for name, swatch in BGR_PALETTE.items():
        slab = cv2.cvtColor(np.uint8([[swatch]]), cv2.COLOR_BGR2LAB)[0, 0].astype(float)
        dist = float(np.linalg.norm(lab - slab))
        if dist < best_d:
            best_name, best_d = name, dist
    if best_d > max_delta:
        return {"nome": "unclassified", "delta": round(best_d, 1)}
    return {"nome": best_name, "delta": round(best_d, 1)}


def rows_from_image(img, catalog: list[dict[str, str]], *, read=None, sorgente: str = BANNER,
                    mirror: bool = True) -> list[dict[str, Any]]:
    """Pack presence comes from the pixels. The name comes from the text reader, never from the scene dict."""
    import numpy as np
    packs: dict[str, dict | None] = {}
    texts: dict[str, str] = {}
    top = img[:TOP] if img.shape[0] >= TOP else img
    width = top.shape[1] // 3
    for i, zone in enumerate(ZONES):
        crop = top[:, i * width:(i + 1) * width]
        body = crop[48:, 20:-20] if crop.shape[0] > 80 and crop.shape[1] > 50 else crop
        # An empty tray is the flat table (std about 9). A pack, even a pale one, is well above that.
        if float(np.std(body)) < 16:
            packs[zone] = None
            continue
        packs[zone] = {"present": True}
        if read is not None:
            texts[zone] = read(crop)
        else:
            from ..labelling import ocr as reader
            texts[zone] = str(reader.read(crop).get("text") or "") if reader.available() else ""
    return describe_zones(packs, texts, catalog, mirror=mirror, sorgente=sorgente)


def describe_zones(packs: dict[str, dict | None], texts: dict[str, str], catalog: list[dict[str, str]], *,
                   mirror: bool = True, compliance: dict[str, dict] | None = None,
                   sorgente: str = BANNER) -> list[dict[str, Any]]:
    """One row per zone. An unknown product is never marked compliant."""
    rows = []
    lexicon = type_lexicon()
    for zone in ZONES:
        pack = packs.get(zone)
        if not pack:
            rows.append({"zona": zone, "stato": "empty", "prodotto": None, "tipo": None, "colore": None,
                         "etichette": {}, "conformita": None, "sorgente": sorgente})
            continue
        text = texts.get(zone) or ""
        found = match_product(text, catalog)
        kind = match_type(text, lexicon)
        decision = None
        if found and compliance and found["id"] in compliance:
            decision = compliance[found["id"]]
        elif found is None:
            decision = {"decisione": None, "motivi": ["not checkable (no approved spec)"]}
        else:
            decision = {"decisione": None, "motivi": ["not checkable (no approved spec)"]}
        if decision and decision.get("decisione") == "COMPLIANT" and found is None:
            decision = {"decisione": None, "motivi": ["not checkable (no approved spec)"]}
        rows.append({
            "zona": zone, "stato": "pack",
            "prodotto": found or {"id": None, "nome": None, "marca": None, "ratio": None, "fonte": "unknown product"},
            "tipo": kind, "colore": None,
            "etichette": {"fronte": "presente", "retro": "presente (specchio)" if mirror else "non visibile"},
            "conformita": decision, "sorgente": sorgente,
        })
    return rows


def find_in_rows(rows: list[dict[str, Any]], *, tipo: str | None = None, nome: str | None = None,
                 marca: str | None = None) -> dict[str, Any]:
    hits = []
    for row in rows:
        if row["stato"] != "pack":
            continue
        kind = (row.get("tipo") or {}).get("valore")
        product = row.get("prodotto") or {}
        if tipo and kind != tipo:
            continue
        blob = " ".join(str(product.get(k) or "") for k in ("nome", "marca", "id")).lower()
        if nome and nome.lower() not in blob and ocr.ratio(blob, nome) < 85:
            continue
        if marca and marca.lower() not in blob and ocr.ratio(blob, marca) < 85:
            continue
        hits.append(row["zona"])
    if len(hits) == 1:
        return {"stato": "match", "zone": hits}
    if len(hits) > 1:
        return {"stato": "ambiguous", "zone": hits}
    return {"stato": "not_found", "zone": []}
