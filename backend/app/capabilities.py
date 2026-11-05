"""What this station can actually do, read from config and live status.

Nothing here is a hard-coded claim about hardware that the config does not state.
"""

from __future__ import annotations

import subprocess
from typing import Any

from .config import Settings
from .safety.phrases import lexicon_review_status


def _tesseract_langs() -> list[str]:
    try:
        proc = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [w for w in proc.stdout.split() if w and not w.endswith(":")]


def build_capabilities(settings: Settings, *, robot_connected: bool = False, frame_age_s: float | None = None,
                       catalog_size: int = 0) -> dict[str, Any]:
    robot = settings.robot.get("robot") or {}
    station = str(robot.get("station") or "arm3")
    cam = dict(settings.camera or {})
    available = bool(cam.get("available")) and station != "arm3"
    max_age = float(cam.get("max_age_s") or 1.5)
    fresh = frame_age_s is not None and frame_age_s <= max_age
    zones = cam.get("zones_px") or {}
    calibrated = all(isinstance(zones.get(z), list) and len(zones[z]) >= 3 for z in ("sinistra", "centro", "destra"))
    tool = (settings.robot.get("tool") or {}).get("tcp_offsets") or {}
    review = lexicon_review_status()
    model = robot.get("model")
    return {
        "station": station,
        "robot": {"vendor": robot.get("vendor") or "UNKNOWN",
                  "model": "UNKNOWN" if not model or str(model).upper() == "UNKNOWN" else model,
                  "mode": settings.mode, "connected": bool(robot_connected)},
        "tool": {"features": [k for k in ("forca", "spintore", "punta") if k in tool], "gripper": False},
        "skills": sorted(settings.skills),
        "zones": list(settings.zones),
        "directions": list(settings.directions),
        "delivery_zone": (settings.robot.get("workspace") or {}).get("delivery_zone", "destra"),
        "accepted_zone": "destra",
        "reject_destination": "scarto",
        "poses": {name: bool((pose or {}).get("verificata_reale")) for name, pose in settings.poses.items()},
        "camera": {"available": available, "connected": bool(available and fresh),
                   "frame_age_s": frame_age_s if available else None,
                   "zones_calibrated": bool(available and calibrated),
                   "source": cam.get("source") if available else None,
                   "simulated": cam.get("source") == "sim",
                   "label": "SIMULATED CAMERA · SYNTHETIC PRODUCTS" if cam.get("source") == "sim" else None},
        "catalogue_size": int(catalog_size),
        "tesseract_languages": _tesseract_langs(),
        "amharic": "text-fallback",
        "voice_languages": {lang: ("reviewed" if review["reviewed_by"].get(lang) else "NOT VERIFIED")
                            for lang in ("it", "en", "zh", "ja", "am")},
    }
