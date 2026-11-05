from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = Path("config/vision")
    operator_port: int = 8710
    phone_port: int = 8711
    phone_https: bool = True
    camera_source: str = "push"
    camera_url: str = ""
    poll_interval_s: float = 0.3
    max_frame_age_s: float = 2.0
    xai_api_key: str = field(default="", repr=False)
    xai_vision_model: str = "grok-4.7"
    xai_base_url: str = "https://api.x.ai/v1"
    xai_timeout_s: float = 8.0
    max_drift_px: float = 6.0
    drift_check: bool = True
    workspace_margin_mm: float = 10.0
    left_axis_sign: float = 1.0
    diff_threshold: int = 35
    min_area_frac: float = 0.0006
    max_area_frac: float = 0.2

    @classmethod
    def from_env(cls, dotenv: Path | None = Path(".env")) -> "Settings":
        if dotenv is not None:
            _load_dotenv(dotenv)
        env = os.environ
        return cls(
            data_dir=Path(env.get("VISION_DATA_DIR", "config/vision")),
            operator_port=int(env.get("VISION_PORT", 8710)),
            phone_port=int(env.get("VISION_PHONE_PORT", 8711)),
            phone_https=env.get("VISION_PHONE_HTTPS", "true").lower() != "false",
            camera_source=env.get("CAMERA_SOURCE", "push").lower(),
            camera_url=env.get("CAMERA_URL", "").rstrip("/"),
            max_frame_age_s=_float("VISION_MAX_FRAME_AGE_S", 2.0),
            xai_api_key=env.get("XAI_API_KEY", "").strip(),
            xai_vision_model=env.get("XAI_VISION_MODEL", "grok-4.7"),
            xai_base_url=env.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
            xai_timeout_s=_float("XAI_VISION_TIMEOUT_S", 8.0),
            max_drift_px=_float("VISION_MAX_DRIFT_PX", 6.0),
            drift_check=env.get("VISION_DRIFT_CHECK", "true").lower() != "false",
            workspace_margin_mm=_float("VISION_WORKSPACE_MARGIN_MM", 10.0),
            left_axis_sign=-1.0 if env.get("VISION_LEFT_AXIS", "+y").strip() == "-y" else 1.0,
        )
