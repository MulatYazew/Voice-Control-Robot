"""Build tools/collision_viewer.html: UR3e meshes + gripper + named poses, for the browser 3D check.

    python tools/build_collision_viewer.py          (rerun after changing robot.yaml gripper or poses.yaml)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import load_settings  # noqa: E402
from app.skills.mesh_model import JOINTS, LINKS, MeshModel, export_scene  # noqa: E402

s = load_settings()
g = dict(s.robot.get("gripper") or {})
axis = g.get("offset_axis_flange")
g["offset_axis_flange"] = [1.0, 0.0, 0.0]          # exported along +x; the page rotates it to the chosen axis
m = MeshModel({**g, "enabled": True})
data = {
    "meshes": export_scene(m),
    "joints": [[list(x), list(r)] for x, r in JOINTS],
    "links": {k: [list(v[1]), list(v[2])] for k, v in LINKS.items()},
    "poses": {k: p["joints"] for k, p in (s.poses or {}).items() if isinstance(p, dict) and p.get("joints")},
    "beam_axis": axis,
    "margin_m": float(((s.robot.get("guard") or {}).get("mesh") or {}).get("gripper_margin_m", 0.008)),
    "table_z": float((s.robot.get("relative") or {}).get("table_z_m", 0.0)),
}
tpl = (ROOT / "tools" / "collision_viewer.template.html").read_text(encoding="utf-8")
out = ROOT / "tools" / "collision_viewer.html"
out.write_text(tpl.replace("/*__SCENE__*/null", json.dumps(data, separators=(",", ":"))), encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
