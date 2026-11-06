"""Configuration: config/*.yaml + environment (.env). Validated at startup."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
LOG_DIR = Path(os.getenv("VOCE_LOG_DIR") or ROOT / "logs")
FRONTEND_DIST = ROOT / "frontend" / "dist"

MODES = ("sim", "ursim", "real")
LANGUAGES = {"it": "italiano", "en": "English", "zh": "中文", "ja": "日本語", "am": "አማርኛ",
             "es": "español", "fr": "français", "de": "Deutsch"}
# Codes the voice API accepts. Amharic is not in the official hint list, so it is omitted.
VOICE_HINTS = {"it": "it", "en": "en", "zh": "zh", "es": "es-ES", "fr": "fr", "de": "de", "ja": "ja"}
CAMERA_SOURCES = ("browser", "url", "sim")
# Second, deliberate opt-in for real hardware (from grokarm-ur3e): ROBOT_MODE=real also needs this env value.
REAL_MOTION_PHRASE = "STAFF_APPROVED"


class ConfigError(Exception):
    pass


@dataclass
class Settings:
    mode: str
    robot: dict[str, Any]
    skills: dict[str, Any]
    poses: dict[str, Any]
    xai_api_key: str | None
    xai_model: str = "grok-voice-latest"
    xai_voice: str = "eve"
    lang_hint: str | None = "it"
    wake_word: str | None = None
    lang: str = "it"
    camera_source: str = "browser"
    camera_url: str | None = None
    inspector: str = "mock"
    mock_verdict: str = "FAIL"
    xai_vision_model: str = "grok-4.7"
    xai_base_url: str = "https://api.x.ai/v1"
    host: str = "127.0.0.1"
    port: int = 8000
    config_dir: Path = CONFIG_DIR
    data_dir: Path = DATA_DIR
    warnings: list[str] = field(default_factory=list)
    camera: dict[str, Any] = field(default_factory=dict)
    user_instructions: dict[str, Any] = field(default_factory=dict)   # learnt commands, data/user_instructions.yaml

    # ------------------------------------------------------------ helpers ---
    @property
    def zones(self) -> list[str]:
        return list(self.robot["workspace"]["zones"])

    @property
    def directions(self) -> list[str]:
        return list(self.robot["workspace"]["push_directions"])

    @property
    def gripper_on(self) -> bool:
        """True only when a driven gripper is mounted. The passive semicircle cup tool stays gripper.enabled."""
        return bool((self.robot.get("tool") or {}).get("gripper"))

    @property
    def inspection(self) -> dict[str, Any]:
        return dict(self.robot.get("inspection") or {})

    def zone_label(self, zone: str) -> str:
        return str((self.inspection.get("labels") or {}).get(zone, zone))

    @property
    def ttl_s(self) -> float:
        raw = self.robot["safety"].get("confirmation_ttl_s", 20)
        return 0.0 if raw in (None, "", 0) else float(raw)

    def voice_language_hint(self) -> str | None:
        lang = self.lang or self.lang_hint
        if not lang or lang == "am":
            return None
        return VOICE_HINTS.get(lang, lang)

    @property
    def motion(self) -> dict[str, float]:
        return dict(self.robot["motion"][self.mode])

    @property
    def robot_host(self) -> str | None:
        c = self.robot["connections"]
        return c["real"]["host"] if self.mode == "real" else (os.getenv("URSIM_HOST") or c["ursim"]["host"])

    def public(self) -> dict[str, Any]:
        """Safe to send to the UI (no secrets, no hosts)."""
        r = self.robot["robot"]
        return {"mode": self.mode, "robot_name": r.get("name"), "robot_model": r.get("model"),
                "polyscope": r.get("polyscope_version"), "zones": self.zones, "directions": self.directions,
                "ttl_s": self.ttl_s, "grok_model": self.xai_model, "grok_configured": bool(self.xai_api_key),
                "warnings": self.warnings, "camera_available": bool(self.camera.get("available")),
                "camera_source": self.camera.get("source"),
                "cell_camera_source": self.camera_source,   # browser webcam / phone (url) feed for the Camera tab
                "phone_urls": _phone_urls(),
                "camera_calibrated": _zones_calibrated(self.camera),
                "delivery_zone": self.robot.get("workspace", {}).get("delivery_zone", "destra"),
                "station": "ARM 1 · CAMERA 1" if self.camera.get("available") else "ARM 3 · senza telecamera",
                "lang": self.lang, "languages": LANGUAGES, "inspector": self.inspector,
                "inspection": {k: self.inspection.get(k) for k in ("input_zone", "pass_zone", "fail_zone", "labels")},
                "guard_enabled": bool((self.robot.get("guard") or {}).get("enabled", False)),
                "gripper": bool((self.robot.get("gripper") or {}).get("enabled", False))}


def _load_yaml(name: str, config_dir: Path) -> dict[str, Any]:
    p = config_dir / name
    if not p.exists():
        raise ConfigError(f"missing {p}")
    return yaml.safe_load(p.read_text()) or {}


def load_settings(mode: str | None = None, *, config_dir: Path = CONFIG_DIR, data_dir: Path = DATA_DIR,
                  env_file: Path | None = ROOT / ".env") -> Settings:
    if env_file and env_file.exists():
        load_dotenv(env_file)
    robot = _load_yaml("robot.yaml", config_dir)
    skills = _load_yaml("skills.yaml", config_dir)["skills"]
    poses = _load_yaml("poses.yaml", config_dir)["poses"]
    camera = (_load_yaml("camera.yaml", config_dir).get("camera") or {}) if (config_dir / "camera.yaml").exists() else {}

    # user poses recorded by voice / record_pose.py (P2) live in data/, never in config/
    up = data_dir / "user_poses.yaml"
    if up.exists():
        for k, v in (yaml.safe_load(up.read_text()) or {}).get("poses", {}).items():
            if k in poses and poses[k].get("fonte") != "voce":
                continue  # never let a voice-recorded pose shadow a configured one
            poses[k] = v

    mode = (mode or os.getenv("ROBOT_MODE") or robot.get("default_mode") or "ursim").strip().lower()
    s = Settings(
        user_instructions=_user_instructions(data_dir),
        mode=mode, robot=robot, skills=skills, poses=poses,
        xai_api_key=os.getenv("XAI_API_KEY") or None,
        xai_model=os.getenv("XAI_MODEL", "grok-voice-latest"),
        xai_voice=os.getenv("XAI_VOICE", "eve"),
        lang_hint=(os.getenv("LANG_HINT", "it") or None),
        wake_word=(os.getenv("WAKE_WORD") or None),
        lang=(os.getenv("UI_LANG") or os.getenv("LANG_HINT") or "it").strip().lower()[:2],
        camera_source=(os.getenv("CAMERA_SOURCE") or camera.get("source") or "browser").strip().lower(),
        camera_url=(os.getenv("CAMERA_URL") or None),
        inspector=(os.getenv("INSPECTOR") or ("grok" if os.getenv("XAI_API_KEY") else "mock")).strip().lower(),
        mock_verdict=(os.getenv("MOCK_VERDICT") or "FAIL").strip().upper(),
        xai_vision_model=os.getenv("XAI_VISION_MODEL", "grok-4.7"),
        xai_base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        config_dir=config_dir, data_dir=data_dir, camera=camera,
    )
    validate(s)
    return s


def _zones_calibrated(camera: dict[str, Any]) -> bool:
    zones = camera.get("zones_px") or {}
    return all(isinstance(zones.get(z), list) and len(zones[z]) >= 3 for z in ("sinistra", "centro", "destra"))


_POSE_RE = re.compile(r"\{(\w+)\}")


def validate(s: Settings) -> None:
    if s.mode not in MODES:
        raise ConfigError(f"ROBOT_MODE must be one of {MODES}, got {s.mode!r}")
    ws = s.robot.get("workspace", {})
    if not ws.get("zones"):
        raise ConfigError("workspace.zones is empty")
    for z in ws.get("trays_assumed_at", []):
        if z not in ws["zones"]:
            raise ConfigError(f"trays_assumed_at contains unknown zone {z!r}")
    if s.lang not in LANGUAGES:
        s.lang = "it"
    if s.camera_source not in CAMERA_SOURCES:
        raise ConfigError(f"CAMERA_SOURCE must be one of {CAMERA_SOURCES}, got {s.camera_source!r}")
    if s.camera_source == "url" and not s.camera_url:
        raise ConfigError("CAMERA_SOURCE=url requires CAMERA_URL")
    if s.inspector not in ("grok", "mock"):
        raise ConfigError(f"INSPECTOR must be grok or mock, got {s.inspector!r}")
    insp = s.robot.get("inspection")
    if insp:
        for k in ("input_zone", "pass_zone", "fail_zone"):
            if insp.get(k) not in ws["zones"]:
                raise ConfigError(f"inspection.{k} must be one of the workspace zones, got {insp.get(k)!r}")
        if insp.get("inspect_pose") and insp.get("inspect_pose") not in s.poses:
            raise ConfigError(f"inspection.inspect_pose {insp.get('inspect_pose')!r} missing in poses.yaml")
    if s.mode not in s.robot.get("motion", {}):
        raise ConfigError(f"motion.{s.mode} missing in robot.yaml")

    # every pose reachable by a skill sequence must exist with 6 joints
    for name, p in s.poses.items():
        j = p.get("joints")
        if not (isinstance(j, list) and len(j) == 6 and all(isinstance(x, (int, float)) for x in j)):
            raise ConfigError(f"pose {name!r} must have 6 numeric joints")
    domains = {"zona": s.zones, "direzione": s.directions,
               "posa": [k for k, v in s.poses.items() if v.get("selezionabile")]}
    for sid, sk in s.skills.items():
        params = sk.get("parametri", {})
        for pname, spec in params.items():
            if spec["tipo"] not in domains:
                raise ConfigError(f"skill {sid}: unknown parameter type {spec['tipo']}")
        for step in sk.get("sequenza", []):
            if step["move"] == "wait":
                continue
            tmpl = step["posa"]
            names = _POSE_RE.findall(tmpl)
            combos: list[dict[str, str]] = [{}]
            for n in names:
                combos = [{**c, n: v} for c in combos for v in domains[params[n]["tipo"]]]
            for c in combos:
                pose = tmpl.format(**c)
                if pose not in s.poses:
                    raise ConfigError(f"skill {sid}: sequence references missing pose {pose!r}")

    if s.mode == "real":
        if os.getenv("ALLOW_REAL_MOTION", "") != REAL_MOTION_PHRASE:
            raise ConfigError(f"ROBOT_MODE=real requires ALLOW_REAL_MOTION={REAL_MOTION_PHRASE}, set only with lab staff "
                              "present and a hand near the E-stop")
        real = s.robot["connections"]["real"]
        if not real.get("enabled") or not real.get("host"):
            raise ConfigError("ROBOT_MODE=real requires connections.real.enabled: true and connections.real.host "
                              "in config/robot.yaml (explicit opt-in).")
        unverified = [k for k, v in s.poses.items() if not v.get("verificata_reale")]
        if unverified:
            s.warnings.append(f"{len(unverified)} poses not verified on the real robot; skills using them will be refused.")
    if not s.xai_api_key:
        s.warnings.append("XAI_API_KEY not set: voice disabled, TEXT MODE only.")
    for k in ("model", "polyscope_version"):
        if str(s.robot["robot"].get(k, "UNKNOWN")).upper() == "UNKNOWN":
            s.warnings.append(f"robot {k} UNKNOWN (verify on hardware)")


def _phone_urls() -> list[str]:
    """Phone camera page URLs, when the launcher started the grokarm_vision service (logs/vision_server.json)."""
    try:
        return list(json.loads((LOG_DIR / "vision_server.json").read_text()).get("phone_urls") or [])
    except (OSError, ValueError):
        return []


def _user_instructions(data_dir: Path) -> dict[str, Any]:
    """Learnt commands saved by voice (save_instruction). Missing or unreadable file = none."""
    ui = Path(data_dir) / "user_instructions.yaml"
    try:
        return (yaml.safe_load(ui.read_text(encoding="utf-8")) or {}).get("instructions", {}) if ui.exists() else {}
    except (OSError, yaml.YAMLError):
        return {}
