"""Find which flange axis the gripper beam points along, from the robot's CURRENT pose, and write it to robot.yaml.

Never moves the robot. Before running: with the teach pendant, put the arm so the flange axis is level (horizontal)
and the gripper beam hangs straight DOWN (ring at the bottom), as in the photos. Then:

    .venv/bin/python scripts/beam_axis.py            (real robot, prints the result)
    .venv/bin/python scripts/beam_axis.py --write    (also writes gripper.offset_axis_flange)
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import CONFIG_DIR, load_settings  # noqa: E402
from app.robot.kinematics import Kinematics, model_of  # noqa: E402

AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0)}


def beam_axis(q, model: str = "UR3e", tol_deg: float = 25.0) -> tuple[list[int] | None, dict[str, float], float]:
    """(axis in the flange frame or None, angle of each candidate from straight down, flange-axis tilt)."""
    R = Kinematics(model).frames(q)[-1][:3, :3]
    tilt = math.degrees(math.asin(min(1.0, abs(float(R[2, 2])))))   # 0 = flange axis level
    angles = {}
    for name, e in AXES.items():
        d = -float((R @ np.array(e, float))[2])
        angles[name] = math.degrees(math.acos(max(-1.0, min(1.0, d))))
    best = min(angles, key=angles.get)
    return (list(AXES[best]) if angles[best] <= tol_deg else None), angles, tilt


def read_q(host: str, port: int) -> list[float]:
    from app.robot.ur import RTDEReader
    r = RTDEReader(host, port)
    r.connect(3.0)
    end = time.monotonic() + 3.0
    while not r.fresh() and time.monotonic() < end:
        time.sleep(0.02)
    if not r.fresh():
        raise SystemExit("Nessun dato RTDE dal robot: è acceso e collegato?")
    q = list(r.latest["actual_q"])
    r.close()
    return q


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["real", "ursim"], default="real")
    ap.add_argument("--q", type=float, nargs=6, help="use these joints (rad) instead of reading the robot")
    ap.add_argument("--write", action="store_true", help="write gripper.offset_axis_flange into config/robot.yaml")
    a = ap.parse_args()
    s = load_settings(a.mode, env_file=ROOT / ".env")
    q = a.q or read_q(s.robot_host, int(s.robot["connections"]["ports"]["rtde"]))
    model = model_of(str((s.robot.get("robot") or {}).get("model", "UR3e")))
    axis, angles, tilt = beam_axis(q, model)
    print("关节角 (rad):", " ".join(f"{v:.4f}" for v in q))
    print(f"法兰轴离水平: {tilt:.1f}°")
    print("各方向离正下方:", ", ".join(f"{k} {v:.0f}°" for k, v in angles.items()))
    if tilt > 25 or axis is None:
        raise SystemExit("❌ 姿态不对：把法兰轴摆成水平、夹爪横梁垂直朝下（误差 25° 以内），再运行一次。")
    line = f"offset_axis_flange: [{axis[0]}, {axis[1]}, {axis[2]}]"
    print("✅ 横梁方向:", line)
    if a.write:
        p = CONFIG_DIR / "robot.yaml"
        text = p.read_text(encoding="utf-8")
        new, n = re.subn(r"(?m)^(\s*)offset_axis_flange:.*$", lambda m: m.group(1) + line, text, count=1)
        if n != 1:
            raise SystemExit("robot.yaml 里没找到 offset_axis_flange 这一行，请手动填上面那一行。")
        p.write_text(new, encoding="utf-8")
        print("✅ 已写入 config/robot.yaml。重启服务（start_real.bat）后生效。")
    else:
        print("确认没问题的话，加 --write 再运行一次，就会自动写进 config/robot.yaml。")


if __name__ == "__main__":
    main()
