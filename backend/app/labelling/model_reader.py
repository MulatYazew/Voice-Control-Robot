"""Read the model pack: photos -> pending ProductSpec (no training, works for unseen products).

  CV      measures WHERE each label is (relative position, size, angle) on each face photo
  OCR     reads the EXACT text of each label (kept verbatim; becomes the reference after approval)
  Grok    interprets WHAT each label is (front brand label / ingredients+legal), reads it independently,
          names the product. Grok never sets tolerances and never fills in text it cannot read.
  Merge   disagreements between OCR and Grok -> field marked uncertain -> operator must confirm.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import cv2

from . import ocr
from . import vision as V
from .grok_vision import GrokVisionError, ask_json, image_part
from .specification import FACES, LabelSpec, ProductSpec, SpecStore

log = logging.getLogger("voce.labelling")

SYSTEM = """You read photos of a cosmetic MODEL PACK for a labelling station. Each photo shows ONE face of the pack.
Detected label regions are listed per face with an index and the OCR text found in them.
For every detected label say what it is and read it yourself. Rules:
- Copy text VERBATIM as printed. Never correct, complete, translate or invent text, ingredients or legal wording.
- If something is unreadable or ambiguous, use null and add the field name to "uncertain".
- kind: "front_brand" (logo / brand / product name), "ingredients_legal" (INCI list and/or legal wording), or "other".
- region: one of upper_left, upper_center, upper_right, center_left, center, center_right, lower_left, lower_center, lower_right.
- Do NOT propose tolerances, measurements or compliance judgements."""

NULLSTR = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["product_name", "brand", "format", "packaging_type", "labels", "warnings"],
    "properties": {
        "product_name": NULLSTR, "brand": NULLSTR, "format": NULLSTR,
        "packaging_type": {"type": "string", "enum": ["box", "bottle", "jar", "tube", "other", "unknown"]},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["face", "index", "kind", "text_verbatim", "brand", "product_name", "has_logo", "inci",
                         "legal_text", "orientation", "region", "uncertain"],
            "properties": {
                "face": {"type": "string", "enum": list(FACES)}, "index": {"type": "integer"},
                "kind": {"type": "string", "enum": ["front_brand", "ingredients_legal", "other"]},
                "text_verbatim": NULLSTR, "brand": NULLSTR, "product_name": NULLSTR,
                "has_logo": {"type": ["boolean", "null"]},
                "inci": {"type": ["array", "null"], "items": {"type": "string"}}, "legal_text": NULLSTR,
                "orientation": {"type": "string", "enum": ["upright", "rotated_90", "rotated_180", "rotated_270", "unknown"]},
                "region": NULLSTR, "uncertain": {"type": "array", "items": {"type": "string"}}}}}},
}


def _region(cx: float, cy: float) -> str:
    v = "upper" if cy < 0.4 else ("lower" if cy > 0.6 else "center")
    h = "left" if cx < 0.4 else ("right" if cx > 0.6 else "center")
    return "center" if (v, h) == ("center", "center") else f"{v}_{h}" if v != "center" else f"center_{h}"


def legal_from(text: str, inci: list[str]) -> str | None:
    """Text printed after the INCI list (first sentence end after the last ingredient)."""
    n = ocr.normalise(text)
    if not inci:
        return None
    last = inci[-1]
    i = n.find(last)
    if i < 0:
        return None
    rest = n[i + len(last):].lstrip(". ")
    return rest or None


def derive_mandatory(l: LabelSpec) -> None:
    """INCI list + legal wording parsed from the (approved) reference text."""
    l.inci = ocr.split_inci(l.reference_text) or None
    l.legal_text = legal_from(l.reference_text, l.inci or [])


def read_model_pack(photos: dict[str, bytes], store: SpecStore, cfg: dict[str, Any], *, api_key: str | None,
                    product_hint: str | None = None) -> ProductSpec:
    if not photos:
        raise ValueError("nessuna foto / no photos")
    bad = [f for f in photos if f not in FACES]
    if bad:
        raise ValueError(f"facce non valide / invalid faces: {bad}")
    det, qcfg, ocfg = cfg["detection"], cfg["image_quality"], cfg["ocr"]
    pid = store.new_id(product_hint or "prodotto")
    spec = ProductSpec(product_id=pid)
    pdir = store.product_dir(pid, 1)
    pdir.mkdir(parents=True, exist_ok=True)
    per_face: dict[str, dict[str, Any]] = {}
    info_face: dict[str, Any] = {}
    parts: list[dict[str, Any]] = []
    listing = []
    for face_name, data in photos.items():
        img = V.load(data)
        (pdir / f"photo_{face_name}.jpg").write_bytes(V.encode_jpeg(img))
        face = V.find_face(img, qcfg["min_face_area_frac"])
        q = V.quality(face, qcfg, img)
        cv2.imwrite(str(pdir / f"face_{face_name}.jpg"), face.image)
        spec.faces[face_name] = f"face_{face_name}.jpg"
        labels = V.detect_labels(face.image, det["label_min_area_frac"], det["label_color_distance"], face.bg_lab) if q["ok"] else []
        texts = []
        for i, b in enumerate(labels):
            crop = V.crop_label(face.image, b)
            cv2.imwrite(str(pdir / f"label_{face_name}_{i}.jpg"), crop)
            texts.append(ocr.read(V.ocr_crop(face.image, b), ocfg["languages"]))
        per_face[face_name] = {"quality": q, "labels": labels, "ocr": texts}
        info_face[face_name] = face.image
        if not q["ok"]:
            spec.notes.append(f"{face_name}: qualità immagine insufficiente / poor image quality: {q['issues']}")
        parts.append(image_part(V.encode_jpeg(cv2.resize(face.image, (640, int(640 * face.image.shape[0] / 800))))))
        listing.append(f"FACE {face_name}: " + ("; ".join(
            f"[index {i}] region {_region(b.cx, b.cy)}, OCR: {t['text'][:600]!r}" for i, (b, t) in enumerate(zip(labels, texts)))
            or "no label detected"))
    parts.append({"type": "text", "text": "Photos in this order: " + ", ".join(photos) + ".\n" + "\n".join(listing)})

    grok: dict[str, Any] = {}
    try:
        grok, meta = ask_json(api_key, cfg["grok"]["vision_model"], SYSTEM, parts, SCHEMA, "model_pack",
                              cfg["grok"]["timeout_s"])
        spec.source["grok"] = meta
    except GrokVisionError as e:
        spec.source["grok_error"] = str(e)
        spec.notes.append(f"Grok non disponibile: interpretazione da confermare a mano / Grok unavailable: {e}")
    spec.source.update({"ocr": "tesseract " + ocfg["languages"], "cv": "opencv " + cv2.__version__,
                        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest()[:16]})
    spec.product_name = grok.get("product_name")
    spec.brand = grok.get("brand")
    spec.format = grok.get("format")
    spec.packaging_type = grok.get("packaging_type") or "unknown"
    spec.notes += [f"Grok: {w}" for w in grok.get("warnings", [])]

    g_by = {(g["face"], g["index"]): g for g in grok.get("labels", [])}
    used: set[str] = set()
    for face_name, info in per_face.items():
        for i, (b, t) in enumerate(zip(info["labels"], info["ocr"])):
            g = g_by.get((face_name, i), {})
            kind = g.get("kind") or "other"
            lid = face_name if face_name not in used else f"{face_name}_{i}"
            used.add(lid)
            l = LabelSpec(label_id=lid, face=face_name, kind=kind,
                          position={"cx": round(b.cx, 4), "cy": round(b.cy, 4), "w": round(b.w, 4), "h": round(b.h, 4),
                                    "angle": round(b.angle, 2)},
                          region=_region(b.cx, b.cy), orientation=g.get("orientation") or "upright",
                          reference_text=t.get("text", ""), grok_text=g.get("text_verbatim"),
                          product_name=g.get("product_name"), brand=g.get("brand"), has_logo=g.get("has_logo"),
                          mandatory_text=(kind == "ingredients_legal"), reference_crop=f"label_{face_name}_{i}.jpg",
                          uncertain=list(g.get("uncertain") or []))
            if not g:
                l.uncertain.append("kind")
            if V.label_sharpness(V.crop_label(info_face[face_name], b)) < qcfg["min_sharpness"]:
                l.uncertain.append("image_quality")
            if not t.get("ok") or t.get("confidence", 0) < 70:
                l.uncertain.append("reference_text")
            elif l.grok_text and ocr.ratio(l.reference_text, l.grok_text) < 95:
                l.uncertain.append("reference_text")      # OCR and Grok disagree: a human must decide
            if l.mandatory_text:
                derive_mandatory(l)
                g_inci = [ocr.normalise(x) for x in (g.get("inci") or [])]
                if not l.inci or (g_inci and g_inci != l.inci):
                    l.uncertain.append("inci")
            l.uncertain = sorted(set(l.uncertain))
            spec.labels.append(l)
        if not info["labels"]:
            spec.notes.append(f"{face_name}: nessuna etichetta rilevata / no label detected")
    store.save(spec)
    log.info("model pack read", extra={"data": {"product": pid, "labels": [l.label_id for l in spec.labels],
                                                "uncertain": {l.label_id: l.uncertain for l in spec.labels}}})
    return spec
