"""Mesh collision model: the REAL UR3e link shapes + the printed gripper, checked before every motion.

Why: the capsule check in motion_guard.py puts the arm capsules on the DH frame points, but on a UR the upper arm
tube is 120 mm and the forearm 27 mm to the side of those points (ur_description visual_parameters.yaml,
"mesh_offset"). A gripper beam can sweep through the real tube while the capsules say "clear".

This module places Universal Robots' own collision meshes (config/meshes/ur3e, BSD-3, from
UniversalRobots/Universal_Robots_ROS2_Description) on the official URDF chain, attaches the gripper as a rigid mesh
on the flange (it never moves relative to the flange), and asks FCL for the minimum distance between the gripper
and every arm link that can reach it, plus the gripper and every link against the table.

  * The gripper is fixed: only the joints move it. Same idea as the photo: two bodies in one model.
  * ``margins(q)`` returns {name: metres}; negative = collision (or closer than the margin).
  * motion_guard.MotionGuard calls it in ``margins`` for every sample of every movej / movel, so any motion that
    would push the gripper through the arm is refused or re-routed BEFORE it is sent.
  * ``python -m app.skills.mesh_model`` (from backend/) checks every pose in poses.yaml and prints a table.

Needs ``python-fcl`` and ``trimesh`` (pip). Without them the guard silently keeps the capsule model.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np

MESH_DIR = Path(__file__).resolve().parents[3] / "config" / "meshes" / "ur3e"

# Official UR3e chain (ur_description config/ur3e/default_kinematics.yaml): parent->child xyz, rpy.
# Frame 0 = base_link_inertia = the controller's "base" frame = the DH base used by kinematics.py.
JOINTS = [
    ((0, 0, 0.15185), (0, 0, 0)),
    ((0, 0, 0), (math.pi / 2, 0, 0)),
    ((-0.24355, 0, 0), (0, 0, 0)),
    ((-0.2132, 0, 0.13105), (0, 0, 0)),
    ((0, -0.08535, 0), (math.pi / 2, 0, 0)),
    ((0, 0.0921, 0), (math.pi / 2, math.pi, math.pi)),
]
# Collision mesh offsets in their link frame (visual_parameters.yaml mesh_offset): file, xyz, rpy.
LINKS = {
    "base":     ("base.stl", (0, 0, 0), (0, 0, math.pi)),
    "shoulder": ("shoulder.stl", (0, 0, 0), (0, 0, math.pi)),
    "upperarm": ("upperarm.stl", (0, 0, 0.120), (math.pi / 2, 0, -math.pi / 2)),
    "forearm":  ("forearm.stl", (0, 0, 0.027), (math.pi / 2, 0, -math.pi / 2)),
    "wrist1":   ("wrist1.stl", (0, 0, -0.104), (math.pi / 2, 0, 0)),
    "wrist2":   ("wrist2.stl", (0, 0, -0.08535), (0, 0, 0)),
    "wrist3":   ("wrist3.stl", (0, 0, -0.0921), (math.pi / 2, 0, 0)),
}
LINK_FRAME = {"base": 0, "shoulder": 1, "upperarm": 2, "forearm": 3, "wrist1": 4, "wrist2": 5, "wrist3": 6}
# Links the gripper can touch. wrist3 carries it (always "touching"), so it is not checked.
GRIP_VS = ("base", "shoulder", "upperarm", "forearm", "wrist1", "wrist2")


def _rpy(r: float, p: float, y: float) -> np.ndarray:
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def _sphere(v: np.ndarray) -> tuple[np.ndarray, float]:
    c = (v.min(axis=0) + v.max(axis=0)) / 2
    return c, float(np.max(np.linalg.norm(v - c, axis=1)))


def _T(xyz: Sequence[float], rpy: Sequence[float]) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = _rpy(*rpy)
    T[:3, 3] = xyz
    return T


def _rz(a: float) -> np.ndarray:
    T = np.eye(4)
    T[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    return T


def _tool0_fix() -> np.ndarray:
    return _T((0, 0, 0), (0, -math.pi / 2, -math.pi / 2)) @ _T((0, 0, 0), (math.pi / 2, 0, math.pi / 2))


def chain(q: Sequence[float]) -> list[np.ndarray]:
    """Base->link frames 0..6 (0 = base, 6 = wrist_3_link) and, as item 7, base->tool0 (the flange)."""
    out = [np.eye(4)]
    T = np.eye(4)
    for (xyz, rpy), qi in zip(JOINTS, q):
        T = T @ _T(xyz, rpy) @ _rz(float(qi))
        out.append(T)
    out.append(T @ _tool0_fix())
    return out


# ------------------------------------------------------------------ gripper geometry (flange / tool0 frame) ---
def _box(lo: Sequence[float], hi: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    v = np.array([[lo[0] if i & 1 == 0 else hi[0], lo[1] if i & 2 == 0 else hi[1], lo[2] if i & 4 == 0 else hi[2]]
                  for i in range(8)])
    f = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                  [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]])
    return v, f


def _cyl(r: float, z0: float, z1: float, n: int = 32) -> tuple[np.ndarray, np.ndarray]:
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ring = np.c_[r * np.cos(a), r * np.sin(a)]
    v = np.vstack([np.c_[ring, np.full(n, z0)], np.c_[ring, np.full(n, z1)], [[0, 0, z0], [0, 0, z1]]])
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [[i, j, n + i], [j, n + j, n + i], [2 * n, j, i], [2 * n + 1, n + i, n + j]]
    return v, np.array(f)


def _half_annulus(ri: float, ro: float, h: float, n: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """Half ring in the local XY plane, centre at origin, closed on the -X side, mouth toward +X; z in [-h/2, h/2]
    (exactly the STEP part: semicircle x<=0, inner r 26.5, outer r 33.5, 10 mm tall)."""
    a = np.linspace(math.pi / 2, 3 * math.pi / 2, n + 1)
    vs, fs, k = [], [], 0
    for i in range(n):
        pts = []
        for ang in (a[i], a[i + 1]):
            for r in (ri, ro):
                pts.append((r * math.cos(ang), r * math.sin(ang)))
        # 8-vertex wedge: (inner_a, outer_a, inner_b, outer_b) x (bottom, top)
        v = np.array([[x, y, z] for z in (-h / 2, h / 2) for x, y in pts])
        f = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                      [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]])
        vs.append(v)
        fs.append(f + k)
        k += 8
    return np.vstack(vs), np.vstack(fs)


def gripper_mesh(g: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Gripper triangles in the flange frame (z = flange axis, out of the robot).

    Layout (robot.yaml: gripper): plate on the flange; a beam along ``offset_axis_flange`` (perpendicular to z);
    a level half ring at its end, ring plane perpendicular to the beam, closed side toward ``-mouth`` and the
    beam joined to that closed side. ``layout: axial`` instead models the STEP file literally (beam along z).
    """
    pr = float(g.get("plate_radius_m", 0.0315))
    pt = float(g.get("plate_thickness_m", 0.008))
    off = float(g.get("ring_offset_m", 0.1115))
    ax = float(g.get("ring_axial_m", 0.012))
    ri, ro = float(g.get("ring_inner_radius_m", 0.0265)), float(g.get("ring_outer_radius_m", 0.0335))
    h = float(g.get("ring_height_m", 0.010))
    bw, bt = float(g.get("beam_width_m", 0.024)), float(g.get("beam_thickness_m", 0.010))
    parts: list[tuple[np.ndarray, np.ndarray]] = [_cyl(pr, 0.0, pt)]
    if str(g.get("layout", "perpendicular")) == "axial":
        # STEP literal: beam along +z from the plate to the ring, ring plane contains z, mouth +z.
        zc = off
        bv, bf = _box((-bt / 2, -bw / 2, pt), (bt / 2, bw / 2, zc - ro + 0.002))
        rv, rf = _half_annulus(ri, ro, h)
        rv = rv[:, [2, 1, 0]] * np.array([1, 1, 1]) + np.array([0, 0, zc])   # local x -> z
        parts += [(bv, bf), (rv, rf)]
    else:
        # Built along +x (beam) then rotated so +x = offset_axis_flange.
        mouth = 1.0 if float(g.get("mouth_sign", 1.0)) >= 0 else -1.0
        back_z = ax - mouth * (ri + ro) / 2          # mid-wall of the closed side, along the flange axis
        z_lo, z_hi = min(pt, back_z - (ro - ri) / 2), max(pt + bt, back_z + (ro - ri) / 2)
        # beam: plate -> ring; spans the flange-axis range from the plate face to the ring's closed side
        bv, bf = _box((0.0, -bw / 2, z_lo), (off - h / 2, bw / 2, z_hi))
        rv, rf = _half_annulus(ri, ro, h)           # local: closed side -X, mouth +X, thickness along z
        # ring local axes -> flange: local X -> mouth*z, local Y -> y (lateral), local Z -> x (beam)
        rv = np.c_[rv[:, 2] + off, rv[:, 1], mouth * rv[:, 0] + ax]
        if mouth < 0:
            rf = rf[:, [0, 2, 1]]
        parts += [(bv, bf), (rv, rf)]
        e = np.array(g.get("offset_axis_flange") or [1, 0, 0], float)
        e = e / np.linalg.norm(e)
        yaw = math.atan2(e[1], e[0])
        Rz = _rz(yaw)[:3, :3]
        parts = [(v @ Rz.T, f) for v, f in parts]
    vs, fs, k = [], [], 0
    for v, f in parts:
        vs.append(v)
        fs.append(f + k)
        k += len(v)
    return np.vstack(vs), np.vstack(fs)


# --------------------------------------------------------------------------------------------- the model ---
@lru_cache(maxsize=None)
def _link_mesh(name: str) -> tuple[np.ndarray, np.ndarray]:
    import trimesh
    fn, xyz, rpy = LINKS[name]
    m = trimesh.load(MESH_DIR / fn, force="mesh")
    T = _T(xyz, rpy)
    v = np.asarray(m.vertices, float) @ T[:3, :3].T + T[:3, 3]
    return v, np.asarray(m.faces, np.int64)


class MeshModel:
    def __init__(self, gripper: dict[str, Any] | None, *, table_z: float = 0.0, margin_m: float = 0.008,
                 table_margin_m: float = 0.003) -> None:
        import fcl
        self.fcl = fcl
        self.table_z = float(table_z)
        self.margin = float(margin_m)
        self.table_margin = float(table_margin_m)
        self.link_v: dict[str, np.ndarray] = {}
        self.obj: dict[str, Any] = {}
        for name in LINKS:
            v, f = _link_mesh(name)
            self.link_v[name] = v
            self.obj[name] = self._bvh(v, f)
        self.grip = dict(gripper or {}) if (gripper or {}).get("enabled") else {}
        self.grip_v: np.ndarray | None = None
        if self.grip and self.grip.get("offset_axis_flange") is None and self.grip.get("layout") != "axial":
            self.grip["offset_axis_flange"] = [1.0, 0.0, 0.0]   # placeholder; MotionGuard drops pinza_* until known
        if self.grip:
            v, f = gripper_mesh(self.grip)
            self.grip_v, self.grip_f = v, f
            self.obj["gripper"] = self._bvh(v, f)
        self.sph = {name: _sphere(v) for name, v in self.link_v.items()}
        if self.grip_v is not None:
            self.sph["gripper"] = _sphere(self.grip_v)

    def set_beam_axis(self, e: Sequence[float]) -> None:
        """Rebuild the gripper once its beam direction in the flange frame is known (learned or configured)."""
        if not self.grip:
            return
        self.grip["offset_axis_flange"] = [float(x) for x in e]
        v, f = gripper_mesh(self.grip)
        self.grip_v, self.grip_f = v, f
        self.obj["gripper"] = self._bvh(v, f)
        self.sph["gripper"] = _sphere(v)

    def _bvh(self, v: np.ndarray, f: np.ndarray) -> Any:
        m = self.fcl.BVHModel()
        m.beginModel(len(v), len(f))
        m.addSubModel(v.astype(np.float64), f.astype(np.int32))
        m.endModel()
        return self.fcl.CollisionObject(m, self.fcl.Transform())

    def poses(self, q: Sequence[float]) -> dict[str, np.ndarray]:
        fr = chain(q)
        P = {name: fr[LINK_FRAME[name]] for name in LINKS}
        if self.grip:
            P["gripper"] = fr[7]
        return P

    def _place(self, name: str, T: np.ndarray) -> Any:
        o = self.obj[name]
        o.setTransform(self.fcl.Transform(T[:3, :3], T[:3, 3]))
        return o

    def distance(self, a: str, Ta: np.ndarray, b: str, Tb: np.ndarray, *, far_m: float = 0.06) -> float:
        """Minimum distance between two placed meshes (negative = penetrating). Bounding spheres first: when they
        are more than ``far_m`` apart the sphere gap (a safe LOWER bound) is returned without asking FCL."""
        (ca, ra), (cb, rb) = self.sph[a], self.sph[b]
        gap = float(np.linalg.norm((Ta[:3, :3] @ ca + Ta[:3, 3]) - (Tb[:3, :3] @ cb + Tb[:3, 3]))) - ra - rb
        if gap > far_m:
            return gap
        req = self.fcl.DistanceRequest(enable_nearest_points=False, enable_signed_distance=True)
        res = self.fcl.DistanceResult()
        d = self.fcl.distance(self._place(a, Ta), self._place(b, Tb), req, res)
        if d <= 0:   # FCL returns -1 (or a depth) when the meshes intersect
            creq = self.fcl.CollisionRequest(num_max_contacts=1, enable_contact=True)
            cres = self.fcl.CollisionResult()
            self.fcl.collide(self.obj[a], self.obj[b], creq, cres)
            depth = max((c.penetration_depth for c in cres.contacts), default=0.0)
            return -max(float(depth), 1e-4)
        return float(d)

    def margins(self, q: Sequence[float]) -> dict[str, float]:
        """Negative = violated. ``pinza_<link>``: gripper vs that link (minus margin); ``*_tavolo``: lowest point
        of the gripper / of the links from the elbow outwards vs the table."""
        P = self.poses(q)
        m: dict[str, float] = {}
        if self.grip:
            for link in GRIP_VS:
                m[f"pinza_{link}"] = self.distance("gripper", P["gripper"], link, P[link]) - self.margin
            Tg = P["gripper"]
            low = float(np.min(self.grip_v @ Tg[2, :3] + Tg[2, 3]))
            m["pinza_tavolo_mesh"] = low - (self.table_z + self.table_margin)
        lows = []
        for link in ("forearm", "wrist1", "wrist2", "wrist3"):
            T = P[link]
            lows.append(float(np.min(self.link_v[link] @ T[2, :3] + T[2, 3])))
        m["braccio_tavolo_mesh"] = min(lows) - (self.table_z + self.table_margin)
        return m

    def worst(self, q: Sequence[float]) -> tuple[str, float]:
        return min(self.margins(q).items(), key=lambda kv: kv[1])

    def check_path(self, qa: Sequence[float], qb: Sequence[float], step_rad: float = 0.02) -> tuple[str, float, np.ndarray] | None:
        """First violation along a joint-space (movej) interpolation, or None."""
        a, b = np.asarray(qa, float), np.asarray(qb, float)
        n = max(2, int(math.ceil(float(np.max(np.abs(b - a))) / step_rad)) + 1)
        for i in range(n):
            q = a + (b - a) * i / (n - 1)
            k, v = self.worst(q)
            if v < 0:
                return k, v, q
        return None


def model_from_settings(settings: Any, table_z: float | None = None) -> MeshModel | None:
    r = settings.robot
    mc = (r.get("guard") or {}).get("mesh") or {}
    if not mc.get("enabled", True):
        return None
    try:
        import fcl  # noqa: F401
        import trimesh  # noqa: F401
    except ImportError:
        return None
    tz = float((r.get("relative") or {}).get("table_z_m", 0.0)) if table_z is None else float(table_z)
    return MeshModel(r.get("gripper"), table_z=tz, margin_m=float(mc.get("gripper_margin_m", 0.008)),
                     table_margin_m=float(mc.get("table_margin_m", 0.003)))


def export_scene(model: MeshModel) -> dict[str, Any]:
    """Meshes (local frames) for the 3D viewer."""
    out = {name: {"v": np.round(_link_mesh(name)[0], 5).ravel().tolist(),
                  "f": _link_mesh(name)[1].ravel().tolist()} for name in LINKS}
    if model.grip_v is not None:
        out["gripper"] = {"v": np.round(model.grip_v, 5).ravel().tolist(), "f": model.grip_f.ravel().tolist()}
    return out


def _main() -> None:
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.config import load_settings  # type: ignore
    ap = argparse.ArgumentParser(description="Check every named pose (and the path from home) with the mesh model.")
    ap.add_argument("--q", type=float, nargs=6, help="check one joint vector (rad)")
    ap.add_argument("--beam-down-q", type=float, nargs=6,
                    help="joints of a pose where the beam hangs straight down: prints offset_axis_flange")
    args = ap.parse_args()
    s = load_settings()
    if args.beam_down_q:
        R = chain(args.beam_down_q)[7][:3, :3]
        e = R.T @ np.array([0, 0, -1.0])
        e[2] = 0
        e /= np.linalg.norm(e)
        print(f"gripper.offset_axis_flange: [{e[0]:.3f}, {e[1]:.3f}, 0.0]")
        return
    mm = model_from_settings(s)
    if mm is None:
        print("pip install python-fcl trimesh")
        return
    todo = {"--q": args.q} if args.q else {k: p["joints"] for k, p in (s.poses or {}).items()
                                           if isinstance(p, dict) and p.get("joints")}
    home = (s.poses or {}).get("home", {}).get("joints")
    for name, q in todo.items():
        k, v = mm.worst(q)
        path = mm.check_path(home, q) if home else None
        print(f"{name:34s} {'OK ' if v >= 0 else 'NO '} worst {k:22s} {v * 1000:7.1f} mm   "
              f"from home: {'ok' if path is None else f'{path[0]} {path[1] * 1000:.0f} mm'}")


if __name__ == "__main__":
    _main()
