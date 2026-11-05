"""All inverse-kinematics branches of a UR arm (up to 8: shoulder left/right x elbow up/down x wrist flip).

Closed form after Hawkins, "Analytic Inverse Kinematics for the Universal Robots UR-5/UR-10 Arms" (2013),
on the same nominal DH table as ``kinematics.Kinematics``. Every candidate is verified with forward
kinematics and polished with a few damped-least-squares steps, so a sign slip in one branch can only lose
that branch, never return a wrong pose.

The planner uses the branches to pick the one that is cheapest and safest to reach, instead of the single
solution a numeric IK happens to converge to.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from .kinematics import IKError, Kinematics, rotvec

TWO_PI = 2 * math.pi


def _A(kin: Kinematics, i: int, q: float) -> np.ndarray:
    ct, st = math.cos(q), math.sin(q)
    ca, sa = math.cos(kin.alpha[i]), math.sin(kin.alpha[i])
    return np.array([[ct, -st * ca, st * sa, kin.a[i] * ct],
                     [st, ct * ca, -ct * sa, kin.a[i] * st],
                     [0, sa, ca, kin.d[i]],
                     [0, 0, 0, 1.0]])


def _pose_err(kin: Kinematics, q: Sequence[float], target: np.ndarray) -> float:
    T = kin.fk(q)
    return float(np.linalg.norm(T[:3, 3] - target[:3, 3]) + 0.05 * np.linalg.norm(rotvec(target[:3, :3] @ T[:3, :3].T)))


def wrap_near(q: Sequence[float], ref: Sequence[float], limit: float = TWO_PI - 0.05) -> np.ndarray:
    """Each angle shifted by k*2pi to be closest to ``ref`` while staying within +-limit."""
    out = np.array(q, dtype=float)
    for i, (a, r) in enumerate(zip(out, ref)):
        best = None
        for k in (-2, -1, 0, 1, 2):
            v = a + k * TWO_PI
            if abs(v) <= limit and (best is None or abs(v - r) < abs(best - r)):
                best = v
        out[i] = a if best is None else best
    return out


def ik_all(kin: Kinematics, target_tip: np.ndarray, q_ref: Sequence[float] | None = None,
           tol: float = 1e-4) -> list[np.ndarray]:
    """Every joint solution placing the tool tip at ``target_tip`` (4x4), wrapped near ``q_ref``."""
    q_ref = np.zeros(6) if q_ref is None else np.asarray(q_ref, dtype=float)
    d1, d4, d5, d6 = kin.d[0], kin.d[3], kin.d[4], kin.d[5]
    a2, a3 = kin.a[1], kin.a[2]
    tcp = np.eye(4)
    tcp[:3, 3] = kin.tcp
    T = target_tip @ np.linalg.inv(tcp)            # flange pose
    R, p = T[:3, :3], T[:3, 3]
    p05 = T @ np.array([0, 0, -d6, 1.0])
    r = math.hypot(p05[0], p05[1])
    if r < abs(d4) + 1e-9:
        return []                                  # wrist centre inside the shoulder cylinder
    psi = math.atan2(p05[1], p05[0])
    phi = math.acos(np.clip(d4 / r, -1, 1))
    sols: list[np.ndarray] = []
    for q1 in (psi + phi + math.pi / 2, psi - phi + math.pi / 2):
        s1, c1 = math.sin(q1), math.cos(q1)
        c5 = (p[0] * s1 - p[1] * c1 - d4) / d6
        if abs(c5) > 1 + 1e-9:
            continue
        for q5 in (math.acos(np.clip(c5, -1, 1)), -math.acos(np.clip(c5, -1, 1))):
            s5 = math.sin(q5)
            if abs(s5) < 1e-6:
                q6 = float(q_ref[5])               # wrist singular: q6 is free, keep the current one
            else:
                q6 = math.atan2((-R[0, 1] * s1 + R[1, 1] * c1) / s5, (R[0, 0] * s1 - R[1, 0] * c1) / s5)
            T14 = np.linalg.inv(_A(kin, 0, q1)) @ T @ np.linalg.inv(_A(kin, 5, q6)) @ np.linalg.inv(_A(kin, 4, q5))
            px, pz = T14[0, 3], T14[1, 3]            # planar 2-link problem in frame 1 (its z is the shoulder axis)
            L = math.hypot(px, pz)
            c3 = (L * L - a2 * a2 - a3 * a3) / (2 * a2 * a3)
            if abs(c3) > 1 + 1e-9:
                continue
            for q3 in (math.acos(np.clip(c3, -1, 1)), -math.acos(np.clip(c3, -1, 1))):
                q2 = math.atan2(pz, px) - math.atan2(a3 * math.sin(q3), a2 + a3 * math.cos(q3))
                T13 = _A(kin, 1, q2) @ _A(kin, 2, q3)
                T34 = np.linalg.inv(T13) @ T14
                q4 = math.atan2(T34[1, 0], T34[0, 0])
                q = np.array([q1, q2, q3, q4, q5, q6])
                if _pose_err(kin, q, target_tip) > tol:
                    try:
                        q = kin.ik(target_tip, q, iters=30)
                    except IKError:
                        continue
                    if _pose_err(kin, q, target_tip) > tol:
                        continue
                q = wrap_near(np.arctan2(np.sin(q), np.cos(q)), q_ref)
                if not any(np.allclose(np.cos(q - s), 1, atol=1e-6) for s in sols):
                    sols.append(q)
    return sols
