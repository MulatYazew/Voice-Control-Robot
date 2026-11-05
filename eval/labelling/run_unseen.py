"""Rehearsal on the committed unseen pack. Writes a result file only after a real run.

    python scripts/make_synthetic_packs.py --heldout
    python -m eval.labelling.run_unseen

Does not call Grok. Does not retune thresholds. The arm cycle is attempted with a folder camera
that replays the pack photos; the log says whether that completed without an upload.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import cv2

from app.config import ROOT, load_settings
from app.labelling.service import LabellingService
from app.labelling.sorting import SortingCycle
from app.monitoring.events import EventBus, History, Metrics
from app.perception.camera import CameraService
from app.robot.sim import SimAdapter
from app.safety.gate import Gate
from app.skills.registry import Registry
from eval.labelling.run_eval import TOL, spec_score

UNSEEN_DIR = ROOT / "data" / "datasets" / "unseen"
UNSEEN_SEED = 20261105  # same constant as scripts/make_synthetic_packs.py

RESULTS = ROOT / "eval" / "results"


def main() -> None:
    pack = UNSEEN_DIR / "mirto_balsamo_barba"
    if not (pack / "truth.json").exists():
        raise SystemExit("unseen pack is not on disk. Run: python scripts/make_synthetic_packs.py --heldout")
    truth = json.loads((pack / "truth.json").read_text())
    cases = json.loads((pack / "cases.json").read_text())
    svc = LabellingService(None, root=Path(tempfile.mkdtemp(prefix="voce_unseen_")))
    t0 = time.time()
    spec = svc.learn({f: (pack / "reference" / f"{f}.jpg").read_bytes() for f in ("front", "back")}, truth["product_id"])
    learn_s = time.time() - t0
    score = spec_score(spec, truth)
    edits = {}
    for label in spec.labels:
        edit: dict = {}
        if label.face == "front":
            edit["kind"] = "front_brand"
            if not label.product_name:
                edit["product_name"] = truth["name"]
            if not label.brand:
                edit["brand"] = truth["brand"]
        if label.face == "back" and label.kind != "ingredients_legal":
            edit["kind"] = "ingredients_legal"
        if edit:
            edits[label.label_id] = edit
    spec = svc.approve(spec.product_id, operator="eval-unseen", tolerances={l.label_id: TOL for l in spec.labels},
                       confirmed={l.label_id: list(l.uncertain) for l in spec.labels}, edits=edits)
    sheets = {}
    for name, expected in (("sheet", "MATCH"), ("sheet_wrong_product", "MISMATCH"), ("sheet_missing_back", "MISMATCH")):
        got = svc.verify_sheet(spec.product_id, (pack / "reference" / f"{name}.jpg").read_bytes())
        sheets[name] = {"expected": expected, "got": got["decision"], "sheet_state": got.get("sheet_state")}
    inspected = []
    for case in cases:
        photos = {f: (pack / "test" / f"{case['case']}_{f}.jpg").read_bytes() for f in ("front", "back")}
        result = svc.inspect(spec.product_id, photos, use_grok=False)
        inspected.append({"case": case["case"], "expected": case["expected"], "decision": result["decision"],
                          "defect": case.get("defect")})
    bad = [c for c in inspected if c["expected"] != "COMPLIANT"]
    false_accept = [c["case"] for c in bad if c["decision"] == "COMPLIANT"]
    good = [c for c in inspected if c["expected"] == "COMPLIANT"]
    false_reject = [c["case"] for c in good if c["decision"] == "NON_COMPLIANT"]
    reviews = [c["case"] for c in inspected if c["decision"] == "REVIEW_REQUIRED"]
    # The cycle may start only against the last sheet that matched. A failed sheet must not stay selected.
    matched = svc.verify_sheet(spec.product_id, (pack / "reference" / "sheet.jpg").read_bytes())
    cycle = asyncio.run(_cycle(svc, pack, spec.product_id))
    cycle["sheet_before_cycle"] = {"decision": matched["decision"], "sheet_state": matched.get("sheet_state")}
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "seed": UNSEEN_SEED, "product": truth["product_id"],
           "dataset": "synthetic unseen, rendered, not a real pack", "grok": False,
           "spec": score, "seconds_photos_to_approved_spec": round(learn_s, 1), "sheets": sheets,
           "false_acceptances": len(false_accept), "false_acceptance_cases": false_accept,
           "false_rejections": len(false_reject), "review": reviews, "cases": inspected,
           "cycle": cycle}
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"unseen_pack_{stamp}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({"file": str(path), "false_acceptances": out["false_acceptances"],
                      "false_rejections": out["false_rejections"], "review": len(reviews),
                      "cycle": cycle}, ensure_ascii=False, indent=1))


async def _cycle(svc: LabellingService, pack: Path, product_id: str) -> dict:
    """One automatic cycle on the first compliant photo. Reports the real phase and intervention count."""
    settings = load_settings("sim", env_file=None)
    gate = Gate(settings, Registry(settings), SimAdapter(settings.poses["home"]["joints"], time_scale=20),
                EventBus(), Metrics(path=None), History())
    await gate.start()
    frame = cv2.imread(str(pack / "test" / "correct_1_front.jpg"))
    cam = CameraService({"available": True, "source": "browser", "faces_visible_from_station": ["front", "back"], "views": {}})
    cam.available = True
    cam.source = "folder:unseen"
    cam.push_frame(frame)
    cycle = SortingCycle(gate, svc, gate.bus, camera=cam)
    svc.cfg.setdefault("cycle", {})["settle_s"] = 0
    started = await cycle.start(product_id, "eval-unseen", True)
    if not started.get("ok"):
        await gate.shutdown()
        return {"started": False, "problems": started.get("problems"), "interventions": cycle.interventions}
    for _ in range(400):
        if cycle.phase in ("DONE", "ABORTED", "HOLD_REVIEW"):
            break
        await asyncio.sleep(0.02)
    report = {"started": True, "phase": cycle.phase, "decision": (cycle.result or {}).get("decision"),
              "interventions": cycle.interventions, "accepted": cycle.counters["accepted"],
              "rejected": cycle.counters["rejected"], "destination_trays": sorted(gate.world.trays_at)}
    await gate.shutdown()
    return report


if __name__ == "__main__":
    main()
