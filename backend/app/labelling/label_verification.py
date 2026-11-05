"""Label sheet verification (before the production cycle starts).

For every required label of the APPROVED specification: find it on the sheet photo (multi-scale template
match of the model label crop, also 180° rotated -> sheet orientation), then OCR the match and compare with
the approved reference text. Missing / wrong / upside-down -> the cycle must not start. Never substitutes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import ocr
from .compliance import COMPLIANT, NON_COMPLIANT, REVIEW
from .specification import ProductSpec

MATCH, MISMATCH = "MATCH", "MISMATCH"


FOUND_MIN = 0.80     # measured on the synthetic set: correct >= 0.96, other product's labels <= 0.58


def _scan(sheet_g: np.ndarray, tmpl_g: np.ndarray, scales) -> tuple[float, float, tuple[int, int, int, int]]:
    best = (-1.0, 1.0, (0, 0, 0, 0))
    H, W = sheet_g.shape
    for sc in scales:
        t = cv2.resize(tmpl_g, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        if t.shape[0] >= H or t.shape[1] >= W or t.shape[0] < 20:
            continue
        _, mx, _, loc = cv2.minMaxLoc(cv2.matchTemplate(sheet_g, t, cv2.TM_CCOEFF_NORMED))
        if mx > best[0]:
            best = (float(mx), float(sc), (loc[0], loc[1], t.shape[1], t.shape[0]))
    return best


def _remaining_copies(sheet_g: np.ndarray, tmpl_g: np.ndarray, box: tuple[int, int, int, int]) -> int:
    """How many copies of this label are still on the sheet, including the one just found."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return 1
    masked = sheet_g.copy()
    masked[max(0, y):y + h, max(0, x):x + w] = 0
    second, _ = _best_match(masked, tmpl_g)
    return 2 if second >= FOUND_MIN else 1


def apply_sheet_opinion(result: dict[str, Any], opinion: dict[str, Any] | None) -> dict[str, Any]:
    """Grok may turn MATCH into REVIEW. It can never turn a rejection into a match."""
    out = dict(result)
    exp = dict(out.get("explanation") or {})
    if opinion is None:
        out["ai_check"] = "not_available"
        exp["en"] = ((exp.get("en") or "") + " AI check not available.").strip()
        exp["it"] = ((exp.get("it") or "") + " Controllo AI non disponibile.").strip()
        out["explanation"] = exp
        return out
    out["ai_check"] = "advisory"
    out["ai_opinion"] = {"match": bool(opinion.get("match")), "per_label": opinion.get("per_label") or [],
                         "explanation": opinion.get("explanation") or "",
                         "label": "AI opinion: advisory, not the decision"}
    if out.get("decision") == MATCH and opinion.get("match") is False:
        out["decision"] = REVIEW
        out["ok"] = False
        exp["en"] = ((exp.get("en") or "") + " AI opinion: advisory, not the decision. Downgraded to REVIEW.").strip()
        exp["it"] = ((exp.get("it") or "") + " Parere AI: consultivo, non è la decisione. Portato a REVIEW.").strip()
    out["explanation"] = exp
    return out


def _best_match(sheet_g: np.ndarray, tmpl_g: np.ndarray) -> tuple[float, tuple[int, int, int, int]]:
    """Blurred multi-scale template match, coarse then refined (tolerates resampling of fine text)."""
    sb = cv2.GaussianBlur(sheet_g, (0, 0), 2.5)
    tb = cv2.GaussianBlur(tmpl_g, (0, 0), 2.5)
    mx, sc, _ = _scan(sb, tb, np.linspace(0.4, 1.8, 29))
    mx, sc, box = _scan(sb, tb, np.linspace(sc - 0.05, sc + 0.05, 11))
    return mx, box


def verify_sheet(spec: ProductSpec, sheet: np.ndarray, product_dir: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    if spec.approval_status != "approved":
        raise ValueError("la specifica non è approvata / specification not approved")
    g = cv2.cvtColor(sheet, cv2.COLOR_BGR2GRAY)
    scale = 1200 / max(g.shape[1], 1)
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    color = cv2.resize(sheet, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_AREA)
    labels, worst = [], MATCH
    for l in spec.labels:
        if not l.required:
            continue
        ref = cv2.imread(str(product_dir / (l.reference_crop or "")), cv2.IMREAD_GRAYSCALE)
        if ref is None:
            labels.append({"label_id": l.label_id, "found": False, "status": REVIEW,
                           "it": f"{l.label_id}: immagine di riferimento mancante", "en": f"{l.label_id}: reference image missing"})
            worst = REVIEW if worst == MATCH else worst
            continue
        m = int(0.03 * min(ref.shape[:2]))
        ref = ref[m:-m, m:-m] if m else ref   # without the cut edge
        up, box = _best_match(g, ref)
        flipped, _ = _best_match(g, cv2.rotate(ref, cv2.ROTATE_180))
        item: dict[str, Any] = {"label_id": l.label_id, "match_score": round(up, 3), "flipped_score": round(flipped, 3),
                                "box": box}
        if flipped >= FOUND_MIN and flipped > up + 0.05:
            item.update(found=True, status=NON_COMPLIANT, it=f"{l.label_id}: foglio capovolto", en=f"{l.label_id}: sheet upside down")
        elif up < FOUND_MIN:
            item.update(found=False, status=NON_COMPLIANT, it=f"{l.label_id}: etichetta assente dal foglio",
                        en=f"{l.label_id}: label not on the sheet")
        else:
            x, y, w, h = box
            # the template was trimmed 3% per side: widen back (plus a little) so first/last letters are not clipped;
            # border lines read as "|" are removed by ocr.normalise()
            mx, my = int(0.04 * w), int(0.04 * h)
            H_, W_ = color.shape[:2]
            got = ocr.read(color[max(0, y - my):min(H_, y + h + my), max(0, x - mx):min(W_, x + w + mx)], cfg["ocr"]["languages"])
            item["ocr"] = got["text"]
            same = ocr.normalise(got["text"]) == ocr.normalise(l.reference_text) or (
                l.inci and ocr.split_inci(got["text"]) == l.inci)
            r = ocr.ratio(l.reference_text, got["text"])
            item["text_ratio"] = round(r, 1)
            if l.mandatory_text and not same:
                item.update(found=True, status=NON_COMPLIANT if r < cfg["ocr"]["review_min_ratio"] else REVIEW,
                            it=f"{l.label_id}: testo sul foglio diverso dal modello ({r:.0f}%)",
                            en=f"{l.label_id}: text on the sheet differs from the model ({r:.0f}%)",
                            diff=ocr.diff(l.reference_text, got["text"]))
            else:
                item.update(found=True, status=COMPLIANT, it=f"{l.label_id}: presente e corretta", en=f"{l.label_id}: present and correct")
        if item.get("found"):
            item["remaining"] = _remaining_copies(g, ref, box)
        else:
            item["remaining"] = 0
        labels.append(item)
        if item["status"] == NON_COMPLIANT:
            worst = MISMATCH
        elif item["status"] == REVIEW and worst == MATCH:
            worst = REVIEW
    ok = worst == MATCH
    empties = [i["label_id"] for i in labels if i.get("remaining") == 0]
    ones = [i["label_id"] for i in labels if i.get("remaining") == 1]
    sheet_state = "SHEET_EMPTY" if empties else ("ONE_LEFT" if ones else "OK")
    return {"decision": worst, "ok": ok, "labels": labels, "sheet_state": sheet_state,
            "explanation": {"it": ("Foglio etichette corretto per " if ok else "Foglio etichette NON utilizzabile per ") + f"{spec.product_name}. "
                            + "; ".join(i["it"] for i in labels),
                            "en": ("Label sheet matches " if ok else "Label sheet NOT usable for ") + f"{spec.product_name}. "
                            + "; ".join(i["en"] for i in labels)}}
