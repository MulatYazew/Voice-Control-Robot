"""OCR (Tesseract) + text normalisation and character-level comparison.

The raw OCR output is always kept; comparisons use a normalised form (case, whitespace and
punctuation spacing only — never letters or digits), so a changed ingredient is never "normalised away".
"""

from __future__ import annotations

import difflib
import re
import shutil
import unicodedata
from typing import Any

import cv2
import numpy as np

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None


def available() -> bool:
    return pytesseract is not None and shutil.which("tesseract") is not None


def read(crop: np.ndarray, langs: str = "ita+eng") -> dict[str, Any]:
    """OCR one deskewed label crop. Returns raw text, mean word confidence (0-100) and a status."""
    if not available():
        return {"ok": False, "text": "", "confidence": 0.0, "error": "tesseract non disponibile"}
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    scale = max(1.0, 1400 / max(g.shape[1], 1))       # small labels: upscale for OCR
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    data = pytesseract.image_to_data(g, lang=langs, config="--psm 6", output_type=pytesseract.Output.DICT)
    words, confs, lines, cur_key = [], [], [], None
    for i, w in enumerate(data["text"]):
        if not w.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key != cur_key:
            lines.append([])
            cur_key = key
        lines[-1].append(w)
        words.append(w)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)
    text = "\n".join(" ".join(l) for l in lines)
    return {"ok": True, "text": text, "confidence": round(float(np.mean(confs)) if confs else 0.0, 1),
            "n_words": len(words)}


def normalise(text: str | None) -> str:
    """Case-fold, unify whitespace and the spacing around punctuation. Letters/digits are untouched."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).upper()
    t = t.replace("\n", " ").replace("’", "'").replace("‘", "'").replace("|", " ")  # "|" = label border lines
    t = re.sub(r"\s*([,.;:/()\-])\s*", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def ratio(a: str, b: str) -> float:
    from rapidfuzz import fuzz
    return float(fuzz.ratio(normalise(a), normalise(b)))


def contains(haystack: str, needle: str) -> float:
    """Best fuzzy containment score of needle in haystack (0..100)."""
    from rapidfuzz import fuzz
    return float(fuzz.partial_ratio(normalise(needle), normalise(haystack)))


def diff(ref: str, got: str, max_items: int = 6) -> list[dict[str, str]]:
    """Human-readable differences between the approved reference and the OCR text."""
    a, b = normalise(ref), normalise(got)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            out.append({"op": op, "ref": a[max(0, i1 - 8):i2 + 8], "got": b[max(0, j1 - 8):j2 + 8]})
        if len(out) >= max_items:
            break
    return out


def split_inci(text: str) -> list[str]:
    """Split an ingredient list ("INGREDIENTS: A, B, C.") into normalised INCI names."""
    t = normalise(text)
    t = re.sub(r"^(INGREDIENTS|INGREDIENTI|INCI)\s*:?", "", t).strip()
    t = t.split(".")[0] if "." in t else t
    return [x.strip() for x in t.split(",") if x.strip()]


def section(text: str, start_marker: str | None = None) -> str:
    """Return the text from a marker on (e.g. legal text after the INCI list)."""
    if not start_marker:
        return text
    i = normalise(text).find(normalise(start_marker))
    return normalise(text)[i:] if i >= 0 else ""
