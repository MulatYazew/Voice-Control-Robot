"""Configuration: config/*.yaml + environment (.env). Validated at startup."""

from __future__ import annotations

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
LOG_DIR = ROOT / "logs"
FRONTEND_DIST = ROOT / "frontend" / "dist"

MODES = ("sim", "ursim", "real")


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
    host: str = "127.0.0.1"
    port: int = 8000
    config_dir: Path = CONFIG_DIR
    data_dir: Path = DATA_DIR
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ helpers ---
    @property
    def zones(self) -> list[str]:
        return list(self.robot["workspace"]["zones"])

    @property
    def directions(self) -> list[str]:
        return list(self.robot["workspace"]["push_directions"])

    @property
    def ttl_s(self) -> float:
        return float(self.robot["safety"]["confirmation_ttl_s"])

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
                "warnings": self.warnings}


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

    # user poses recorded by voice / record_pose.py (P2) live in data/, never in config/
    up = data_dir / "user_poses.yaml"
    if up.exists():
        for k, v in (yaml.safe_load(up.read_text()) or {}).get("poses", {}).items():
            if k in poses and poses[k].get("fonte") != "voce":
                continue  # never let a voice-recorded pose shadow a configured one
            poses[k] = v

    mode = (mode or os.getenv("ROBOT_MODE") or robot.get("default_mode") or "ursim").strip().lower()
    s = Settings(
        mode=mode, robot=robot, skills=skills, poses=poses,
        xai_api_key=os.getenv("XAI_API_KEY") or None,
        xai_model=os.getenv("XAI_MODEL", "grok-voice-latest"),
        xai_voice=os.getenv("XAI_VOICE", "eve"),
        lang_hint=(os.getenv("LANG_HINT", "it") or None),
        wake_word=(os.getenv("WAKE_WORD") or None),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        config_dir=config_dir, data_dir=data_dir,
    )
    validate(s)
    return s


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
