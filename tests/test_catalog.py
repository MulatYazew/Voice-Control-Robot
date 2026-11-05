"""Catalogue enrolment: match a transformed view, reject an unknown product."""

import numpy as np
import cv2

from app.perception.catalog import Catalog


def _texture(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((220, 280, 3), np.uint8)
    img[:] = ((30 + seed * 37) % 220, (40 + seed * 17) % 220, (50 + seed * 29) % 220)
    for _ in range(40):
        center = (int(rng.integers(10, 270)), int(rng.integers(10, 210)))
        color = tuple(int(x) for x in rng.integers(0, 255, size=3))
        cv2.circle(img, center, int(rng.integers(6, 18)), color, -1)
    return img


def _warp(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 12, 0.9)
    return cv2.warpAffine(img, m, (w, h), borderValue=(20, 20, 20))


def test_enroll_three_and_reject_unknown(tmp_path):
    cat = Catalog(tmp_path / "catalog")
    images = {name: _texture(i + 1) for i, name in enumerate(("salvia", "crema", "tonico"))}
    for name, img in images.items():
        cat.enroll(name, img, keywords=[name])
    assert len(cat.products()) == 3
    for name, img in images.items():
        hit = cat.match(_warp(img), text=name)
        assert hit and hit["product_id"] == name
    blank = np.full((220, 280, 3), 20, np.uint8)
    unknown = cat.match(blank, text="sconosciuto")
    assert unknown is None
    assert "salvia" in cat.keyterms()
