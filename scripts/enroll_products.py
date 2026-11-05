"""Enrol product photos already on disk. One photo per SKU, no training.

    python scripts/enroll_products.py <folder>

Each immediate subdirectory is a product. Its name is the product id. Images are *.jpg / *.png.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.perception.catalog import Catalog


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/enroll_products.py <folder>")
        return 2
    src = Path(sys.argv[1])
    if not src.is_dir():
        print(f"not a folder: {src}")
        return 2
    cat = Catalog(Path("data/catalog"))
    n = 0
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        images = [p for p in d.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
        if not images:
            continue
        img = cv2.imread(str(images[0]))
        if img is None:
            continue
        cat.enroll(d.name, img, keywords=[d.name])
        n += 1
        print(f"enrolled {d.name}")
    print(f"{n} products in {cat.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
