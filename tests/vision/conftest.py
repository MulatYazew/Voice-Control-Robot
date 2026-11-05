from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grokarm_vision.config import Settings  # noqa: E402
from grokarm_vision.resolve import OfflineResolver  # noqa: E402
from grokarm_vision.service import VisionService  # noqa: E402

import scene  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "vision")


@pytest.fixture
def ready_service(settings: Settings) -> VisionService:
    service = VisionService(settings, resolver=OfflineResolver())
    bg = scene.table()
    service.store.put_image(bg)
    service.save_calibration(scene.calibration_points())
    service.capture_background()
    return service
