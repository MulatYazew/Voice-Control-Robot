"""Motion guard: every motion is re-checked from the robot's ACTUAL joints just before it is sent, and a blocked
direct move is replaced by a short chain of safe via points.

Why protective stops happen on this cell (UR article "Understanding protective stops", error codes C153/C154/C204):
  * movel passes near a singularity (wrist |sin q5|~0, elbow straight/folded |sin q3|~0, wrist centre on the base
    axis): the controller needs huge joint speeds for a slow tool speed and stops.
  * movej from a pose that is not "home" sweeps the arm through the table, the base or its own links.
  * a joint runs into its configured position limit.
  * accelerations with no blends; wrong payload/TCP (configuration, not code: set them on the pendant).

What this module does, without any extra dependency:
  1. A configuration check with margins: joint limits, the three UR singularities, link capsules vs the table,
     vs the base column, vs each other, vs boxes listed in ``guard.obstacles``.
  2. Segment checks that follow what the controller really does: movej is interpolated in joint space; movel is
     tracked along the straight Cartesian line with IK, and refused if the joints would have to move faster than
     ``movel_max_rad_per_m`` per metre of tool travel (the singularity symptom) or if a check fails.
  3. If a direct movej is blocked: a lazy probabilistic roadmap (Bohlin & Kavraki 2000, the PRM family used by
     OMPL/MoveIt) over candidate via points -- lifted start, lifted goal, the named hub poses, then random samples --
     searched with A* on travel time (max joint delta / joint speed, the movej duration), then shortcut.
  4. Via points get a small blend radius so the arm does not stop at each one (fewer jerks, fewer C204).

The guard can only make a motion safer or refuse it. It never changes the final target the operator confirmed.
"""

from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

import numpy as np

from ..robot.kinematics import IKError, Kinematics, rotvec
from ..robot.ur_ik import ik_all
from .registry import MotionStep

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "joint_limits_rad": [6.23, 6.23, 2.8, 6.23, 6.23, 6.23],   # elbow beyond ~160 deg folds into itself
    "limit_margin_rad": 0.05,
    "min_wrist_sin": 0.10,            # |sin q5|
    "min_elbow_sin": 0.12,            # |sin q3|: arm fully stretched or folded
    "min_shoulder_r_m": 0.03,         # wrist centre distance from the base axis beyond d4
    "table_z_m": None,                # None = relative.table_z_m / calibration
    "link_clearance_m": 0.03,         # arm links (surface) above the table
    "tip_below_table_m": 0.005,       # the tip may touch the table (calibration) but never go into it
    "self_margin_m": 0.02,
    "radii_m": {"base": 0.075, "upper": 0.06, "fore": 0.05, "wrist": 0.048, "tool": 0.02},
    "base_column_h_m": 0.20,
    "obstacles": [],                  # [{name, min: [x,y,z], max: [x,y,z]}] in the robot base frame, metres
    "movej_step_rad": 0.03,
    "movel_step_m": 0.005,
    "movel_max_rad_per_m": 15.0,
    "lift_m": 0.06,
    "hubs": ["home", "attesa"],
    "samples": 250,
    "neighbours": 10,
    "max_plan_s": 3.0,
    "via_penalty_s": 0.4,
    "blend_m": 0.02,
    "seed": 7,
}


class GuardRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GuardResult:
    motion: list[MotionStep]
    vias_added: int = 0
    replaced: list[str] = field(default_factory=list)
    est_time_s: float = 0.0
    plan_ms: float = 0.0


def _seg_dist(p1: np.ndarray, q1: np.ndarray, p2: np.ndarray, q2: np.ndarray) -> float:
    """Closest distance between segments p1-q1 and p2-q2."""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = float(d1 @ d1), float(d2 @ d2), float(d2 @ r)
    if a < 1e-12 and e < 1e-12:
        return float(np.linalg.norm(r))
    if a < 1e-12:
        s, t = 0.0, np.clip(f / e, 0, 1)
    else:
        c = float(d1 @ r)
        if e < 1e-12:
            t, s = 0.0, np.clip(-c / a, 0, 1)
        else:
            b = float(d1 @ d2)
            den = a * e - b * b
            s = np.clip((b * f - c * e) / den, 0, 1) if den > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0:
                t, s = 0.0, np.clip(-c / a, 0, 1)
            elif t > 1:
                t, s = 1.0, np.clip((b - c) / a, 0, 1)
    return float(np.linalg.norm((p1 + d1 * s) - (p2 + d2 * t)))


def _box_dist(p: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.linalg.norm(np.maximum(np.maximum(lo - p, 0), p - hi)))


class MotionGuard:
    def __init__(self, kin: Kinematics, cfg: dict[str, Any] | None, *, table_z: float, joint_speed: float,
                 hubs: dict[str, Sequence[float]] | None = None, gripper: dict[str, Any] | None = None) -> None:
        # Printed semicircle gripper (semicircle_gripper.step): a beam PERPENDICULAR to the flange axis and a
        # level half ring at its end, mouth along the flange axis. Modelled as capsules that join the table and
        # self-collision checks. The beam direction in the flange frame comes from robot.yaml or is learned once
        # from the first actual pose with the flange axis level and one flange axis pointing straight down.
        self.grip = dict(gripper or {}) if (gripper or {}).get("enabled") else {}
        v = self.grip.get("offset_axis_flange")
        self.beam_axis: np.ndarray | None = (np.array(v, float) / np.linalg.norm(v)) if v is not None else None
        c = {**DEFAULTS, **(cfg or {})}
        c["radii_m"] = {**DEFAULTS["radii_m"], **((cfg or {}).get("radii_m") or {})}
        self.c = c
        self.kin = kin
        self.table_z = float(c["table_z_m"]) if c.get("table_z_m") is not None else float(table_z)
        self.v = max(float(joint_speed), 1e-3)
        self.lim = np.array(c["joint_limits_rad"], dtype=float) - float(c["limit_margin_rad"])
        self.hubs = {k: np.array(v, dtype=float) for k, v in (hubs or {}).items() if k in c["hubs"]}
        self.obstacles = [(o.get("name", "box"), np.array(o["min"], float), np.array(o["max"], float))
                          for o in c["obstacles"]]

    # ============================================================ geometry ===
    def _links(self, q: Sequence[float]) -> dict[str, tuple[np.ndarray, np.ndarray, float]]:
        fr = self.kin.frames(q)
        P = [f[:3, 3] for f in fr]
        tip = self.kin.tip(q)
        R = self.c["radii_m"]
        links = {"upper": (P[1], P[2], R["upper"]), "fore": (P[2], P[3], R["fore"]),
                 "w1": (P[3], P[4], R["wrist"]), "w2": (P[4], P[5], R["wrist"]), "w3": (P[5], P[6], R["wrist"]),
                 "tool": (P[6], tip, R["tool"]),
                 "base": (np.zeros(3), np.array([0, 0, float(self.c["base_column_h_m"])]), R["base"])}
        if self.grip and self.beam_axis is not None:
            g = self.grip
            Rf = fr[-1][:3, :3]
            z, b = Rf[:, 2], Rf @ self.beam_axis
            lat = np.cross(z, b)
            off, ax = float(g.get("ring_offset_m", 0.1115)), float(g.get("ring_axial_m", 0.012))
            ro = float(g.get("ring_outer_radius_m", 0.0335))
            c = P[6] + z * ax + b * off
            root = P[6] + z * 0.008 + b * float(g.get("plate_radius_m", 0.0315))
            hh = float(g.get("ring_height_m", 0.010)) / 2
            links["beam"] = (root, c - z * ro - b * (0.008 + hh), 0.008)   # beam ends on top of the ring
            links["ring_l"] = (c - z * ro, c + lat * ro, hh)
            links["ring_r"] = (c - z * ro, c - lat * ro, hh)
        return links

    def learn_beam(self, q: Sequence[float]) -> None:
        """Beam direction = the flange x/y axis pointing straight down (within 20°) in this actual pose."""
        if not self.grip or self.beam_axis is not None:
            return
        R = self.kin.frames(q)[-1][:3, :3]
        for e in ((1.0, 0, 0), (-1.0, 0, 0), (0, 1.0, 0), (0, -1.0, 0)):
            if -float((R @ np.array(e))[2]) > math.cos(math.radians(20)):
                self.beam_axis = np.array(e, dtype=float)
                return

    SELF_PAIRS = [("tool", "base"), ("tool", "upper"), ("tool", "fore"), ("tool", "w1"),
                  ("w3", "base"), ("w3", "upper"), ("w3", "fore"),
                  ("w2", "base"), ("w2", "upper"), ("w1", "base"), ("fore", "base")]

    def margins(self, q: Sequence[float], singular: bool = False) -> dict[str, float]:
        """Every safety margin of one configuration. Negative = violated. Keys name the reason.

        Singularities only matter for Cartesian (movel) motion: a movej passes through them without trouble
        (the upright "home" pose itself is singular), so they are checked only with ``singular=True``.
        """
        q = np.asarray(q, dtype=float)
        m: dict[str, float] = {}
        m["limite_giunto"] = float(np.min(self.lim - np.abs(q)))
        if singular:
            m["singolarita_polso"] = abs(math.sin(q[4])) - float(self.c["min_wrist_sin"])
            m["singolarita_gomito"] = abs(math.sin(q[2])) - float(self.c["min_elbow_sin"])
            p05 = self.kin.frames(q)[5][:3, 3]
            m["singolarita_spalla"] = (math.hypot(p05[0], p05[1]) - abs(self.kin.d[3])
                                       - float(self.c["min_shoulder_r_m"]))
        L = self._links(q)
        low = min(min(a[2], b[2]) - r for name, (a, b, r) in L.items()
                  if name not in ("tool", "base", "beam", "ring_l", "ring_r"))   # gripper: own check below
        m["tavolo"] = low - (self.table_z + float(self.c["link_clearance_m"]))
        m["punta_nel_tavolo"] = float(L["tool"][1][2]) - (self.table_z - float(self.c["tip_below_table_m"]))
        sm = float(self.c["self_margin_m"])
        m["autocollisione"] = min(_seg_dist(*L[a][:2], *L[b][:2]) - L[a][2] - L[b][2] - sm for a, b in self.SELF_PAIRS)
        if "beam" in L:
            gl = float(self.grip.get("table_clearance_m", 0.003))
            m["pinza_tavolo"] = min(min(L[k][0][2], L[k][1][2]) - L[k][2]
                                    for k in ("beam", "ring_l", "ring_r")) - (self.table_z + gl)
            gm = float(self.grip.get("self_margin_m", 0.01))
            m["pinza_braccio"] = min(_seg_dist(*L[a][:2], *L[b][:2]) - L[a][2] - L[b][2] - gm
                                     for a in ("beam", "ring_l", "ring_r")
                                     for b in ("base", "upper", "fore", "w1"))
        if self.obstacles:
            worst = math.inf
            for name, (a, b, r) in L.items():
                if name == "base":
                    continue
                for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                    p = a + (b - a) * t
                    for _n, lo, hi in self.obstacles:
                        worst = min(worst, _box_dist(p, lo, hi) - r - sm)
            m["ostacolo"] = worst
        return m

    def config_ok(self, q: Sequence[float]) -> bool:
        return min(self.margins(q).values()) >= 0

    # ============================================================ segments ===
    def _walk(self, samples: list[np.ndarray], singular: bool = False) -> str | None:
        """First violated check along the samples, or None. A violation already present at the start is
        tolerated while it does not get worse, so the arm can always move OUT of a marginal pose."""
        prev = self.margins(samples[0], singular)
        escaping = {k for k, v in prev.items() if v < 0}
        for q in samples[1:]:
            cur = self.margins(q, singular)
            for k, v in cur.items():
                if v >= 0:
                    escaping.discard(k)
                    continue
                if k in escaping and v >= prev[k] - 1e-4:
                    continue
                return k
            escaping &= {k for k, v in cur.items() if v < 0}
            prev = cur
        return None

    def check_movej(self, qa: Sequence[float], qb: Sequence[float]) -> str | None:
        a, b = np.asarray(qa, float), np.asarray(qb, float)
        n = max(2, int(math.ceil(float(np.max(np.abs(b - a))) / float(self.c["movej_step_rad"]))) + 1)
        return self._walk([a + (b - a) * i / (n - 1) for i in range(n)])

    def track_movel(self, qa: Sequence[float], qb: Sequence[float]) -> tuple[list[np.ndarray] | None, str | None]:
        """Joints along the straight tool line from fk(qa) to fk(qb), as the controller's movel would do."""
        Ta, Tb = self.kin.fk(qa), self.kin.fk(qb)
        dist = float(np.linalg.norm(Tb[:3, 3] - Ta[:3, 3]))
        rv = rotvec(Tb[:3, :3] @ Ta[:3, :3].T)
        n = max(2, int(math.ceil(max(dist / float(self.c["movel_step_m"]), float(np.linalg.norm(rv)) / 0.02))) + 1)
        q = np.asarray(qa, float)
        out = [q]
        step = dist / (n - 1)
        for i in range(1, n):
            s = i / (n - 1)
            T = np.eye(4)
            T[:3, 3] = Ta[:3, 3] + (Tb[:3, 3] - Ta[:3, 3]) * s
            T[:3, :3] = _rot(rv * s) @ Ta[:3, :3]
            try:
                qn = self.kin.ik(T, q, iters=60)
            except IKError:
                return None, "percorso_lineare_irraggiungibile"
            dq = float(np.max(np.abs(qn - q)))
            if step > 1e-6 and dq / step > float(self.c["movel_max_rad_per_m"]):
                return None, "singolarita_sul_percorso_lineare"
            q = qn
            out.append(q)
        if float(np.max(np.abs(out[-1] - np.asarray(qb, float)))) > 0.05:
            return None, "cambio_configurazione_sul_percorso_lineare"
        return out, None

    def check_movel(self, qa: Sequence[float], qb: Sequence[float]) -> str | None:
        samples, why = self.track_movel(qa, qb)
        if why:
            return why
        return self._walk(samples, singular=True)

    # ============================================================ planning ===
    def cost(self, qa: Sequence[float], qb: Sequence[float]) -> float:
        return float(np.max(np.abs(np.asarray(qb, float) - np.asarray(qa, float)))) / self.v

    def lifted(self, q: Sequence[float], h: float | None = None) -> np.ndarray | None:
        """Same joints branch, tip raised by ``h`` straight up, if the straight lift itself is safe."""
        T = self.kin.fk(q)
        T[2, 3] += float(self.c["lift_m"] if h is None else h)
        try:
            ql = self.kin.ik(T, q, iters=80)
        except IKError:
            return None
        if float(np.max(np.abs(ql - np.asarray(q, float)))) > 0.8 or not self.config_ok(ql):
            return None
        return ql

    def _sample(self, rng: np.random.Generator, qa: np.ndarray, qb: np.ndarray) -> np.ndarray:
        """Half the samples around the start/goal corridor, half uniform in a sane box (arm above the table)."""
        if rng.random() < 0.5:
            t = rng.random()
            return qa + (qb - qa) * t + rng.normal(0, 0.5, 6)
        lo = np.array([-math.pi, -math.pi, -2.6, -math.pi, -math.pi, -math.pi])
        hi = np.array([math.pi, 0.0, 2.6, math.pi, math.pi, math.pi])
        q = rng.uniform(lo, hi)
        q[5] = qb[5] + rng.normal(0, 0.3)            # wrist 3 only spins the tool: keep it near the goal
        return q

    def plan(self, qa: Sequence[float], qb: Sequence[float]) -> list[np.ndarray]:
        """Via points (ending with qb) for a movej from qa to qb. Raises GuardRejected."""
        a, b = np.asarray(qa, float), np.asarray(qb, float)
        if not self.config_ok(b):
            bad = min(self.margins(b).items(), key=lambda kv: kv[1])[0]
            raise GuardRejected(bad, f"Il punto di arrivo non è sicuro ({bad}).")
        if self.check_movej(a, b) is None:
            return [b]
        t0 = time.monotonic()
        nodes: list[np.ndarray] = [a, b]
        for q in (self.lifted(a), self.lifted(b), *self.hubs.values()):
            if q is not None and self.config_ok(q):
                nodes.append(np.asarray(q, float))
        rng = np.random.default_rng(int(self.c["seed"]))
        bad_edges: set[tuple[int, int]] = set()
        good_edges: set[tuple[int, int]] = set()

        def edge_ok(i: int, j: int) -> bool:
            key = (min(i, j), max(i, j))
            if key in good_edges:
                return True
            if key in bad_edges:
                return False
            ok = self.check_movej(nodes[i], nodes[j]) is None
            (good_edges if ok else bad_edges).add(key)
            return ok

        batch = 0
        while True:
            path = self._lazy_astar(nodes, edge_ok, bad_edges)
            if path is not None:
                vias = self._shortcut([nodes[i] for i in path])
                return vias[1:]
            if time.monotonic() - t0 > float(self.c["max_plan_s"]) or batch * 50 >= int(self.c["samples"]):
                raise GuardRejected("nessun_percorso_sicuro",
                                    "Non trovo un percorso sicuro fino alla posizione richiesta: "
                                    f"il movimento diretto passerebbe per '{self.check_movej(a, b)}'.")
            batch += 1
            added = 0
            while added < 50 and time.monotonic() - t0 < float(self.c["max_plan_s"]):
                q = self._sample(rng, a, b)
                if self.config_ok(q):
                    nodes.append(q)
                    added += 1

    def _lazy_astar(self, nodes: list[np.ndarray], edge_ok, bad_edges: set) -> list[int] | None:
        """A* on optimistic edges (k nearest by time); collision-check only the edges on the best path."""
        k = int(self.c["neighbours"])
        pen = float(self.c["via_penalty_s"])
        N = len(nodes)
        Q = np.array(nodes)
        times = np.max(np.abs(Q[:, None, :] - Q[None, :, :]), axis=2) / self.v
        nbr = {i: [int(j) for j in np.argsort(times[i])[1:k + 1]] for i in range(N)}
        for i in range(N):                       # start and goal always try every node
            for j in (0, 1):
                if i != j and i not in nbr[j]:
                    nbr[j].append(i)
                    nbr[i].append(j)
        while True:
            dist = {0: 0.0}
            parent: dict[int, int] = {}
            heap = [(times[0, 1], 0)]
            done: set[int] = set()
            found = False
            while heap:
                _, u = heapq.heappop(heap)
                if u in done:
                    continue
                if u == 1:
                    found = True
                    break
                done.add(u)
                for v in nbr[u]:
                    if (min(u, v), max(u, v)) in bad_edges:
                        continue
                    nd = dist[u] + times[u, v] + (pen if v != 1 else 0.0)
                    if nd < dist.get(v, math.inf):
                        dist[v], parent[v] = nd, u
                        heapq.heappush(heap, (nd + times[v, 1], v))
            if not found:
                return None
            path = [1]
            while path[-1] != 0:
                path.append(parent[path[-1]])
            path.reverse()
            if all(edge_ok(path[i], path[i + 1]) for i in range(len(path) - 1)):
                return path

    def _shortcut(self, path: list[np.ndarray]) -> list[np.ndarray]:
        out = [path[0]]
        i = 0
        while i < len(path) - 1:
            j = len(path) - 1
            while j > i + 1 and self.check_movej(path[i], path[j]) is not None:
                j -= 1
            out.append(path[j])
            i = j
        return out

    # ============================================================== steps ===
    def secure(self, q0: Sequence[float], motion: Sequence[MotionStep]) -> GuardResult:
        """The motion the robot will actually run, starting from the measured joints ``q0``."""
        t0 = time.monotonic()
        q = np.asarray(q0, float)
        self.learn_beam(q)
        out: list[MotionStep] = []
        res = GuardResult(motion=out)
        for idx, st in enumerate(motion):
            if st.move == "wait" or st.joints is None:
                out.append(st)
                continue
            target = np.asarray(st.joints, float)
            if st.move == "movel":
                why = self.check_movel(q, target)
                if why is None:
                    out.append(st)
                    res.est_time_s += float(np.linalg.norm(self.kin.tip(target) - self.kin.tip(q))) / max(st.speed, 1e-3)
                    q = target
                    continue
                if self.check_movej(q, target) is None and self._near_line(q, target, 0.02):
                    out.append(replace(st, move="movej", speed=self.v, accel=max(st.accel, 0.3)))
                    res.replaced.append(f"passo {idx + 1}: movel->movej ({why})")
                    res.est_time_s += self.cost(q, target)
                    q = target
                    continue
                raise GuardRejected(why, f"Il movimento lineare del passo {idx + 1} non è sicuro ({why}).")
            vias = self.plan(q, target)
            pts = [q, *vias]
            for k, v in enumerate(vias):
                last = k == len(vias) - 1
                blend = 0.0 if last else self._blend(pts[k], v, pts[k + 2])
                out.append(st if last else MotionStep("movej", None, tuple(round(float(x), 6) for x in v),
                                                       st.speed, st.accel, blend=blend))
                res.est_time_s += self.cost(pts[k], v)
            res.vias_added += len(vias) - 1
            q = target
        res.plan_ms = round((time.monotonic() - t0) * 1000, 1)
        return res

    def _near_line(self, qa, qb, tol_m: float) -> bool:
        """The joint-space move stays within tol of the straight tool line (so movej can replace a movel)."""
        pa, pb = self.kin.tip(qa), self.kin.tip(qb)
        for i in range(1, 10):
            p = self.kin.tip(np.asarray(qa) + (np.asarray(qb) - np.asarray(qa)) * i / 10)
            if _seg_dist(p, p, pa, pb) > tol_m:
                return False
        return True

    def _blend(self, qa, qv, qn) -> float:
        a, v, n = self.kin.tip(qa), self.kin.tip(qv), self.kin.tip(qn)
        r = min(float(self.c["blend_m"]), 0.4 * float(np.linalg.norm(v - a)), 0.4 * float(np.linalg.norm(n - v)))
        return round(r, 4) if r >= 0.002 else 0.0


def _rot(rv: np.ndarray) -> np.ndarray:
    th = float(np.linalg.norm(rv))
    if th < 1e-12:
        return np.eye(3)
    k = rv / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K


def guard_from_settings(settings: Any, kin: Kinematics | None = None, table_z: float | None = None) -> MotionGuard:
    """The guard models the PHYSICAL tool (``guard.tool_length_m``, else ``tool.tip_length_m``, else bare flange):
    the planner's 15 cm virtual tip is only a pointing aid and must not be treated as a rigid needle. The same
    length is used as the TCP for movel tracking, so set it equal to the TCP configured on the teach pendant."""
    from ..robot.kinematics import model_of
    r = settings.robot
    rel = r.get("relative") or {}
    g = r.get("guard") or {}
    L = g.get("tool_length_m")
    if L is None:
        L = (r.get("tool") or {}).get("tip_length_m")
    kin = Kinematics(model_of(str((r.get("robot") or {}).get("model", "UR3e"))), (0.0, 0.0, float(L or 0.0)))
    tz = float(rel.get("table_z_m", 0.0)) if table_z is None else float(table_z)
    hubs = {name: p["joints"] for name, p in (settings.poses or {}).items()
            if isinstance(p, dict) and p.get("joints") and (settings.mode != "real" or p.get("verificata_reale"))}
    return MotionGuard(kin, r.get("guard"), table_z=tz, joint_speed=float(settings.motion["joint_speed"]), hubs=hubs,
                       gripper=r.get("gripper"))


def tool_down(yaw: float) -> np.ndarray:
    """Rotation with the tool axis (z) pointing straight down and the tool x axis at ``yaw`` in the table plane."""
    x = np.array([math.cos(yaw), math.sin(yaw), 0.0])
    z = np.array([0.0, 0.0, -1.0])
    return np.column_stack((x, np.cross(z, x), z))


def best_route(guard: MotionGuard, q0: Sequence[float], poses: Sequence[np.ndarray]) -> tuple[list[np.ndarray], float]:
    """Cheapest safe way (via points + final joints) to ANY of the given flange poses, over every IK branch.

    The candidates are ranked by direct travel time first, and only the promising ones are planned, so the
    search stays well under a second.
    """
    q0 = np.asarray(q0, float)
    cands = []
    for T in poses:
        for q in ik_all(guard.kin, T, q0):
            if guard.config_ok(q):
                cands.append(q)
    if not cands:
        raise GuardRejected("irraggiungibile", "Nessuna postura del braccio raggiunge quel punto in sicurezza.")
    cands.sort(key=lambda q: guard.cost(q0, q))
    best: tuple[list[np.ndarray], float] | None = None
    for q in cands[:8]:
        if best is not None and guard.cost(q0, q) >= best[1]:
            break
        try:
            vias = guard.plan(q0, q)
        except GuardRejected:
            continue
        pts = [q0, *vias]
        t = sum(guard.cost(pts[i], pts[i + 1]) for i in range(len(vias))) + guard.c["via_penalty_s"] * (len(vias) - 1)
        if best is None or t < best[1]:
            best = (vias, t)
    if best is None:
        raise GuardRejected("nessun_percorso_sicuro", "Non trovo un percorso sicuro sopra quel punto.")
    return best


def hover_and_lower(guard: MotionGuard, q0: Sequence[float], xy: Sequence[float], *, hover_flange_z: float,
                    low_flange_z: float, speeds: dict[str, float], descent_speed: float = 0.03,
                    yaw_steps: int = 8) -> tuple[list[MotionStep], dict[str, Any]]:
    """From ANY current pose: the fastest safe joint route to the flange straight above ``xy`` (tool down, any
    heading, any IK branch), then a slow straight descent to ``low_flange_z``. The descent is checked as a movel."""
    poses = []
    for k in range(yaw_steps):
        T = np.eye(4)
        T[:3, :3] = tool_down(2 * math.pi * k / yaw_steps)
        T[:3, 3] = [float(xy[0]), float(xy[1]), float(hover_flange_z)]
        poses.append(T)
    vias, t = best_route(guard, q0, poses)
    q_hover = vias[-1]
    T_low = guard.kin.fk(q_hover)
    T_low[2, 3] = float(low_flange_z)
    try:
        q_low = guard.kin.ik(T_low, q_hover, iters=200)
    except IKError:
        raise GuardRejected("irraggiungibile", "Non riesco a scendere sopra il bicchiere da qui.")
    why = guard.check_movel(q_hover, q_low)
    if why:
        raise GuardRejected(why, f"La discesa verticale non è sicura ({why}).")
    r = lambda v: tuple(round(float(x), 6) for x in v)  # noqa: E731
    pts = [np.asarray(q0, float), *vias]
    motion = []
    for i, v in enumerate(vias):
        last = i == len(vias) - 1
        blend = 0.0 if last else guard._blend(pts[i], v, pts[i + 2])
        motion.append(MotionStep("movej", None, r(v), speeds["joint_speed"], speeds["joint_accel"], blend=blend))
    drop = min(float(speeds["linear_speed"]), float(descent_speed))
    motion.append(MotionStep("movel", None, r(q_low), drop, speeds["linear_accel"]))
    down = float(hover_flange_z) - float(low_flange_z)
    return motion, {"vias": len(vias) - 1, "travel_s": round(t, 2), "descent_mm": round(down * 1000),
                    "timeout_s": t * 2.5 + down / max(drop, 1e-3) * 2 + 10}
