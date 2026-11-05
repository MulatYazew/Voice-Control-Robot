"""Conservative compliance decision for one pack against an APPROVED specification.

Decision = most severe finding over all required labels:  COMPLIANT < REVIEW_REQUIRED < NON_COMPLIANT.
  * Mandatory text (INCI / legal) must match the approved reference exactly; uncertain OCR -> REVIEW.
  * Any measurement inside the "review margin" of a limit -> REVIEW.
  * Missing image / poor quality / no OCR -> REVIEW.  Never COMPLIANT without a valid inspection.
  * Grok's opinion can only DOWNGRADE (COMPLIANT -> REVIEW_REQUIRED), never upgrade.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import ocr
from . import vision as V
from .grok_vision import GrokVisionError, ask_json, image_part
from .specification import LabelSpec, ProductSpec

COMPLIANT, REVIEW, NON_COMPLIANT = "COMPLIANT", "REVIEW_REQUIRED", "NON_COMPLIANT"
_RANK = {COMPLIANT: 0, REVIEW: 1, NON_COMPLIANT: 2}

# finding code -> (severity, Italian, English)
MSG = {
    "ok": (COMPLIANT, "conforme", "compliant"),
    "missing_image": (REVIEW, "faccia {face} non fotografata", "face {face} not photographed"),
    "image_quality": (REVIEW, "qualità immagine insufficiente ({detail})", "insufficient image quality ({detail})"),
    "ocr_unavailable": (REVIEW, "OCR non disponibile: testo non verificabile", "OCR unavailable: text cannot be verified"),
    "missing": (NON_COMPLIANT, "etichetta {label} mancante sulla faccia {face}", "label {label} missing on face {face}"),
    "wrong_face": (NON_COMPLIANT, "sulla faccia {face} c'è l'etichetta {other} invece di {label}",
                   "face {face} carries label {other} instead of {label}"),
    "wrong_label": (NON_COMPLIANT, "etichetta {label} errata: il testo non corrisponde al modello ({detail})",
                    "label {label} is wrong: text does not match the model ({detail})"),
    "wrong_text": (NON_COMPLIANT, "etichetta {label}: testo INCI/legale diverso dal modello ({detail})",
                   "label {label}: INCI/legal text differs from the model ({detail})"),
    "text_uncertain": (REVIEW, "etichetta {label}: testo non verificato con certezza ({detail})",
                       "label {label}: text could not be verified with certainty ({detail})"),
    "misaligned": (NON_COMPLIANT, "etichetta {label} spostata di {value:.1f}% (tolleranza {limit:.1f}%)",
                   "label {label} displaced by {value:.1f}% (tolerance {limit:.1f}%)"),
    "misaligned_near": (REVIEW, "etichetta {label} spostata di {value:.1f}%, vicino al limite {limit:.1f}%",
                        "label {label} displaced by {value:.1f}%, close to the {limit:.1f}% limit"),
    "crooked": (NON_COMPLIANT, "etichetta {label} storta di {value:.1f}° (tolleranza {limit:.1f}°)",
                "label {label} crooked by {value:.1f}° (tolerance {limit:.1f}°)"),
    "crooked_near": (REVIEW, "etichetta {label} ruotata di {value:.1f}°, vicino al limite {limit:.1f}°",
                     "label {label} rotated {value:.1f}°, close to the {limit:.1f}° limit"),
    "size": (NON_COMPLIANT, "etichetta {label} di dimensioni diverse dal modello ({detail})",
             "label {label} size differs from the model ({detail})"),
    "defect": (NON_COMPLIANT, "etichetta {label}: difetto visibile ({detail})", "label {label}: visible defect ({detail})"),
    "defect_near": (REVIEW, "etichetta {label}: possibile difetto ({detail})", "label {label}: possible defect ({detail})"),
    "visual_mismatch": (REVIEW, "etichetta {label}: aspetto diverso dal modello (similarità {value:.2f})",
                        "label {label}: appearance differs from the model (similarity {value:.2f})"),
    "unexpected_label": (REVIEW, "faccia {face}: etichetta non prevista dal modello", "face {face}: label not in the model"),
    "grok_concern": (REVIEW, "Grok segnala: {detail}", "Grok flags: {detail}"),
}
DEFECT_IT = {"crease": "piega", "bubble": "bolla", "damage": "danno"}


@dataclass
class Finding:
    code: str
    label: str | None = None
    face: str | None = None
    value: float | None = None
    limit: float | None = None
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> str:
        return MSG[self.code][0]

    def text(self, lang: str) -> str:
        tpl = MSG[self.code][1 if lang == "it" else 2]
        return tpl.format(label=self.label, face=self.face, value=self.value or 0.0, limit=self.limit or 0.0,
                          detail=self.detail, other=self.extra.get("other"))

    def public(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "label": self.label, "face": self.face,
                "value": self.value, "limit": self.limit, "detail": self.detail, "extra": self.extra,
                "it": self.text("it"), "en": self.text("en")}


def _worst(findings: list[Finding]) -> str:
    return max((f.severity for f in findings), key=_RANK.__getitem__, default=COMPLIANT)


def _match(spec_l: LabelSpec, boxes: list[V.LabelBox], max_dist: float) -> V.LabelBox | None:
    p = spec_l.position
    best, bd = None, 1e9
    for b in boxes:
        d = max(abs(b.cx - p["cx"]), abs(b.cy - p["cy"]))
        size_ok = 0.6 < (b.w * b.h) / max(p["w"] * p["h"], 1e-6) < 1.6
        if d < bd and d <= max_dist and size_ok:
            best, bd = b, d
    return best


def _check_text(l: LabelSpec, got: dict[str, Any], spec: ProductSpec, cfg: dict[str, Any]) -> list[Finding]:
    ocfg = cfg["ocr"]
    text, conf = got.get("text", ""), got.get("confidence", 0.0)
    if l.mandatory_text:
        ref_inci, got_inci = l.inci or [], ocr.split_inci(text)
        if ocr.normalise(l.reference_text) == ocr.normalise(text) or (ref_inci and got_inci == ref_inci and
                                                                       _legal_ok(l, text)):
            return []
        r = ocr.ratio(l.reference_text, text)
        # whole-ingredient differences read with high confidence = a real, different INCI
        missing = [x for x in ref_inci if x not in got_inci]
        extra = [x for x in got_inci if x not in ref_inci]
        from rapidfuzz import fuzz
        real_sub = bool(missing or extra) and conf >= 85 and all(
            max((fuzz.ratio(x, y) for y in ref_inci), default=0) < 80 for x in extra)
        detail = (f"mancano/missing {missing}, presenti/found {extra}" if (missing or extra)
                  else f"similarità {r:.0f}%")
        if r < ocfg["review_min_ratio"]:
            return [_other_label_or(l, text, spec, "wrong_label", detail)]
        if real_sub:
            return [Finding("wrong_text", l.label_id, l.face, detail=detail, extra={"diff": ocr.diff(l.reference_text, text)})]
        return [Finding("text_uncertain", l.label_id, l.face, detail=detail, extra={"diff": ocr.diff(l.reference_text, text)})]
    # non-mandatory (front/brand) label: product name and brand must be present
    wants = [x for x in (l.product_name, l.brand) if x]
    if not wants:
        r = ocr.ratio(l.reference_text, text)
        if r >= ocfg["name_min_ratio"]:
            return []
        return [_other_label_or(l, text, spec, "wrong_label", f"similarità {r:.0f}%") if r < ocfg["review_min_ratio"]
                else Finding("text_uncertain", l.label_id, l.face, detail=f"similarità {r:.0f}%")]
    scores = {w: ocr.contains(text, w) for w in wants}
    worst = min(scores.values())
    if worst >= ocfg["name_min_ratio"]:
        return []
    detail = ", ".join(f"«{w}» {s:.0f}%" for w, s in scores.items())
    if worst < ocfg["review_min_ratio"]:
        return [_other_label_or(l, text, spec, "wrong_label", detail)]
    return [Finding("text_uncertain", l.label_id, l.face, detail=detail)]


def _legal_ok(l: LabelSpec, text: str) -> bool:
    if not l.legal_text:
        return True
    return ocr.normalise(l.legal_text) in ocr.normalise(text)


def _other_label_or(l: LabelSpec, text: str, spec: ProductSpec, code: str, detail: str) -> Finding:
    """If the text belongs to ANOTHER label of this product, it's on the wrong face."""
    for o in spec.labels:
        if o.label_id != l.label_id and o.reference_text and ocr.ratio(o.reference_text, text) >= 85:
            return Finding("wrong_face", l.label_id, l.face, detail=detail, extra={"other": o.label_id})
    return Finding(code, l.label_id, l.face, detail=detail)


def inspect(spec: ProductSpec, photos: dict[str, np.ndarray], cfg: dict[str, Any], product_dir: Path, *,
            out_dir: Path, api_key: str | None = None, use_grok: bool = True) -> dict[str, Any]:
    if spec.approval_status != "approved":
        raise ValueError("la specifica non è approvata / specification not approved")
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    det, qcfg, dcfg = cfg["detection"], cfg["image_quality"], cfg["defects"]
    margin = dcfg["review_margin"]
    findings: list[Finding] = []
    per_label: dict[str, Any] = {}
    faces_info: dict[str, Any] = {}
    face_imgs: dict[str, np.ndarray] = {}
    if not ocr.available():
        findings.append(Finding("ocr_unavailable"))

    for face_name in sorted({l.face for l in spec.labels} | set(photos)):
        if face_name not in photos:
            continue
        face = V.find_face(photos[face_name], qcfg["min_face_area_frac"])
        q = V.quality(face, qcfg, photos[face_name])
        cv2.imwrite(str(out_dir / f"face_{face_name}.jpg"), face.image)
        boxes = V.detect_labels(face.image, det["label_min_area_frac"], det["label_color_distance"], face.bg_lab) if q["ok"] else []
        faces_info[face_name] = {"quality": q, "labels_detected": [b.public() for b in boxes]}
        face_imgs[face_name] = face.image
        if not q["ok"]:
            findings.append(Finding("image_quality", face=face_name, detail=", ".join(q["issues"])))
        faces_info[face_name]["_boxes"] = boxes

    for l in spec.labels:
        if not l.required:
            continue
        info = faces_info.get(l.face)
        if info is None:
            findings.append(Finding("missing_image", l.label_id, l.face))
            continue
        if not info["quality"]["ok"]:
            continue  # already REVIEW for this face
        boxes = info["_boxes"]
        b = _match(l, boxes, det["match_max_center_dist"])
        res: dict[str, Any] = {"face": l.face}
        per_label[l.label_id] = res
        if b is None:
            blurred = [x for x in boxes if V.label_sharpness(V.crop_label(face_imgs[l.face], x)) < qcfg["min_sharpness"]]
            if blurred:   # a region we cannot read or measure: never conclude "missing"/"wrong" from it
                findings.append(Finding("image_quality", l.label_id, l.face, detail="regione etichetta sfocata / label region blurred"))
                for x in blurred:
                    boxes.remove(x)
                res["detected"] = None
                continue
            # something else on this face where the label should be?
            if boxes:
                crop = V.ocr_crop(face_imgs[l.face], boxes[0])
                got = ocr.read(crop, cfg["ocr"]["languages"])
                f = _other_label_or(l, got["text"], spec, "missing", "")
                findings.append(f)
                res["ocr"] = got
            else:
                findings.append(Finding("missing", l.label_id, l.face))
            res["detected"] = None
            continue
        boxes.remove(b)
        res["detected"] = b.public()
        crop = V.crop_label(face_imgs[l.face], b)
        sharp = V.label_sharpness(crop)
        res["sharpness"] = round(sharp, 1)
        if sharp < qcfg["min_sharpness"]:
            # cannot read or measure this label reliably: hold for review, never accept
            findings.append(Finding("image_quality", l.label_id, l.face, detail=f"etichetta sfocata / label blurred ({sharp:.0f})"))
            continue
        p = l.position
        # --- placement (relative, uncalibrated camera) ---
        off = max(abs(b.cx - p["cx"]), abs(b.cy - p["cy"])) * 100
        ang = abs(b.angle - p.get("angle", 0.0))
        tol = l.tolerance
        res.update(offset_pct=round(off, 2), angle_deg=round(ang, 2))
        if off > tol.position_pct:
            findings.append(Finding("misaligned", l.label_id, l.face, off, tol.position_pct))
        elif off > margin * tol.position_pct:
            findings.append(Finding("misaligned_near", l.label_id, l.face, off, tol.position_pct))
        if ang > tol.angle_deg:
            findings.append(Finding("crooked", l.label_id, l.face, ang, tol.angle_deg))
        elif ang > margin * tol.angle_deg:
            findings.append(Finding("crooked_near", l.label_id, l.face, ang, tol.angle_deg))
        size_ratio = (b.w * b.h) / max(p["w"] * p["h"], 1e-6)
        if not 0.8 < size_ratio < 1.25:
            findings.append(Finding("size", l.label_id, l.face, detail=f"area ×{size_ratio:.2f}"))
        # --- content ---
        cv2.imwrite(str(out_dir / f"label_{l.label_id}.jpg"), crop)
        got = ocr.read(V.ocr_crop(face_imgs[l.face], b), cfg["ocr"]["languages"]) if ocr.available() else {}
        res["ocr"] = got
        text_findings = _check_text(l, got, spec, cfg) if got else []
        findings += text_findings
        # --- physical defects (aligned difference vs the model label) ---
        ref_path = product_dir / (l.reference_crop or "")
        if l.reference_crop and ref_path.exists():
            ref = cv2.imread(str(ref_path))
            cmp = V.compare_crops(ref, crop, dcfg)
            res["defects"] = cmp
            kinds = sorted({d["kind"] for d in cmp["defects"]})
            detail = ", ".join(f"{DEFECT_IT[k]}/{k}" for k in kinds) + f", area {cmp['defect_area_frac'] * 100:.2f}%"
            # a proven text difference (wrong label / face / INCI) explains the image residual: not physical damage
            if not any(f.code in ("wrong_label", "wrong_face", "wrong_text") for f in text_findings):
                if cmp["ncc"] < dcfg["visual_identity_min_ncc"]:
                    findings.append(Finding("visual_mismatch", l.label_id, l.face, cmp["ncc"]))
                if cmp["defect_area_frac"] > dcfg["max_defect_area_frac"] or "crease" in kinds:
                    findings.append(Finding("defect", l.label_id, l.face, detail=detail, extra={"defects": cmp["defects"]}))
                elif cmp["defect_area_frac"] > margin * dcfg["max_defect_area_frac"]:
                    findings.append(Finding("defect_near", l.label_id, l.face, detail=detail))
    for face_name, info in faces_info.items():
        if info["quality"]["ok"] and info["_boxes"]:   # leftover labels not in the model
            findings.append(Finding("unexpected_label", face=face_name))
        info.pop("_boxes", None)

    decision = _worst(findings)
    grok_opinion = None
    if use_grok and cfg["grok"].get("second_opinion") and decision == COMPLIANT and api_key:
        grok_opinion = _grok_second_opinion(spec, product_dir, face_imgs, cfg, api_key)
        if grok_opinion.get("verdict") in ("non_compliant", "uncertain"):
            findings.append(Finding("grok_concern", detail=grok_opinion.get("reason_en") or grok_opinion.get("verdict", "")))
            decision = _worst(findings)
    if not findings:
        findings.append(Finding("ok"))
    pub = [f.public() for f in findings]
    result = {
        "inspection_id": out_dir.name, "product_id": spec.product_id, "spec_version": spec.version, "decision": decision,
        "findings": pub, "labels": per_label, "faces": faces_info, "grok_opinion": grok_opinion,
        "explanation": {lang: _explain(decision, [f for f in findings if f.severity != COMPLIANT], lang) for lang in ("it", "en")},
        "duration_s": round(time.time() - t0, 2), "ts": time.time(),
    }
    return result


def _explain(decision: str, issues: list[Finding], lang: str) -> str:
    head = {COMPLIANT: ("Prodotto conforme al modello.", "Compliant product: matches the model."),
            NON_COMPLIANT: ("Prodotto non conforme:", "Non-compliant product:"),
            REVIEW: ("Revisione richiesta, il prodotto non viene accettato:", "Review required, the product is not accepted:")}[decision]
    h = head[0 if lang == "it" else 1]
    if not issues:
        return h
    return h + " " + "; ".join(f.text(lang) for f in issues) + "."


GROK_SYSTEM = """You compare photos of a cosmetic pack with photos of its approved MODEL pack (same faces).
Look ONLY for visible problems: missing/wrong/swapped label, crooked label, crease, bubble, tear, wrong text.
Be conservative: if unsure, answer "uncertain". Do not invent problems you cannot see. Answer briefly."""
GROK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "reason_it", "reason_en"],
               "properties": {"verdict": {"type": "string", "enum": ["compliant", "non_compliant", "uncertain"]},
                              "reason_it": {"type": "string"}, "reason_en": {"type": "string"}}}


def _grok_second_opinion(spec: ProductSpec, product_dir: Path, faces: dict[str, np.ndarray], cfg: dict[str, Any],
                         api_key: str) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for face_name, img in faces.items():
        ref = product_dir / spec.faces.get(face_name, "")
        if ref.exists():
            parts.append({"type": "text", "text": f"MODEL face {face_name}:"})
            parts.append(image_part(V.encode_jpeg(cv2.resize(cv2.imread(str(ref)), (512, int(512 * img.shape[0] / img.shape[1]))))))
        parts.append({"type": "text", "text": f"PACK UNDER INSPECTION face {face_name}:"})
        parts.append(image_part(V.encode_jpeg(cv2.resize(img, (512, int(512 * img.shape[0] / img.shape[1]))))))
    try:
        out, meta = ask_json(api_key, cfg["grok"]["vision_model"], GROK_SYSTEM, parts, GROK_SCHEMA, "inspection",
                             cfg["grok"]["timeout_s"])
        return {**out, "meta": meta}
    except GrokVisionError as e:
        # Grok unavailable: the deterministic result stands (Grok can only ever downgrade)
        return {"verdict": "unavailable", "error": str(e)}


def new_inspection_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
