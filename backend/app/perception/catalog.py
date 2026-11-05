"""Product catalogue by enrolment, not retraining.

One photo of a new SKU is stored with its OCR keywords and ORB descriptors.
Recognition matches against those references. No per-product training, no GPU.
Thresholds in config/perception.yaml are engineering defaults, to re-tune on real photos.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .finder import thresholds


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "prodotto"


class Catalog:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def products(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.root.glob("*/product.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return out

    def keyterms(self) -> list[str]:
        words: list[str] = []
        for prod in self.products():
            words.append(str(prod.get("name") or ""))
            words.extend(prod.get("aliases") or [])
            words.extend(prod.get("keywords") or [])
        return [w for w in words if isinstance(w, str) and w]

    def enroll(self, name: str, image: np.ndarray, *, product_id: str | None = None,
               aliases: list[str] | None = None, keywords: list[str] | None = None,
               colours: list[str] | None = None) -> dict[str, Any]:
        pid = product_id or _slug(name)
        d = self.root / pid
        d.mkdir(parents=True, exist_ok=True)
        n = len(list(d.glob("ref_*.jpg"))) + 1
        cv2.imwrite(str(d / f"ref_{n}.jpg"), image)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        kps, des = cv2.ORB_create(nfeatures=800).detectAndCompute(gray, None)
        if des is not None and kps:
            np.savez(d / "orb.npz", des=des, pts=np.float32([k.pt for k in kps]))
        meta_path = d / "product.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {
            "product_id": pid, "name": name, "aliases": [], "keywords": [], "colours": []}
        for src, key in ((aliases, "aliases"), (keywords, "keywords"), (colours, "colours")):
            for w in src or []:
                if w and w not in meta[key]:
                    meta[key].append(w)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return meta

    def _orb_inliers(self, image: np.ndarray, product_dir: Path) -> int:
        path = product_dir / "orb.npz"
        if not path.exists():
            return 0
        data = np.load(path)
        stored, pts = data["des"], data["pts"] if "pts" in data.files else None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        kps, des = cv2.ORB_create(nfeatures=800).detectAndCompute(gray, None)
        if des is None or stored is None or kps is None or len(des) < 8 or len(stored) < 8 or pts is None:
            return 0
        pairs = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(des, stored), key=lambda m: m.distance)[:80]
        if len(pairs) < 8:
            return len(pairs)
        src = np.float32([kps[m.queryIdx].pt for m in pairs]).reshape(-1, 1, 2)
        dst = np.float32([pts[m.trainIdx] for m in pairs]).reshape(-1, 1, 2)
        _h, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if mask is None:
            return 0
        return int(mask.ravel().sum())

    def match(self, image: np.ndarray | None = None, text: str = "") -> dict[str, Any] | None:
        """Best product, or None. OCR keywords are primary; ORB inliers are secondary."""
        from rapidfuzz import fuzz
        min_fuzzy = float(thresholds().get("ocr_fuzzy", 85))
        min_inliers = int(thresholds().get("orb_inliers", 12))
        best: tuple[float, dict[str, Any]] | None = None
        for prod in self.products():
            score = 0.0
            why: list[str] = []
            blob = " ".join([prod.get("name") or "", *(prod.get("aliases") or []), *(prod.get("keywords") or [])])
            if text and blob:
                fz = float(fuzz.partial_ratio(text.lower(), blob.lower()))
                if fz >= min_fuzzy:
                    score += fz
                    why.append(f"ocr:{fz:.0f}")
            if image is not None:
                inl = self._orb_inliers(image, self.root / prod["product_id"])
                if inl >= min_inliers:
                    score += float(inl)
                    why.append(f"orb:{inl}")
            if score > 0 and (best is None or score > best[0]):
                best = (score, {**prod, "score": round(score, 1), "why": why})
        return None if best is None else best[1]


def enroll_approved(spec: Any, product_dir: Path, catalog_root: Path) -> None:
    """Copy an approved labelling spec into the catalogue. Best-effort; never blocks approval."""
    keywords: list[str] = []
    for label in getattr(spec, "labels", []) or []:
        for chunk in (getattr(label, "reference_text", None), getattr(label, "product_name", None),
                      getattr(label, "brand", None)):
            if chunk:
                keywords.append(str(chunk)[:80])
    image = None
    for pattern in ("front*.jpg", "ref_*.jpg", "*.jpg"):
        hits = sorted(product_dir.glob(pattern))
        if hits:
            image = cv2.imread(str(hits[0]))
            if image is not None:
                break
    if image is None:
        return
    Catalog(catalog_root).enroll(getattr(spec, "product_name", None) or spec.product_id, image,
                                 product_id=spec.product_id, keywords=keywords[:12])
