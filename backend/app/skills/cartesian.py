"""Computed motions: relative moves ("3 cm to the left"), joint rotations ("base 30°") and touching a point
seen by the calibrated camera.

Everything here turns into ordinary joint targets (MotionStep with joints), so the adapters, the
confirmation gate, STOP and the arrival check on actual_q work exactly as for recorded poses.
Nothing comes from the language model: direction and joint are closed enums, the amount is read from the
operator's words, the touch target from the camera calibration. Every target is checked here first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from ..config import Settings
from ..robot.kinematics import IKError, Kinematics, model_of, rotvec
from .registry import MotionStep, SkillError, norm

FUORI_LIMITI = "fuori_dai_limiti"
RICHIESTA_AMBIGUA = "richiesta_ambigua"
NON_CALIBRATO = "non_calibrato"

DIRECTIONS = ["sinistra", "destra", "avanti", "indietro", "su", "giu"]
DIR_ALIASES = {
    "sinistra": ["left", "izquierda", "gauche", "links", "左", "左边", "向左", "往左"],
    "destra": ["right", "derecha", "droite", "rechts", "右", "右边", "向右", "往右"],
    "avanti": ["forward", "forwards", "front", "adelante", "avant", "vorne", "vorwarts", "前", "向前", "往前"],
    "indietro": ["back", "backward", "backwards", "atras", "arriere", "zuruck", "后", "向后", "往后"],
    "su": ["up", "alto", "in alto", "arriba", "haut", "en haut", "oben", "hoch", "上", "向上", "往上"],
    "giu": ["giù", "down", "basso", "in basso", "abajo", "bas", "en bas", "unten", "runter", "下", "向下", "往下"],
}
JOINTS = ["base", "spalla", "gomito", "polso1", "polso2", "polso3"]
JOINT_ALIASES = {
    "base": ["1", "giunto 1", "joint 1", "shoulder pan", "basis", "底座", "基座", "第一关节", "1号关节", "关节1", "ベース"],
    "spalla": ["2", "giunto 2", "joint 2", "shoulder", "hombro", "epaule", "schulter", "肩", "肩膀", "第二关节", "2号关节",
               "关节2"],
    "gomito": ["3", "giunto 3", "joint 3", "elbow", "codo", "coude", "ellbogen", "肘", "手肘", "第三关节", "3号关节", "关节3"],
    "polso1": ["4", "giunto 4", "joint 4", "wrist 1", "wrist1", "polso 1", "手腕1", "腕1", "第四关节", "4号关节", "关节4"],
    "polso2": ["5", "giunto 5", "joint 5", "wrist 2", "wrist2", "polso 2", "手腕2", "腕2", "第五关节", "5号关节", "关节5"],
    "polso3": ["6", "giunto 6", "joint 6", "wrist 3", "wrist3", "polso 3", "手腕3", "腕3", "第六关节", "6号关节", "关节6",
               "tool flange", "flangia"],
}
SIDES = ["piu", "meno", "sinistra", "destra", "su", "giu"]


def _canon(value: Any, table: dict[str, list[str]]) -> str | None:
    """Exact id or alias, or an alias used inside a longer phrase ('a la izquierda', 'die Basis')."""
    n = norm(value).replace("_", " ")
    if not n:
        return None
    names = {canon: [canon, *aliases] for canon, aliases in table.items()}
    for canon, aliases in names.items():
        if any(n == norm(a).replace("_", " ") for a in aliases):
            return canon
    words = set(n.split())
    best: str | None = None
    best_len = 0
    for canon, aliases in names.items():
        for a in aliases:
            na = norm(a).replace("_", " ")
            if not na or na.isdigit():
                continue
            hit = na in words or (" " in na and na in n) or (not na.isascii() and len(na) > 1 and na in n)
            if hit and len(na) > best_len:
                best, best_len = canon, len(na)
    return best


def canon_direction(v: Any) -> str | None:
    return _canon(v, DIR_ALIASES)


def canon_joint(v: Any) -> str | None:
    return _canon(v, JOINT_ALIASES)


def canon_side(v: Any) -> str | None:
    n = norm(v)
    if n in ("piu", "più", "+", "positivo", "positive", "plus", "正", "正方向"):
        return "piu"
    if n in ("meno", "-", "negativo", "negative", "minus", "负", "反方向", "负方向"):
        return "meno"
    return canon_direction(v) if canon_direction(v) in ("sinistra", "destra", "su", "giu") else None


def _largest_true_rect(mask: np.ndarray) -> tuple[int, int, int, int, int]:
    """Largest all-true rectangle. Returns area, row0, col0, row1, col1 (inclusive)."""
    rows, cols = mask.shape
    height = [0] * cols
    best = (0, 0, 0, 0, 0)
    for r in range(rows):
        for c in range(cols):
            height[c] = height[c] + 1 if mask[r, c] else 0
        stack = [-1]
        for c in range(cols + 1):
            h = height[c] if c < cols else 0
            while stack[-1] != -1 and height[stack[-1]] > h:
                hh = height[stack.pop()]
                left = stack[-1] + 1
                right = c - 1
                area = hh * (right - left + 1)
                if area > best[0]:
                    best = (area, r - hh + 1, left, r, right)
            stack.append(c)
    return best


@dataclass
class Plan:
    motion: list[MotionStep]
    timeout_s: float
    info: dict[str, Any]


class Planner:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        r = settings.robot
        self.rel = dict(r.get("relative") or {})
        self.tch = dict(r.get("touch") or {})
        tool = r.get("tool") or {}
        L = tool.get("tip_length_m")
        self.tip_length_measured = L is not None
        self.kin = Kinematics(model_of(str((r.get("robot") or {}).get("model", "UR3e"))),
                              (0.0, 0.0, float(L if L is not None else self.rel.get("assumed_tip_length_m", 0.15))))

    # ------------------------------------------------------------ limits ---
    @property
    def max_deg(self) -> float:
        return float(self.rel.get("max_deg", 360))

    def _table_z(self, cal: Any) -> float:
        if cal is not None and cal.table_z is not None:
            return float(cal.table_z)
        return float(self.rel.get("table_z_m", 0.0))

    def _check(self, q: Sequence[float], table_z: float, *, tip_clearance: float) -> None:
        """Static checks on a joint target."""
        lim = 2 * math.pi - 0.05
        if any(abs(x) > lim for x in q):
            raise SkillError(FUORI_LIMITI, "Un giunto supererebbe il suo limite di rotazione.")
        if abs(math.sin(q[4])) < float(self.rel.get("min_wrist_sin", 0.12)):
            raise SkillError(FUORI_LIMITI, "Il polso sarebbe troppo vicino a una singolarità: movimento rifiutato.")
        fr = self.kin.frames(q)
        tip = self.kin.tip(q)
        if tip[2] < table_z + tip_clearance - 1e-6:
            raise SkillError(FUORI_LIMITI, f"La punta scenderebbe sotto il limite del tavolo "
                                           f"({(tip[2] - table_z) * 1000:.0f} mm dal tavolo).")
        joint_clear = float(self.rel.get("joint_clearance_m", 0.04))
        for i in (2, 3, 4, 5, 6):
            if fr[i][2, 3] < table_z + joint_clear:
                raise SkillError(FUORI_LIMITI, "Una parte del braccio andrebbe troppo vicina al tavolo.")
        r = math.hypot(tip[0], tip[1])
        if r > float(self.rel.get("reach_max_m", 0.5)):
            raise SkillError(FUORI_LIMITI, f"Troppo lontano dalla base ({r * 1000:.0f} mm).")
        if r < float(self.rel.get("reach_min_m", 0.12)):
            raise SkillError(FUORI_LIMITI, "Troppo vicino all'asse della base.")

    def _check_path(self, q0: Sequence[float], q1: Sequence[float], table_z: float, *, tip_clearance: float,
                    n: int = 12) -> None:
        a, b = np.array(q0, dtype=float), np.array(q1, dtype=float)
        for i in range(1, n + 1):
            self._check(a + (b - a) * i / n, table_z, tip_clearance=tip_clearance)

    def _ik(self, T: np.ndarray, qnear: Sequence[float], max_change: float) -> np.ndarray:
        try:
            q = self.kin.ik(T, qnear)
        except IKError:
            raise SkillError(FUORI_LIMITI, "Posizione non raggiungibile con l'orientamento attuale della punta.")
        if float(np.max(np.abs(q - np.array(qnear)))) > max_change:
            raise SkillError(FUORI_LIMITI, "Servirebbe una grande rotazione dei giunti: movimento rifiutato.")
        return q

    def _speeds(self) -> dict[str, float]:
        return self.s.motion

    # ----------------------------------------------------------- motions ---
    def translate(self, q: Sequence[float], direction: str, cm: float, cal: Any = None) -> Plan:
        if direction not in DIRECTIONS:
            raise SkillError(RICHIESTA_AMBIGUA, "Direzione non valida.", param="direzione", options=DIRECTIONS)
        if not math.isfinite(cm) or cm <= 0:
            raise SkillError(FUORI_LIMITI, "La distanza deve essere maggiore di zero.", param="quantita")
        vec = np.array((self.rel.get("frame") or {}).get(direction) or [0, 0, 0], dtype=float)
        if np.linalg.norm(vec) < 1e-6:
            raise SkillError(FUORI_LIMITI, f"Direzione {direction} non configurata in robot.yaml (relative.frame).")
        vec = vec / np.linalg.norm(vec)
        q = np.array(q, dtype=float)
        flange, R = self._flange_pose(q)
        delta = vec * cm / 100.0
        target = np.array(flange, dtype=float) + delta
        tip1 = self.kin.tip(q) + delta
        radius = math.hypot(float(tip1[0]), float(tip1[1]))
        if radius > float(self.rel.get("reach_max_m", 0.5)):
            raise SkillError(FUORI_LIMITI, f"Troppo lontano dalla base ({radius * 1000:.0f} mm).")
        if radius < float(self.rel.get("reach_min_m", 0.12)):
            raise SkillError(FUORI_LIMITI, "Troppo vicino all'asse della base.")
        tz = self._table_z(cal)
        z_floor = tz + float(self.rel.get("joint_clearance_m", 0.04))
        limit = float(self.rel.get("max_joint_change_rad", 0.8))
        steps = self._flange_segment(q, R, flange, R, target, limit, z_floor)
        m = self._speeds()
        motion = [MotionStep("movel", None, tuple(round(float(x), 6) for x in qq),
                             m["linear_speed"], m["linear_accel"]) for qq in steps]
        dur = cm / 100.0 / max(m["linear_speed"], 1e-3)
        return Plan(motion, timeout_s=dur * 3 + 8,
                    info={"tip_mm": [round(float(c) * 1000, 1) for c in tip1], "cm": round(cm, 1)})

    def rotate(self, q: Sequence[float], joint: str, side: str, deg: float, cal: Any = None) -> Plan:
        if joint not in JOINTS:
            raise SkillError(RICHIESTA_AMBIGUA, "Quale giunto?", param="giunto", options=JOINTS)
        if not 0 < deg <= self.max_deg:
            raise SkillError(FUORI_LIMITI, f"Al massimo {self.max_deg:g} gradi per comando.", param="quantita")
        sign = {"piu": 1, "meno": -1}.get(side)
        if sign is None:
            sign = ((self.rel.get("joint_sides") or {}).get(joint) or {}).get(side)
        if sign not in (1, -1):
            raise SkillError(RICHIESTA_AMBIGUA, f"In che verso ruoto {joint}?", param="verso",
                             options=["piu", "meno"] + list(((self.rel.get("joint_sides") or {}).get(joint) or {})))
        i = JOINTS.index(joint)
        q1 = list(q)
        q1[i] = q[i] + sign * math.radians(deg)
        tz = self._table_z(cal)
        clear = float(self.rel.get("tip_clearance_m", 0.02))
        start_clear = self.kin.tip(q)[2] - tz
        self._check_path(q, q1, tz, tip_clearance=min(clear, start_clear))
        m = self._speeds()
        dur = math.radians(deg) / max(m["joint_speed"], 1e-3)
        step = MotionStep("movej", None, tuple(round(float(x), 6) for x in q1), m["joint_speed"], m["joint_accel"])
        return Plan([step], timeout_s=dur * 3 + 8, info={"segno": sign})

    def _calib(self) -> dict[str, Any]:
        return dict(self.s.robot.get("calib") or {})

    def _reachable_at(self, qnear: Sequence[float], rot: np.ndarray, x: float, y: float, z: float) -> np.ndarray | None:
        """Joints that put the tip on (x, y, z) with the same orientation, or None."""
        if not (float(self.rel.get("reach_min_m", 0.12)) <= math.hypot(x, y) <= float(self.rel.get("reach_max_m", 0.5))):
            return None
        M = np.eye(4)
        M[:3, :3] = rot
        M[:3, 3] = [x, y, z]
        try:
            q1 = self.kin.ik(M, qnear, iters=50)
        except IKError:
            return None
        try:
            self._check(q1, z, tip_clearance=0.0)
        except SkillError:
            return None
        tip = self.kin.tip(q1)
        if max(abs(float(tip[0]) - x), abs(float(tip[1]) - y), abs(float(tip[2]) - z)) > 0.004:
            return None
        return q1

    def _reach_cells(self, q: Sequence[float], rot: np.ndarray | None = None, z: float | None = None):
        """Every grid point on this table height that the UR3e model can touch with the tip straight down.

        The limits are the arm's own: link lengths in the kinematics, plus ``reach_min_m`` / ``reach_max_m``.
        A paper cup is not an input. The cup may sit on any of these points.
        """
        T = self.kin.fk(q)
        R = np.array(T[:3, :3] if rot is None else rot, dtype=float)
        tilt = float(np.degrees(np.arccos(np.clip(-float(R[2, 2]), -1.0, 1.0))))
        if tilt > 20.0:
            raise SkillError(FUORI_LIMITI, "punta_non_verticale")
        tip_z = float(T[2, 3])
        z = tip_z if z is None else float(z)
        reach = float(self.rel.get("reach_max_m", 0.5))
        step = 0.04
        xs = np.round(np.arange(-reach, reach + step * 0.5, step), 5)
        ys = np.round(np.arange(-reach, reach + step * 0.5, step), 5)
        tip = T[:3, 3]
        i0 = int(np.argmin(np.abs(xs - tip[0])))
        j0 = int(np.argmin(np.abs(ys - tip[1])))
        if abs(tip_z - z) <= 0.005:
            seed0 = np.array(q, dtype=float)
        else:
            seed0 = self._reachable_at(q, R, float(xs[i0]), float(ys[j0]), z)
            if seed0 is None:
                raise SkillError(FUORI_LIMITI, "area_troppo_piccola")
        sols: dict[tuple[int, int], np.ndarray] = {(i0, j0): seed0}
        seen: set[tuple[int, int]] = {(i0, j0)}
        queue = [(i0 - 1, j0), (i0 + 1, j0), (i0, j0 - 1), (i0, j0 + 1)]
        while queue:
            i, j = queue.pop()
            if (i, j) in seen or not (0 <= i < len(xs) and 0 <= j < len(ys)):
                continue
            seen.add((i, j))
            seed = seed0
            for ni, nj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if (ni, nj) in sols:
                    seed = sols[(ni, nj)]
                    break
            got = self._reachable_at(seed, R, float(xs[i]), float(ys[j]), z)
            if got is None:
                continue
            sols[(i, j)] = got
            queue.extend(((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)))
        if len(sols) < 4:
            raise SkillError(FUORI_LIMITI, "area_troppo_piccola")
        return xs, ys, sols

    def table_reach(self, q: Sequence[float], rot: np.ndarray, z: float) -> list[tuple[float, float]]:
        """Table XY the arm can touch, in metres. This is the working area for every cup."""
        xs, ys, sols = self._reach_cells(q, rot, z)
        return [(float(xs[i]), float(ys[j])) for i, j in sorted(sols)]

    def table_corners(self, q: Sequence[float]) -> list[dict[str, Any]]:
        """Parked. Largest rectangle inside ``table_reach`` at the current tip height."""
        T = self.kin.fk(q)
        xs, ys, sols = self._reach_cells(q)
        z = float(T[2, 3])
        tip = T[:3, 3]
        R = np.array(T[:3, :3], dtype=float)
        mask = np.zeros((len(xs), len(ys)), dtype=bool)
        for i, j in sols:
            mask[i, j] = True
        _area, r0, c0, r1, c1 = _largest_true_rect(mask)
        # Smaller X is "avanti", smaller Y is "sinistra" (relative.frame).
        picked = ((r0, c0, "avanti_sinistra"), (r0, c1, "avanti_destra"),
                  (r1, c1, "indietro_destra"), (r1, c0, "indietro_sinistra"))
        rot = np.round(R, 6).tolist()
        out = []
        for n, (i, j, name) in enumerate(picked):
            x, y = float(xs[i]), float(ys[j])
            out.append({"i": n, "name": name, "dx_mm": round((x - float(tip[0])) * 1000, 1),
                        "dy_mm": round((y - float(tip[1])) * 1000, 1), "xyz": (x, y, z), "rot": rot})
        return out

    def goto_table_point(self, q: Sequence[float], xyz: Sequence[float], rot: np.ndarray) -> Plan:
        """Lift, slide to the corner, lower to the recorded table height. Orientation stays as it was."""
        T = self.kin.fk(q)
        here = T[:3, 3]
        target = np.array([float(xyz[0]), float(xyz[1]), float(xyz[2])], dtype=float)
        dist = float(np.hypot(target[0] - here[0], target[1] - here[1]))
        limit = float(self._calib().get("max_m", 0.9))
        if dist > limit:
            raise SkillError(FUORI_LIMITI, "angolo_lontano")
        lift = float(self._calib().get("lift_m", 0.02))
        z_up = max(float(here[2]), float(target[2])) + lift
        R = np.array(rot, dtype=float)

        def pose(x: float, y: float, z: float) -> np.ndarray:
            M = np.eye(4)
            M[:3, :3] = R
            M[:3, 3] = [x, y, z]
            return M

        change = float(self.tch.get("max_joint_change_rad", 1.2))
        floor = min(float(here[2]), float(target[2]))
        q_up = self._ik(pose(float(here[0]), float(here[1]), z_up), q, change)
        self._check_path(q, q_up, floor, tip_clearance=0.0)
        steps_xy = max(1, int(math.ceil(dist / 0.08)))
        q_cur = q_up
        overs = []
        for k in range(1, steps_xy + 1):
            x = float(here[0]) + (float(target[0]) - float(here[0])) * k / steps_xy
            y = float(here[1]) + (float(target[1]) - float(here[1])) * k / steps_xy
            q_next = self._ik(pose(x, y, z_up), q_cur, change)
            self._check_path(q_cur, q_next, floor, tip_clearance=lift * 0.5)
            overs.append(q_next)
            q_cur = q_next
        q_down = self._ik(pose(float(target[0]), float(target[1]), float(target[2])), q_cur, change)
        self._check_path(q_cur, q_down, floor, tip_clearance=0.0)
        m = self._speeds()
        slow = min(m["linear_speed"], 0.05)
        r = lambda v: tuple(round(float(x), 6) for x in v)  # noqa: E731
        motion = [MotionStep("movel", None, r(q_up), slow, m["linear_accel"])]
        motion += [MotionStep("movel", None, r(q_i), slow, m["linear_accel"]) for q_i in overs]
        motion.append(MotionStep("movel", None, r(q_down), slow, m["linear_accel"]))
        return Plan(motion, timeout_s=(dist + 2 * lift) / max(slow, 1e-3) * 3 + 10,
                    info={"dx_mm": round((target[0] - here[0]) * 1000, 1),
                          "dy_mm": round((target[1] - here[1]) * 1000, 1),
                          "lift_mm": round(lift * 1000, 1)})

    def _flange_pose(self, q: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
        T = self.kin.fk(q)
        R = np.array(T[:3, :3], dtype=float)
        return T[:3, 3] - R @ self.kin.tcp, R

    def _tool_down(self, R: np.ndarray) -> np.ndarray:
        """Tool Z straight down, keeping the current tool heading in the horizontal plane."""
        z = np.array([0.0, 0.0, -1.0])
        x = np.array(R[:3, 0], dtype=float)
        x[2] = 0.0
        if float(np.linalg.norm(x)) < 0.25:
            y = np.array(R[:3, 1], dtype=float)
            y[2] = 0.0
            x = np.cross(y, z) if float(np.linalg.norm(y)) >= 0.25 else np.array([1.0, 0.0, 0.0])
        x = x / np.linalg.norm(x)
        y = np.cross(z, x)
        y = y / np.linalg.norm(y)
        x = np.cross(y, z)
        return np.column_stack((x, y, z))

    def _pose_at_flange(self, R: np.ndarray, flange: np.ndarray) -> np.ndarray:
        M = np.eye(4)
        M[:3, :3] = R
        M[:3, 3] = np.asarray(flange, dtype=float) + R @ self.kin.tcp
        return M

    def _slerp(self, R0: np.ndarray, R1: np.ndarray, t: float) -> np.ndarray:
        w = rotvec(R1 @ R0.T)
        th = float(np.linalg.norm(w))
        if th < 1e-8:
            return np.array(R1, dtype=float)
        axis = w / th
        a = th * float(t)
        K = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
        return (np.eye(3) + math.sin(a) * K + (1.0 - math.cos(a)) * (K @ K)) @ R0

    def _check_body(self, q: Sequence[float], z_floor: float) -> None:
        """Link and flange clearance. The 15 cm tool length is virtual, so it is not treated as a rigid needle."""
        lim = 2 * math.pi - 0.05
        if any(abs(float(x)) > lim for x in q):
            raise SkillError(FUORI_LIMITI, "Un giunto supererebbe il suo limite di rotazione.")
        for frame in self.kin.frames(q)[2:]:
            if frame[2, 3] < 0.04:
                raise SkillError(FUORI_LIMITI, "Una parte del braccio passerebbe troppo vicina al tavolo.")
        flange, _ = self._flange_pose(q)
        if float(flange[2]) < z_floor - 1e-4:
            raise SkillError(FUORI_LIMITI, "La traiettoria scenderebbe verso il tavolo.")
        radius = math.hypot(float(flange[0]), float(flange[1]))
        if radius > float(self.rel.get("reach_max_m", 0.5)):
            raise SkillError(FUORI_LIMITI, f"Troppo lontano dalla base ({radius * 1000:.0f} mm).")
        if radius < float(self.rel.get("reach_min_m", 0.12)):
            raise SkillError(FUORI_LIMITI, "Troppo vicino all'asse della base.")

    def _flange_segment(self, q: Sequence[float], R0: np.ndarray, f0: np.ndarray, R1: np.ndarray, f1: np.ndarray,
                        limit: float, z_floor: float, depth: int = 0) -> list[np.ndarray]:
        """Joint steps from one flange pose to the next. Splits until every step stays within the joint limit."""
        try:
            q1 = self.kin.ik(self._pose_at_flange(R1, f1), q)
        except IKError:
            q1 = None
        if q1 is not None and float(np.max(np.abs(q1 - np.asarray(q, dtype=float)))) <= limit + 1e-6:
            a = np.asarray(q, dtype=float)
            for i in range(1, 5):
                self._check_body(a + (q1 - a) * i / 4.0, z_floor)
            return [q1]
        if depth >= 6:
            raise SkillError(FUORI_LIMITI, "Servirebbe una grande rotazione dei giunti: movimento rifiutato.")
        mid_R = self._slerp(R0, R1, 0.5)
        mid_f = (np.asarray(f0, dtype=float) + np.asarray(f1, dtype=float)) / 2.0
        left = self._flange_segment(q, R0, f0, mid_R, mid_f, limit, z_floor, depth + 1)
        right = self._flange_segment(left[-1], mid_R, mid_f, R1, f1, limit, z_floor, depth + 1)
        return left + right

    def point_above(self, q: Sequence[float], xy: tuple[float, float], flange_z: float = 0.16, *,
                    descend: bool = True, lower_to: float | None = None) -> Plan:
        """From the current pose: raise the flange, turn the tool straight down, arc across, stop above xy.

        The flange never goes lower than where it already is, and the arc stays at the higher of that height
        and ``flange_z``. Turning the wrist in joint space (movej) is what leaves a sideways tool; a Cartesian
        move through that wrist pose is not used. The arm still refuses a step that would hit the table or
        need a large joint jump.
        """
        q = np.array(q, dtype=float)
        limit = float(self.rel.get("max_joint_change_rad", 0.8))
        flange, R = self._flange_pose(q)
        R_start = np.array(R, dtype=float)
        hold = max(float(flange_z), float(flange[2]))
        z_floor = float(flange[2]) - 0.01
        level: list[np.ndarray] = []
        target = np.array(flange, dtype=float)
        target[2] = hold
        down = self._tool_down(R)
        tilt = float(np.degrees(np.arccos(np.clip(-float(R[2, 2]), -1.0, 1.0))))
        if tilt > 20.0 or float(flange[2]) < float(flange_z) - 0.005:
            if abs(float(target[2]) - float(flange[2])) > 0.005:
                level.extend(self._flange_segment(q, R, flange, R, target, limit, z_floor))
                q = level[-1]
                flange, R = self._flange_pose(q)
            if float(np.linalg.norm(rotvec(down @ R.T))) > math.radians(8):
                level.extend(self._flange_segment(q, R, flange, down, target, limit, z_floor))
                q = level[-1]
                flange, R = self._flange_pose(q)
        tilt = float(np.degrees(np.arccos(np.clip(-float(R[2, 2]), -1.0, 1.0))))
        if tilt > 20.0:
            raise SkillError(FUORI_LIMITI, "La punta non è verticale: non parto da questa postura.")
        hover_z = hold + float(R[2, 2]) * float(self.kin.tcp[2])
        start = self.kin.tip(q).copy()
        a0, r0 = math.atan2(float(start[1]), float(start[0])), math.hypot(float(start[0]), float(start[1]))
        a1, r1 = math.atan2(float(xy[1]), float(xy[0])), math.hypot(float(xy[0]), float(xy[1]))
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        n_arc = max(1, int(math.ceil(abs(da) / 0.35)))
        wps = []
        if abs(hover_z - float(start[2])) > 0.003:
            lifted = start.copy()
            lifted[2] = hover_z
            wps.append(lifted)
        for i in range(1, n_arc + 1):
            t = i / n_arc
            a, rad = a0 + da * t, r0 + (r1 - r0) * t
            wps.append(np.array([rad * math.cos(a), rad * math.sin(a), hover_z]))
        joints = []
        q_cur = q
        for xyz in wps:
            M = np.eye(4)
            M[:3, :3] = R
            M[:3, 3] = xyz
            q_next = self._ik(M, q_cur, limit)
            self._check(q_next, 0.0, tip_clearance=0.0)
            for frame in self.kin.frames(q_next)[2:]:
                if frame[2, 3] < 0.04:
                    raise SkillError(FUORI_LIMITI, "Una parte del braccio passerebbe troppo vicina al tavolo.")
            got, _ = self._flange_pose(q_next)
            if float(got[2]) < hold - 0.02:
                raise SkillError(FUORI_LIMITI, "La traiettoria scenderebbe verso il tavolo.")
            joints.append(q_next)
            q_cur = q_next
        # The 15 cm tool length is virtual. A cup point lowers the real flange to just above the rim.
        # descend=False stays at the hover (point at a zone). lower_to is a flange height in metres.
        rim = float(lower_to) if lower_to is not None else float(self.tch.get("object_height_m", 0.09)) + 0.015
        here, R_here = self._flange_pose(q_cur)
        drop_steps: list[np.ndarray] = []
        if descend and float(here[2]) > rim + 0.01:
            low = np.array(here, dtype=float)
            low[2] = rim
            drop_steps = self._flange_segment(q_cur, R_here, here, R_here, low, limit, rim - 0.005)
        m = self._speeds()
        slow = min(m["linear_speed"], 0.05)
        drop = min(slow, float(self.tch.get("descent_speed", 0.03)))
        r = lambda v: tuple(round(float(x), 6) for x in v)  # noqa: E731
        motion = [MotionStep("movej", None, r(qq), m["joint_speed"], m["joint_accel"]) for qq in level]
        motion += [MotionStep("movel", None, r(qq), slow, m["linear_accel"]) for qq in joints]
        motion += [MotionStep("movel", None, r(qq), drop, m["linear_accel"]) for qq in drop_steps]
        length = abs(hold - z_floor) + abs(da) * max(r0, r1, 0.05)
        level_deg = math.degrees(float(np.linalg.norm(rotvec(R @ R_start.T))))
        return Plan(motion, timeout_s=length / max(slow, 1e-3) * 3 + 10 + len(level) * 2,
                    info={"xy_mm": [round(float(xy[0]) * 1000, 1), round(float(xy[1]) * 1000, 1)],
                          "flange_z_mm": round(hold * 1000, 1),
                          "rim_z_mm": round(rim * 1000, 1),
                          "turn_deg": round(math.degrees(da), 1),
                          "level_deg": round(level_deg, 1)})

    def zone_xy(self, zone: str) -> tuple[float, float]:
        """Centre of a left/centre/right strip on the taught tape rectangle. Robot left is negative Y."""
        from ..vision.plane import TAUGHT_TAPE_M
        xs = [p[0] for p in TAUGHT_TAPE_M.values()]
        ys = [p[1] for p in TAUGHT_TAPE_M.values()]
        x = (min(xs) + max(xs)) / 2.0
        y0, y1 = min(ys), max(ys)
        t = {"sinistra": 1.0 / 6.0, "centro": 0.5, "destra": 5.0 / 6.0}[zone]
        return (x, y0 + (y1 - y0) * t)

    def command_plan(self, q: Sequence[float], skill: str, params: dict[str, str]) -> Plan | None:
        """Motion for a named command, computed from the current joints. None keeps the configured joint pose.

        Home and the waiting pose stay on their joint list. Zone commands (point, lift, place, push,
        go above a zone) are planned over the taught table, so they do not need a recorded pose.
        """
        if skill == "vai_a_posa":
            pose = str(params.get("posa") or "")
            if pose.endswith("_sopra"):
                zone = pose[: -len("_sopra")]
                if zone in ("sinistra", "centro", "destra"):
                    return self.point_above(q, self.zone_xy(zone), descend=False)
            return None
        zone = params.get("posizione")
        if zone not in ("sinistra", "centro", "destra"):
            return None
        xy = self.zone_xy(zone)
        if skill == "indica":
            return self.point_above(q, xy, descend=False)
        if skill in ("solleva_vassoio", "porta_in_ispezione"):
            hover = self.point_above(q, xy, descend=False)
            q2 = hover.motion[-1].joints
            if q2 is None:
                return hover
            up = self.translate(q2, "su", 15.0)
            return self._join(hover, up)
        if skill in ("appoggia_vassoio", "smista"):
            return self.point_above(q, xy, lower_to=0.12)
        if skill == "spingi":
            direction = params.get("direzione")
            if direction not in ("sinistra", "destra"):
                return None
            hover = self.point_above(q, xy, descend=False)
            q2 = hover.motion[-1].joints
            if q2 is None:
                return hover
            from ..vision.plane import TAUGHT_TAPE_M
            ys = [p[1] for p in TAUGHT_TAPE_M.values()]
            slide_cm = (max(ys) - min(ys)) / 3.0 * 100.0
            slide = self.translate(q2, direction, slide_cm)
            return self._join(hover, slide)
        return None

    @staticmethod
    def _join(a: Plan, b: Plan) -> Plan:
        return Plan(list(a.motion) + list(b.motion), timeout_s=a.timeout_s + b.timeout_s, info={**a.info, **b.info})

    def touch(self, q: Sequence[float], xy: tuple[float, float], cal: Any, height_m: float | None = None) -> Plan:
        """Above the point, slow straight descent to the object's top, back up. Tool orientation = calibration's."""
        if cal is None or not cal.ready:
            raise SkillError(NON_CALIBRATO, "Telecamera non calibrata: prima registra almeno 4 punti di calibrazione.")
        if self.s.mode == "real" and cal.mode != "real":
            raise SkillError(NON_CALIBRATO, "La calibrazione non è stata registrata sul robot reale.")
        margin = float(self.tch.get("workspace_margin_mm", 10))
        if getattr(cal, "reachable", None):
            if not cal.covers(xy):
                raise SkillError(FUORI_LIMITI, "fuori_dal_tavolo")
        elif not cal.inside(xy, margin):
            raise SkillError(FUORI_LIMITI, "L'oggetto è fuori dall'area calibrata: avvicinalo o aggiungi punti di "
                                           "calibrazione lì.")
        tz = float(cal.table_z)
        object_h = float(self.tch.get("object_height_m", 0.09) if height_m is None else height_m)
        top = tz + object_h + float(self.tch.get("touch_offset_m", 0.005))
        above = top + float(self.tch.get("approach_above_m", 0.08))
        R = cal.rot
        T_touch, T_above = np.eye(4), np.eye(4)
        T_touch[:3, :3] = T_above[:3, :3] = R
        T_touch[:3, 3] = [xy[0], xy[1], top]
        T_above[:3, 3] = [xy[0], xy[1], above]
        qn = cal.nearest_joints(xy)
        q_above = self._ik(T_above, qn, float(self.tch.get("max_joint_change_rad", 1.2)))
        q_touch = self._ik(T_touch, q_above, 0.6)
        tip_floor = min(top - tz, float(self.rel.get("tip_clearance_m", 0.02)))
        self._check(q_touch, tz, tip_clearance=tip_floor)
        self._check(q_above, tz, tip_clearance=tip_floor)
        start_clear = self.kin.tip(q)[2] - tz
        self._check_path(q, q_above, tz, tip_clearance=min(start_clear, above - tz - 0.01, 0.03))
        m = self._speeds()
        slow = min(m["linear_speed"], float(self.tch.get("descent_speed", 0.03)))
        r = lambda v: tuple(round(float(x), 6) for x in v)  # noqa: E731
        motion = [MotionStep("movej", None, r(q_above), m["joint_speed"], m["joint_accel"]),
                  MotionStep("movel", None, r(q_touch), slow, m["linear_accel"]),
                  MotionStep("wait", seconds=float(self.tch.get("hold_s", 0.5))),
                  MotionStep("movel", None, r(q_above), slow, m["linear_accel"])]
        travel = float(np.max(np.abs(np.array(q_above) - np.array(q)))) / max(m["joint_speed"], 1e-3)
        dur = travel + 2 * (above - top) / slow + 1
        return Plan(motion, timeout_s=dur * 2 + 10,
                    info={"xy_mm": [round(xy[0] * 1000, 1), round(xy[1] * 1000, 1)],
                          "altezza_tocco_mm": round((top - tz) * 1000, 1)})
