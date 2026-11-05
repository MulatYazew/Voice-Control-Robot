"""Generate a SYNTHETIC labelling dataset (fictional brands, dummy labels) for development and evaluation.

    python scripts/make_synthetic_packs.py            # -> data/datasets/synthetic/

Per product: reference photos (front/back) of the model pack, a label sheet, a wrong sheet, and test packs:
correct (x3, small jitter), crooked, shifted, creased, bubbled, wrong_inci, wrong_label, missing_back,
swapped, blurred. Ground truth in truth.json / cases.json.
Images are rendered, not photographed: results on this set are NOT evidence of real-camera performance.
"""

from __future__ import annotations

import json
import math
import random
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "datasets" / "synthetic"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

PRODUCTS = [
    {"id": "lumen_crema_viso", "split": "dev", "brand": "ATELIER LUMEN", "name": "Crema Viso Idratante", "size": "50 ml",
     "pack": (620, 820), "pack_rgb": (38, 58, 92), "label_rgb": (248, 244, 236), "logo": "circle",
     "front": {"cx": 0.5, "cy": 0.38, "w": 0.70, "h": 0.30}, "back": {"cx": 0.5, "cy": 0.55, "w": 0.80, "h": 0.52},
     "inci": ["AQUA", "GLYCERIN", "BUTYROSPERMUM PARKII BUTTER", "CETEARYL ALCOHOL", "SQUALANE", "TOCOPHEROL",
              "PARFUM", "PHENOXYETHANOL"],
     "legal": "Prodotto in Italia. Lotto e PAO 12M sulla confezione. Solo per uso esterno."},
    {"id": "lumen_siero_notte", "split": "dev", "brand": "ATELIER LUMEN", "name": "Siero Notte Rigenerante",
     "size": "30 ml", "pack": (540, 860), "pack_rgb": (90, 30, 50), "label_rgb": (240, 236, 228), "logo": "diamond",
     "front": {"cx": 0.5, "cy": 0.62, "w": 0.74, "h": 0.26}, "back": {"cx": 0.5, "cy": 0.45, "w": 0.82, "h": 0.50},
     "inci": ["AQUA", "PROPANEDIOL", "NIACINAMIDE", "SODIUM HYALURONATE", "PANTHENOL", "XANTHAN GUM",
              "CITRIC ACID", "SODIUM BENZOATE"],
     "legal": "Prodotto in Italia. Conservare lontano dalla luce. Evitare il contatto con gli occhi."},
    {"id": "fiordaliso_olio_corpo", "split": "dev", "brand": "FIORDALISO", "name": "Olio Corpo Nutriente",
     "size": "100 ml", "pack": (500, 900), "pack_rgb": (196, 160, 70), "label_rgb": (252, 252, 250), "logo": "flower",
     "front": {"cx": 0.5, "cy": 0.45, "w": 0.76, "h": 0.28}, "back": {"cx": 0.5, "cy": 0.60, "w": 0.84, "h": 0.46},
     "inci": ["CAPRYLIC/CAPRIC TRIGLYCERIDE", "PRUNUS AMYGDALUS DULCIS OIL", "SIMMONDSIA CHINENSIS SEED OIL",
              "TOCOPHEROL", "PARFUM", "LINALOOL"],
     "legal": "Prodotto in Italia. Solo per uso esterno. Tenere fuori dalla portata dei bambini."},
    # nebbia_tonico_rosa was held out in run 1 and then used to fix the bubble threshold -> now dev
    {"id": "nebbia_tonico_rosa", "split": "dev", "brand": "NEBBIA", "name": "Tonico alla Rosa", "size": "200 ml",
     "pack": (580, 880), "pack_rgb": (60, 110, 90), "label_rgb": (250, 240, 242), "logo": "drop",
     "front": {"cx": 0.5, "cy": 0.30, "w": 0.66, "h": 0.22}, "back": {"cx": 0.5, "cy": 0.66, "w": 0.80, "h": 0.40},
     "inci": ["ROSA DAMASCENA FLOWER WATER", "GLYCERIN", "BETAINE", "ALLANTOIN", "SODIUM BENZOATE",
              "POTASSIUM SORBATE"],
     "legal": "Prodotto in Italia. Usare entro 6 mesi dall'apertura."},
    # ambra_balsamo_labbra was held out in run 2, then exposed 2 generic bugs (dark pack exposure, blurred region) -> dev
    {"id": "ambra_balsamo_labbra", "split": "dev", "brand": "AMBRA", "name": "Balsamo Labbra al Miele", "size": "15 ml",
     "pack": (480, 720), "pack_rgb": (25, 25, 28), "label_rgb": (236, 214, 160), "logo": "drop",
     "front": {"cx": 0.5, "cy": 0.68, "w": 0.78, "h": 0.24}, "back": {"cx": 0.5, "cy": 0.40, "w": 0.84, "h": 0.50},
     "inci": ["RICINUS COMMUNIS SEED OIL", "CERA ALBA", "MEL", "BUTYROSPERMUM PARKII BUTTER", "TOCOPHEROL", "AROMA"],
     "legal": "Prodotto in Italia. Solo per uso esterno. PAO 12M."},
    # held-out #3 (added after run 2): never used for any threshold or logic decision
    {"id": "salvia_shampoo_solido", "split": "heldout", "brand": "SALVIA & CO", "name": "Shampoo Solido Purificante",
     "size": "80 g", "pack": (700, 560), "pack_rgb": (150, 175, 140), "label_rgb": (255, 255, 255), "logo": "flower",
     "front": {"cx": 0.42, "cy": 0.50, "w": 0.62, "h": 0.36}, "back": {"cx": 0.56, "cy": 0.52, "w": 0.70, "h": 0.66},
     "inci": ["SODIUM COCOYL ISETHIONATE", "STEARIC ACID", "COCOS NUCIFERA OIL", "SALVIA OFFICINALIS LEAF OIL",
              "PANTHENOL", "CITRIC ACID", "LIMONENE"],
     "legal": "Prodotto in Italia. Evitare il contatto con gli occhi. Tenere fuori dalla portata dei bambini."},
]
SWAP_INCI = {"PARFUM": "LIMONENE", "GLYCERIN": "BUTYLENE GLYCOL", "TOCOPHEROL": "TOCOPHERYL ACETATE",
             "SODIUM BENZOATE": "BENZYL ALCOHOL", "NIACINAMIDE": "ASCORBIC ACID", "STEARIC ACID": "CETYL ALCOHOL",
             "LIMONENE": "LINALOOL", "CITRIC ACID": "LACTIC ACID"}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_B if bold else FONT, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def front_label(p: dict, w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), p["label_rgb"])
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, w - 5, h - 5], outline=p["pack_rgb"], width=3)
    r = int(h * 0.22)
    cx, cy = int(w * 0.16), h // 2
    if p["logo"] == "circle":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=p["pack_rgb"], width=6)
        d.ellipse([cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3], fill=p["pack_rgb"])
    elif p["logo"] == "diamond":
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], outline=p["pack_rgb"], width=6)
    elif p["logo"] == "flower":
        for k in range(6):
            a = k * math.pi / 3
            d.ellipse([cx + r * 0.55 * math.cos(a) - r * 0.4, cy + r * 0.55 * math.sin(a) - r * 0.4,
                       cx + r * 0.55 * math.cos(a) + r * 0.4, cy + r * 0.55 * math.sin(a) + r * 0.4], outline=p["pack_rgb"], width=4)
    else:
        d.polygon([(cx, cy - r), (cx + r * 0.7, cy + r * 0.3), (cx, cy + r), (cx - r * 0.7, cy + r * 0.3)], fill=p["pack_rgb"])
    x0 = int(w * 0.32)
    d.text((x0, int(h * 0.18)), p["brand"], font=font(int(h * 0.12), True), fill=p["pack_rgb"])
    d.text((x0, int(h * 0.43)), p["name"], font=font(int(h * 0.10)), fill=(30, 30, 30))
    d.text((x0, int(h * 0.68)), p["size"], font=font(int(h * 0.09)), fill=(60, 60, 60))
    return img


def back_label(p: dict, w: int, h: int, inci: list[str] | None = None) -> Image.Image:
    img = Image.new("RGB", (w, h), p["label_rgb"])
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, w - 5, h - 5], outline=(120, 120, 120), width=2)
    f = font(max(14, int(h * 0.058)))
    y = int(h * 0.07)
    d.text((int(w * 0.06), y), "INGREDIENTS:", font=font(max(14, int(h * 0.062)), True), fill=(20, 20, 20))
    y += int(h * 0.09)
    for line in wrap(d, ", ".join(inci or p["inci"]) + ".", f, int(w * 0.88)):
        d.text((int(w * 0.06), y), line, font=f, fill=(20, 20, 20))
        y += int(h * 0.075)
    y += int(h * 0.04)
    for line in wrap(d, p["legal"], f, int(w * 0.88)):
        d.text((int(w * 0.06), y), line, font=f, fill=(40, 40, 40))
        y += int(h * 0.075)
    return img


def crease(img: Image.Image, rng: random.Random) -> Image.Image:
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    x0, x1 = rng.uniform(0.15, 0.35) * w, rng.uniform(0.65, 0.85) * w
    y0, y1 = rng.uniform(0.2, 0.8) * h, rng.uniform(0.2, 0.8) * h
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.abs((y1 - y0) * xx - (x1 - x0) * yy + x1 * y0 - y1 * x0) / math.hypot(x1 - x0, y1 - y0)
    within = (xx >= min(x0, x1) - 5) & (xx <= max(x0, x1) + 5)
    shade = np.where(within, np.exp(-(dist / 2.0) ** 2) * -90 + np.exp(-((dist - 4) / 2.5) ** 2) * 50, 0)
    return Image.fromarray(np.clip(a + shade[..., None], 0, 255).astype(np.uint8))


def bubbles(img: Image.Image, rng: random.Random) -> Image.Image:
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(rng.randint(2, 4)):
        cx, cy, r = rng.uniform(0.2, 0.8) * w, rng.uniform(0.25, 0.75) * h, rng.uniform(0.05, 0.09) * h + 6
        d = np.hypot(xx - cx, yy - cy) / r
        ring = np.where(d < 1, -60 * np.exp(-((d - 0.9) / 0.12) ** 2) + 70 * np.exp(-((d - 0.35) / 0.2) ** 2), 0)
        a += ring[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def place(face: Image.Image, label: Image.Image, spec: dict, angle: float = 0.0, dx: float = 0.0, dy: float = 0.0) -> None:
    W, H = face.size
    lab = label.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
    cx, cy = (spec["cx"] + dx) * W, (spec["cy"] + dy) * H
    face.paste(lab, (int(cx - lab.width / 2), int(cy - lab.height / 2)), lab)


def render_face(p: dict, which: str, labels: list[tuple[Image.Image, dict, float, float, float]]) -> Image.Image:
    face = Image.new("RGB", p["pack"], p["pack_rgb"])
    d = ImageDraw.Draw(face)
    d.rectangle([0, 0, p["pack"][0] - 1, int(p["pack"][1] * 0.06)], fill=tuple(max(0, c - 25) for c in p["pack_rgb"]))
    for lab, spec, ang, dx, dy in labels:
        place(face, lab, spec, ang, dx, dy)
    return face


def photo(face: Image.Image, rng: random.Random, blur: float = 0.6) -> Image.Image:
    """Put the pack on a table, small random pose/lighting, sensor noise."""
    bw, bh = 1280, 1280
    t = np.full((bh, bw, 3), (rng.randint(170, 190), rng.randint(165, 180), rng.randint(150, 165)), np.float32)
    t += np.random.default_rng(rng.randint(0, 9999)).normal(0, 4, t.shape)
    table = Image.fromarray(np.clip(t, 0, 255).astype(np.uint8))
    scale = rng.uniform(0.95, 1.05)
    f = face.resize((int(face.width * scale), int(face.height * scale)), Image.BICUBIC).convert("RGBA")
    f = f.rotate(rng.uniform(-4, 4), expand=True, resample=Image.BICUBIC)
    table.paste(f, (int((bw - f.width) / 2 + rng.uniform(-60, 60)), int((bh - f.height) / 2 + rng.uniform(-40, 40))), f)
    a = np.asarray(table).astype(np.float32) * rng.uniform(0.92, 1.06)
    a += np.random.default_rng(rng.randint(0, 9999)).normal(0, 2.5, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))


def sheet(labels: list[Image.Image], rng: random.Random) -> Image.Image:
    W, H = 1200, 1600
    s = Image.new("RGB", (W, H), (236, 238, 240))
    y = 80
    for lab in labels:
        lab = lab.resize((int(lab.width * 1.0), int(lab.height * 1.0)))
        s.paste(lab, ((W - lab.width) // 2, y))
        ImageDraw.Draw(s).rectangle([(W - lab.width) // 2 - 6, y - 6, (W + lab.width) // 2 + 6, y + lab.height + 6],
                                    outline=(170, 170, 175), width=2)
        y += lab.height + 90
    return photo_sheet(s, rng)


def photo_sheet(s: Image.Image, rng: random.Random) -> Image.Image:
    a = np.asarray(s).astype(np.float32) * rng.uniform(0.95, 1.03)
    a += np.random.default_rng(rng.randint(0, 999)).normal(0, 2, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.5))


def build(p: dict, others: list[dict], rng: random.Random, root: Path | None = None) -> None:
    d = (root or OUT) / p["id"]
    (d / "reference").mkdir(parents=True)
    (d / "test").mkdir()
    W, H = p["pack"]
    fw, fh = int(p["front"]["w"] * W), int(p["front"]["h"] * H)
    bw, bh = int(p["back"]["w"] * W), int(p["back"]["h"] * H)
    FL, BL = front_label(p, fw, fh), back_label(p, bw, bh)

    def faces(front=None, back=None):
        return {"front": render_face(p, "front", front if front is not None else [(FL, p["front"], 0, 0, 0)]),
                "back": render_face(p, "back", back if back is not None else [(BL, p["back"], 0, 0, 0)])}

    for face, img in faces().items():
        photo(img, rng).save(d / "reference" / f"{face}.jpg", quality=92)
    sheet([FL, BL], rng).save(d / "reference" / "sheet.jpg", quality=92)
    o = others[0]
    oFL = front_label(o, fw, fh)
    oBL = back_label(o, bw, bh)
    sheet([oFL, oBL], rng).save(d / "reference" / "sheet_wrong_product.jpg", quality=92)
    sheet([FL], rng).save(d / "reference" / "sheet_missing_back.jpg", quality=92)

    inci_bad = list(p["inci"])
    k = next(i for i, x in enumerate(inci_bad) if x in SWAP_INCI)
    inci_bad[k] = SWAP_INCI[inci_bad[k]]
    cases = []

    def add(name, expected, fs, blur=0.6, defect=None):
        for face, img in fs.items():
            photo(img, rng, blur).save(d / "test" / f"{name}_{face}.jpg", quality=90)
        cases.append({"case": name, "expected": expected, "defect": defect})

    for i in range(3):
        j = lambda: rng.uniform(-0.008, 0.008)  # noqa: E731
        add(f"correct_{i + 1}", "COMPLIANT", faces([(FL, p["front"], rng.uniform(-0.8, 0.8), j(), j())],
                                                  [(BL, p["back"], rng.uniform(-0.8, 0.8), j(), j())]))
    add("crooked", "NON_COMPLIANT", faces([(FL, p["front"], rng.choice([-1, 1]) * rng.uniform(8, 12), 0, 0)]), defect="crooked")
    add("shifted", "NON_COMPLIANT", faces(None, [(BL, p["back"], 0, 0.0, rng.choice([-1, 1]) * 0.12)]), defect="misaligned")
    add("creased", "NON_COMPLIANT", faces(None, [(crease(BL, rng), p["back"], 0, 0, 0)]), defect="crease")
    add("bubbled", "NON_COMPLIANT", faces([(bubbles(FL, rng), p["front"], 0, 0, 0)]), defect="bubble")
    add("wrong_inci", "NON_COMPLIANT", faces(None, [(back_label(p, bw, bh, inci_bad), p["back"], 0, 0, 0)]), defect="wrong_text")
    add("wrong_label", "NON_COMPLIANT", faces([(oFL, p["front"], 0, 0, 0)]), defect="wrong_label")
    add("missing_back", "NON_COMPLIANT", faces(None, []), defect="missing")
    add("swapped", "NON_COMPLIANT", faces([(BL.resize((fw, fh)), p["front"], 0, 0, 0)], [(FL.resize((bw, bh)), p["back"], 0, 0, 0)]),
        defect="wrong_face")
    add("blurred", "REVIEW_REQUIRED", faces(), blur=7.0, defect="image_quality")

    truth = {"product_id": p["id"], "split": p["split"], "synthetic": True, "brand": p["brand"],
             "product_name": f"{p['brand']} {p['name']}".strip(), "name": p["name"], "size": p["size"],
             "labels": [{"label_id": "front", "face": "front", "position": p["front"],
                         "content": {"brand": p["brand"], "product_name": p["name"], "size": p["size"], "logo": True}},
                        {"label_id": "back", "face": "back", "position": p["back"],
                         "content": {"inci": p["inci"], "legal_text": p["legal"]}}],
             "wrong_inci_variant": inci_bad}
    (d / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1))
    (d / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1))


# Committed before any unseen rehearsal. Do not retune thresholds on the product this seed renders.
UNSEEN_SEED = 20261105
UNSEEN_DIR = ROOT / "data" / "datasets" / "unseen"


def unseen_product() -> dict:
    """A fictional front/back pack that is not one of the development products.

    The renderer does not draw a cap or a bottom. That face is NOT in this pack.
    """
    return {"id": "mirto_balsamo_barba", "split": "unseen", "brand": "MIRTO NERO", "name": "Balsamo Barba",
            "size": "40 ml", "pack": (560, 780), "pack_rgb": (72, 48, 36), "label_rgb": (245, 236, 220), "logo": "drop",
            "front": {"cx": 0.48, "cy": 0.34, "w": 0.64, "h": 0.24},
            "back": {"cx": 0.52, "cy": 0.62, "w": 0.76, "h": 0.42},
            "inci": ["RICINUS COMMUNIS SEED OIL", "CERA ALBA", "BUTYROSPERMUM PARKII BUTTER", "TOCOPHEROL",
                     "ROSMARINUS OFFICINALIS LEAF OIL", "LIMONENE"],
            "legal": "Prodotto in Italia. Solo per uso esterno. Tenere fuori dalla portata dei bambini."}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--heldout", action="store_true")
    parser.add_argument("--seed", type=int, default=UNSEEN_SEED)
    args = parser.parse_args()
    if args.heldout:
        if args.seed != UNSEEN_SEED:
            raise SystemExit(f"refusing seed {args.seed}: the committed unseen seed is {UNSEEN_SEED}")
        if UNSEEN_DIR.exists():
            shutil.rmtree(UNSEEN_DIR)
        rng = random.Random(UNSEEN_SEED)
        product = unseen_product()
        build(product, [PRODUCTS[0]], rng, UNSEEN_DIR)
        print("written", UNSEEN_DIR, "seed", UNSEEN_SEED)
        print("NOT a rehearsal result. Run eval/labelling/run_unseen.py to measure it.")
        return
    if OUT.exists():
        shutil.rmtree(OUT)
    rng = random.Random(20261104)
    for i, p in enumerate(PRODUCTS):
        build(p, [PRODUCTS[(i + 1) % len(PRODUCTS)]], rng)
    (OUT / "README.md").write_text(
        "# Synthetic labelling dataset\n\nRendered by `scripts/make_synthetic_packs.py` (seed 20261104). Fictional brands, "
        "dummy text. INCI names are real nomenclature strings used as dummy content, not real formulations.\n"
        "`nebbia_tonico_rosa` is HELD OUT (never used while developing thresholds) to simulate the jury's unseen pack.\n"
        "Results on this set are not evidence of real-camera performance.\n")
    print("written", OUT, sum(1 for _ in OUT.rglob("*.jpg")), "images")


if __name__ == "__main__":
    main()
