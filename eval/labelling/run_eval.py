"""Smart Labelling evaluation on the synthetic dataset (data/datasets/synthetic, scripts/make_synthetic_packs.py).

    python -m eval.labelling.run_eval              # with Grok (model-pack reading + second opinion)
    python -m eval.labelling.run_eval --no-grok    # deterministic CV + OCR only

For each product: read the model pack -> score the RAW specification against ground truth (before any human
edit) -> simulate the operator approval (tolerances 4 % / 3°, confirm uncertain fields; names typed in only if
the system did not extract them) -> verify 3 sheets -> inspect every test pack.
Most important metric: FALSE ACCEPTANCE = a non-compliant pack classified COMPLIANT.
Development products vs the HELD-OUT product (never used to tune thresholds) are reported separately.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.labelling import ocr
from app.labelling.service import LabellingService

DATA = ROOT / "data" / "datasets" / "synthetic"
RESULTS = ROOT / "eval" / "results"
TOL = {"position_pct": 4.0, "angle_deg": 3.0}   # the simulated operator's choice for these packs


def spec_score(spec, truth) -> dict[str, Any]:
    t_labels = {l["face"]: l for l in truth["labels"]}
    s_by_face = {l.face: l for l in spec.labels}
    out = {"label_count_ok": len(spec.labels) == len(truth["labels"]),
           "faces_ok": set(s_by_face) == set(t_labels),
           "product_name_ok": bool(spec.product_name) and ocr.contains(spec.product_name or "", truth["name"]) >= 90,
           "brand_ok": bool(spec.brand) and ocr.normalise(spec.brand) == ocr.normalise(truth["brand"])}
    back = s_by_face.get("back")
    t_back = t_labels["back"]["content"]
    out["kind_ok"] = bool(back and back.kind == "ingredients_legal" and s_by_face.get("front") and
                          s_by_face["front"].kind == "front_brand")
    out["inci_exact"] = bool(back and back.inci == [x.upper() for x in t_back["inci"]])
    out["legal_exact"] = bool(back and back.legal_text and ocr.normalise(t_back["legal_text"]) == ocr.normalise(back.legal_text))
    errs = []
    for face, tl in t_labels.items():
        sl = s_by_face.get(face)
        if sl:
            errs.append(max(abs(sl.position["cx"] - tl["position"]["cx"]), abs(sl.position["cy"] - tl["position"]["cy"])) * 100)
    out["position_error_pct_max"] = round(max(errs), 2) if errs else None
    out["uncertain_fields"] = {l.label_id: l.uncertain for l in spec.labels}
    return out


def run(use_grok: bool) -> dict[str, Any]:
    api_key = os.getenv("XAI_API_KEY") if use_grok else None
    svc = LabellingService(api_key, root=Path(tempfile.mkdtemp(prefix="voce_eval_")))
    products, cases_out = [], []
    for pdir in sorted(p for p in DATA.iterdir() if p.is_dir()):
        truth = json.loads((pdir / "truth.json").read_text())
        cases = json.loads((pdir / "cases.json").read_text())
        t0 = time.time()
        spec = svc.learn({f: (pdir / "reference" / f"{f}.jpg").read_bytes() for f in ("front", "back")}, truth["product_id"])
        learn_s = time.time() - t0
        score = spec_score(spec, truth)
        # simulated operator approval
        edits = {}
        for l in spec.labels:
            e: dict[str, Any] = {}
            if l.face == "front":
                e["kind"] = "front_brand"
                if not l.product_name:
                    e["product_name"] = truth["name"]
                if not l.brand:
                    e["brand"] = truth["brand"]
            if l.face == "back" and l.kind != "ingredients_legal":
                e["kind"] = "ingredients_legal"
            if e:
                edits[l.label_id] = e
        try:
            spec = svc.approve(spec.product_id, operator="eval", tolerances={l.label_id: TOL for l in spec.labels},
                               confirmed={l.label_id: list(l.uncertain) for l in spec.labels}, edits=edits)
        except Exception as e:   # the model pack could not be turned into an approvable spec: nothing is inspected
            print(f"  {truth['product_id']}: SPEC NOT APPROVABLE ({e}) -> no inspection possible (safe failure)", flush=True)
            products.append({"product": truth["product_id"], "split": truth["split"], "spec": score, "sheets": {},
                             "learn_seconds": round(learn_s, 1), "spec_failed": str(e)})
            for c in cases:
                cases_out.append({"product": truth["product_id"], "split": truth["split"], "case": c["case"],
                                  "defect": c["defect"], "expected": c["expected"], "decision": "NOT_INSPECTED", "codes": [],
                                  "explanation_it": "", "explanation_en": "", "grok": None, "seconds": 0})
            continue
        sheets = {}
        for sh, exp in (("sheet", "MATCH"), ("sheet_wrong_product", "MISMATCH"), ("sheet_missing_back", "MISMATCH")):
            r = svc.verify_sheet(spec.product_id, (pdir / "reference" / f"{sh}.jpg").read_bytes())
            sheets[sh] = {"expected": exp, "got": r["decision"], "ok": r["decision"] == exp}
        for c in cases:
            t1 = time.time()
            r = svc.inspect(spec.product_id, {f: (pdir / "test" / f"{c['case']}_{f}.jpg").read_bytes() for f in ("front", "back")},
                            use_grok=use_grok)
            codes = sorted({f["code"] for f in r["findings"]})
            cases_out.append({"product": truth["product_id"], "split": truth["split"], "case": c["case"],
                              "defect": c["defect"], "expected": c["expected"], "decision": r["decision"], "codes": codes,
                              "explanation_it": r["explanation"]["it"], "explanation_en": r["explanation"]["en"],
                              "grok": (r.get("grok_opinion") or {}).get("verdict"), "seconds": round(time.time() - t1, 2)})
            print(f"  {truth['product_id']:24} {c['case']:13} exp {c['expected']:16} got {r['decision']:16} {codes}", flush=True)
        products.append({"product": truth["product_id"], "split": truth["split"], "spec": score, "sheets": sheets,
                         "learn_seconds": round(learn_s, 1), "grok_meta": spec.source.get("grok")})
    return {"products": products, "cases": cases_out}


def metrics(cases: list[dict[str, Any]], products: list[dict[str, Any]]) -> dict[str, Any]:
    bad = [c for c in cases if c["expected"] == "NON_COMPLIANT"]
    good = [c for c in cases if c["expected"] == "COMPLIANT"]
    rev = [c for c in cases if c["expected"] == "REVIEW_REQUIRED"]
    phys = {"crease", "bubble"}
    det_codes = {"defect", "defect_near"}
    tp = sum(1 for c in cases if c["defect"] in phys and det_codes & set(c["codes"]))
    fp = sum(1 for c in cases if c["defect"] not in phys and "defect" in c["codes"])
    fn = sum(1 for c in cases if c["defect"] in phys and not det_codes & set(c["codes"]))
    sorted_ok = sum(1 for c in cases if (c["expected"] == "COMPLIANT") == (c["decision"] == "COMPLIANT")
                    and (c["decision"] != "COMPLIANT" or c["expected"] == "COMPLIANT"))
    n = lambda xs: len(xs) or 1  # noqa: E731
    sp = [p["spec"] for p in products]
    return {
        "n_cases": len(cases), "n_products": len(products),
        "false_acceptance_rate": {"value": sum(c["decision"] == "COMPLIANT" for c in bad + rev) / n(bad + rev),
                                  "count": sum(c["decision"] == "COMPLIANT" for c in bad + rev), "of": len(bad + rev)},
        "false_rejection_rate": {"value": sum(c["decision"] == "NON_COMPLIANT" for c in good) / n(good),
                                 "count": sum(c["decision"] == "NON_COMPLIANT" for c in good), "of": len(good)},
        "compliant_sent_to_review": {"count": sum(c["decision"] == "REVIEW_REQUIRED" for c in good), "of": len(good)},
        "review_required_rate": {"value": sum(c["decision"] == "REVIEW_REQUIRED" for c in cases) / n(cases)},
        "exact_decision_accuracy": {"value": sum(c["decision"] == c["expected"] for c in cases) / n(cases)},
        "sorting_accuracy": {"value": sorted_ok / n(cases), "note": "accept iff truly compliant; reject/hold otherwise"},
        "defect_detection": {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
                             "tp": tp, "fp": fp, "fn": fn},
        "spec_extraction": {k: sum(1 for s in sp if s[k]) / n(sp) for k in
                            ("label_count_ok", "faces_ok", "product_name_ok", "brand_ok", "kind_ok", "inci_exact", "legal_exact")},
        "spec_position_error_pct_max": max((s["position_error_pct_max"] or 0) for s in sp) if sp else None,
        "sheet_verification_accuracy": sum(v["ok"] for p in products for v in p["sheets"].values()) /
                                       n([1 for p in products for _ in p["sheets"]]),
        "products_spec_failed": [p["product"] for p in products if p.get("spec_failed")],
        "median_inspection_s": statistics.median(c["seconds"] for c in cases) if cases else None,
    }


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-grok", action="store_true")
    a = ap.parse_args()
    use_grok = not a.no_grok and bool(os.getenv("XAI_API_KEY"))
    res = run(use_grok)
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "dataset": "synthetic (rendered, fictional brands)",
           "grok": use_grok, "operator_tolerances": TOL,
           "dev": metrics([c for c in res["cases"] if c["split"] == "dev"], [p for p in res["products"] if p["split"] == "dev"]),
           "heldout": metrics([c for c in res["cases"] if c["split"] == "heldout"], [p for p in res["products"] if p["split"] == "heldout"]),
           "all": metrics(res["cases"], res["products"]), **res}
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = "grok" if use_grok else "nogrok"
    (RESULTS / f"labelling_{stamp}_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (RESULTS / "labelling_latest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for part in ("dev", "heldout"):
        m = out[part]
        print(f"\n== {part}: {m['n_cases']} packs, {m['n_products']} products")
        print(f"  FALSE ACCEPTANCE {m['false_acceptance_rate']['count']}/{m['false_acceptance_rate']['of']} | false rejection "
              f"{m['false_rejection_rate']['count']}/{m['false_rejection_rate']['of']} | compliant->review "
              f"{m['compliant_sent_to_review']['count']}/{m['compliant_sent_to_review']['of']} | exact {m['exact_decision_accuracy']['value']:.0%}")
        print(f"  defects P={m['defect_detection']['precision']} R={m['defect_detection']['recall']} | sheets {m['sheet_verification_accuracy']:.0%}"
              f" | spec {m['spec_extraction']} | pos err max {m['spec_position_error_pct_max']}%")


if __name__ == "__main__":
    main()
