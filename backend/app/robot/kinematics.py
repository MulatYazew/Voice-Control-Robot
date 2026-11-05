"""UR e-Series forward / inverse kinematics on the nominal DH parameters published by Universal Robots.

Used only to turn a *checked* tool-tip target into joint targets; the robot still receives plain joint
vectors (movej / movel over joints) and completion is still verified on actual_q.

The nominal model differs from each robot's factory calibration by a few millimetres. Camera calibration
reads positions through this same model, so that error largely cancels inside the calibrated area.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

DH = {   # d, a, alpha  (m, m, rad)
    "UR3e": ([0.15185, 0, 0, 0.13105, 0.08535, 0.0921], [0, -0.24355, -0.2132, 0, 0, 0],
             [math.pi / 2, 0, 0, math.pi / 2, -math.pi / 2, 0]),
    "UR5e": ([0.1625, 0, 0, 0.1333, 0.0997, 0.0996], [0, -0.425, -0.3922, 0, 0, 0],
             [math.pi / 2, 0, 0, math.pi / 2, -math.pi / 2, 0]),
}


class IKError(ValueError):
    pass


def model_of(name: str) -> str:
    n = name.replace(" ", "").replace("(e-Series)", "e").replace("-", "").lower()
    return "UR5e" if n.startswith("ur5") else "UR3e"


class Kinematics:
    def __init__(self, model: str = "UR3e", tcp: Sequence[float] = (0.0, 0.0, 0.0)) -> None:
        self.d, self.a, self.alpha = (np.array(v, dtype=float) for v in DH[model])
        self.tcp = np.array(tcp, dtype=float)

    def frames(self, q: Sequence[float]) -> list[np.ndarray]:
        """Base->joint-i transforms (i = 0..6); the last one is the flange."""
        T = np.eye(4)
        out = [T]
        for i in range(6):
            ct, st = math.cos(q[i]), math.sin(q[i])
            ca, sa = math.cos(self.alpha[i]), math.sin(self.alpha[i])
            A = np.array([[ct, -st * ca, st * sa, self.a[i] * ct],
                          [st, ct * ca, -ct * sa, self.a[i] * st],
                          [0, sa, ca, self.d[i]],
                          [0, 0, 0, 1]])
            T = T @ A
            out.append(T)
        return out

    def fk(self, q: Sequence[float]) -> np.ndarray:
        """Base->tool-tip transform."""
        T = self.frames(q)[-1].copy()
        T[:3, 3] = T[:3, 3] + T[:3, :3] @ self.tcp
        return T

    def tip(self, q: Sequence[float]) -> np.ndarray:
        return self.fk(q)[:3, 3]

    def ik(self, target: np.ndarray, qnear: Sequence[float], *, iters: int = 400) -> np.ndarray:
        """Joint vector reaching `target` (4x4 tool-tip pose), found by damped least squares started at qnear,
        so the solution stays on qnear's branch (no wrist / elbow flips)."""
        q = np.array(qnear, dtype=float)
        lam = 0.02
        for _ in range(iters):
            fr = self.frames(q)
            T = fr[-1].copy()
            T[:3, 3] = T[:3, 3] + T[:3, :3] @ self.tcp
            ep = target[:3, 3] - T[:3, 3]
            eo = rotvec(target[:3, :3] @ T[:3, :3].T)
            if np.linalg.norm(ep) < 1e-5 and np.linalg.norm(eo) < 1e-4:
                return q
            J = np.zeros((6, 6))
            for i in range(6):
                z, p = fr[i][:3, 2], fr[i][:3, 3]
                J[:3, i] = np.cross(z, T[:3, 3] - p)
                J[3:, i] = z
            e = np.concatenate([ep, eo])
            dq = J.T @ np.linalg.solve(J @ J.T + lam ** 2 * np.eye(6), e)
            n = np.linalg.norm(dq)
            if n > 0.2:
                dq *= 0.2 / n
            q = q + dq
        raise IKError("posizione non raggiungibile da qui")


def rotvec(R: np.ndarray) -> np.ndarray:
    c = max(-1.0, min(1.0, (np.trace(R) - 1) / 2))
    ang = math.acos(c)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if ang < 1e-9:
        return v / 2
    if math.pi - ang < 1e-6:   # 180°: axis from the symmetric part
        axis = np.sqrt(np.maximum((np.diag(R) + 1) / 2, 0))
        return axis * ang
    return v * ang / (2 * math.sin(ang))


def angle_between(Ra: np.ndarray, Rb: np.ndarray) -> float:
    return float(np.linalg.norm(rotvec(Ra @ Rb.T)))
