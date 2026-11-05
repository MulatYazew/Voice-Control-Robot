"""Identification on rendered frames. Results are synthetic. A real camera is a separate file.

False acceptances here mean a row this identifier marked COMPLIANT. An unknown product and a
product with no approved specification are not checkable, so they are not acceptances.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.config import ROOT
from app.perception.identify import rows_from_image
from app.perception.sim_station import BANNER, SimStation, catalog_from_dataset

VARIANTS = ("correct_1", "correct_2", "correct_3", "crooked", "shifted", "creased", "bubbled",
            "wrong_inci", "wrong_label", "missing_back", "swapped", "blurred")
DEFECTS = {"crooked", "shifted", "creased", "bubbled", "wrong_inci", "wrong_label", "missing_back", "swapped", "blurred"}


def main() -> None:
    station = SimStation()
    catalog = catalog_from_dataset(ROOT)
    rows_out = []
    false_acceptances = 0
    for product in catalog:
        for variant in VARIANTS:
            for difficulty in ("normal", "hard"):
                station.difficulty = difficulty
                station.zones = {"sinistra": {"product": product["id"], "variant": variant, "uid": f"{product['id']}:{variant}"},
                                 "centro": None, "destra": None}
                station.uncertain = False
                started = time.perf_counter()
                found = rows_from_image(station.render(), catalog)
                elapsed = time.perf_counter() - started
                left = found[0]
                decision = (left.get("conformita") or {}).get("decisione")
                if decision == "COMPLIANT":
                    false_acceptances += 1
                rows_out.append({
                    "product": product["id"], "variant": variant, "difficulty": difficulty,
                    "stato": left["stato"], "tipo": (left.get("tipo") or {}).get("valore"),
                    "prodotto": (left.get("prodotto") or {}).get("id"),
                    "fonte": (left.get("prodotto") or {}).get("fonte"),
                    "decisione": decision, "seconds": round(elapsed, 3),
                    "sorgente": BANNER,
                })
    station.load_preset("unseen")
    station.difficulty = "normal"
    unseen = rows_from_image(station.render(), catalog)[0]
    payload = {
        "label": "synthetic",
        "banner": BANNER,
        "camera": "rendered station, not a real camera",
        "products": len(catalog),
        "rows": len(rows_out),
        "type_correct": sum(1 for r in rows_out if r["tipo"]),
        "product_correct": sum(1 for r in rows_out if r["prodotto"]),
        "unknown_rows": sum(1 for r in rows_out if r["fonte"] == "unknown product"),
        "false_acceptances": false_acceptances,
        "unseen": {"stato": unseen["stato"], "prodotto": unseen.get("prodotto"), "tipo": unseen.get("tipo"),
                   "conformita": unseen.get("conformita")},
        "note": "Compliance is 'not checkable' unless an approved specification is loaded. A COMPLIANT count above 0 is a false acceptance.",
        "detail": rows_out,
    }
    out = ROOT / "eval" / "results" / f"perception_synthetic_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print("false_acceptances", false_acceptances, "rows", len(rows_out))


if __name__ == "__main__":
    main()
