"""Record the robot's CURRENT joint positions into config/poses.yaml.

Jog the robot to the pose with the teach pendant first (this script never moves it), then:

    python scripts/record_pose.py centro_presa --mode ursim
    python scripts/record_pose.py centro_presa --mode real      # marks verificata_reale: true
    python scripts/record_pose.py --list
    python scripts/record_pose.py centro_presa --mode ursim --dry-run

Only existing pose names can be overwritten unless --new is given (prevents typos).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import CONFIG_DIR, load_settings  # noqa: E402
from app.robot.ur import RTDEReader  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--mode", choices=["ursim", "real"], default="ursim")
    ap.add_argument("--new", action="store_true", help="allow creating a pose name not yet in poses.yaml")
    ap.add_argument("--selectable", action="store_true", help="make it selectable by voice (vai_a_posa)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    path = CONFIG_DIR / "poses.yaml"
    text = path.read_text()
    header = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
    data = yaml.safe_load(text)
    poses = data["poses"]
    if a.list:
        for k, v in poses.items():
            print(f"{k:28} {v.get('fonte', '?'):22} real={'yes' if v.get('verificata_reale') else 'no '} {v['joints']}")
        return
    if not a.name:
        ap.error("pose name required")
    if a.name not in poses and not a.new:
        ap.error(f"unknown pose {a.name!r} (use --new to create it)")

    s = load_settings(a.mode)
    r = RTDEReader(s.robot_host, int(s.robot["connections"]["ports"]["rtde"]))
    r.connect()
    time.sleep(0.3)
    d = r.latest
    r.close()
    if any(abs(v) > 0.005 for v in d["actual_qd"]):
        raise SystemExit("robot is moving: wait until it is still")
    q = [round(float(x), 5) for x in d["actual_q"]]
    old = poses.get(a.name, {})
    entry = {**old, "joints": q, "fonte": f"registrata_{a.mode}_{time.strftime('%Y-%m-%d_%H:%M')}",
             "verificata_reale": a.mode == "real"}
    entry.setdefault("selezionabile", bool(a.selectable))
    print(f"{a.name}: {old.get('joints')} -> {q}  ({entry['fonte']})")
    if a.dry_run:
        print("dry run: nothing written")
        return
    poses[a.name] = entry
    path.write_text(header + yaml.safe_dump({"poses": poses}, sort_keys=False, allow_unicode=True,
                                            default_flow_style=None, width=120))
    print(f"saved to {path}")


if __name__ == "__main__":
    main()
