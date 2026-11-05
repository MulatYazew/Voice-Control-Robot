"""Record one table-corner tip position. Does not move the robot and does not open a second connection.

The real server must already be running (it owns the robot). Jog with the teach pendant, stop, then:

    .\\.venv\\Scripts\\python.exe scripts\\record_corner.py 前左
    .\\.venv\\Scripts\\python.exe scripts\\record_corner.py 前右
    .\\.venv\\Scripts\\python.exe scripts\\record_corner.py 后右
    .\\.venv\\Scripts\\python.exe scripts\\record_corner.py 后左
    .\\.venv\\Scripts\\python.exe scripts\\record_corner.py --show

Order: front-left, front-right, back-right, back-left. "Front" is the side nearest you.
The tip length defaults to the program's assumed 0.15 m. Pass --tip-m only after you have measured it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.robot.kinematics import Kinematics  # noqa: E402

OUT = ROOT / "data" / "table_corners.json"
STATUS = "http://127.0.0.1:8000/api/status"
FRAME = "http://127.0.0.1:8000/api/camera/frame.jpg"
ORDER = ("前左", "前右", "后右", "后左")


def _get(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"打不开 {url} ：{e}. 先确认真实机器人的服务已经在跑，不要另开一个连接。") from e


def _tip(joints: list[float], tip_m: float) -> tuple[list[float], list[float], float]:
    T = Kinematics("UR3e", (0.0, 0.0, tip_m)).fk(joints)
    tip = [round(float(x), 4) for x in T[:3, 3]]
    tool_z = [round(float(x), 3) for x in T[:3, 2]]
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, -tool_z[2]))))
    return tip, tool_z, round(tilt, 1)


def _load() -> dict:
    if not OUT.exists():
        return {"tip_length_m": None, "points": []}
    return json.loads(OUT.read_text(encoding="utf-8"))


def _save(doc: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _show(doc: dict) -> None:
    pts = doc.get("points") or []
    print(f"tip length used: {doc.get('tip_length_m')} m   file: {OUT}")
    for p in pts:
        x, y, z = (v * 1000 for v in p["tip_m"])
        print(f"  {p['name']:4}  x={x:7.1f}  y={y:7.1f}  z={z:7.1f} mm   tilt={p['tilt_deg']}°")
    by = {p["name"]: p for p in pts}
    if all(n in by for n in ORDER):
        a, b, c, d = (by[n]["tip_m"] for n in ORDER)
        width = math.dist(a[:2], b[:2]) * 1000
        depth = math.dist(b[:2], c[:2]) * 1000
        zs = [p["tip_m"][2] * 1000 for p in (by[n] for n in ORDER)]
        print(f"front edge {width:.0f} mm, side edge {depth:.0f} mm, height spread {max(zs) - min(zs):.1f} mm")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", choices=ORDER)
    ap.add_argument("--tip-m", type=float, default=0.15)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    doc = _load()
    if a.show or not a.name:
        _show(doc)
        return
    status = json.loads(_get(STATUS))
    robot = status.get("robot") or {}
    joints = robot.get("joints")
    if not robot.get("connected") or not joints or len(joints) != 6:
        raise SystemExit("服务里没有六个关节。确认页面上是真实机器人并且已连接。")
    if robot.get("moving"):
        raise SystemExit("手臂还在动。停稳后再记。")
    tip, tool_z, tilt = _tip([float(v) for v in joints], a.tip_m)
    if doc.get("tip_length_m") not in (None, a.tip_m):
        raise SystemExit(f"这组点已经用针长 {doc['tip_length_m']} m。四个角要用同一针长，或删掉 {OUT} 重来。")
    photo = None
    try:
        jpeg = _get(FRAME)
        photo = OUT.parent / f"corner-{a.name}.jpg"
        photo.write_bytes(jpeg)
    except SystemExit:
        photo = None
    doc["tip_length_m"] = a.tip_m
    doc["points"] = [p for p in doc.get("points", []) if p.get("name") != a.name]
    doc["points"].append({"name": a.name, "t": time.strftime("%H:%M:%S"),
                          "joints": [round(float(v), 5) for v in joints],
                          "tip_m": tip, "tool_z": tool_z, "tilt_deg": tilt,
                          "photo": str(photo) if photo else None})
    _save(doc)
    x, y, z = (v * 1000 for v in tip)
    print(f"{a.name}: x={x:.1f} y={y:.1f} z={z:.1f} mm, tilt={tilt}°")
    if tilt > 20:
        print("针尖没有接近竖直。扶正后再记一次这个角。")
    if photo is None:
        print("没有相机画面。四个角的机器人坐标已记下；要和画面绑在一起，先让手机开着并固定。")
    _show(doc)


if __name__ == "__main__":
    main()
