"""Camera-to-table calibration recorded with the robot itself.

The operator jogs the tool tip (teach pendant, tool kept vertical) onto a mark on the table, clicks the
same mark in the camera image, and presses "record point": the tip position comes from the robot's
measured joints (forward kinematics), never from typing or from the conversation. Four or more spread-out
points give a pixel -> table XY homography (`grokarm_vision.calibration`), the table height and the
tool orientation that touches will reuse.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from grokarm_vision.calibration import CalibrationError, Calibration, CalPoint

MAX_TILT_DEG = 20.0          # tool axis vs straight down
MAX_ORIENT_SPREAD_DEG = 5.0  # all points with the same tool orientation (pendant: translate only)
MAX_Z_SPREAD_M = 0.015       # the table is flat: tips should all be at the same height


@dataclass
class TablePoint:
    px: tuple[float, float]
    tip: tuple[float, float, float]     # m, robot base frame
    joints: tuple[float, ...]
    rot: list[list[float]]

    def to_dict(self) -> dict[str, Any]:
        return {"px": list(self.px), "tip": list(self.tip), "joints": list(self.joints), "rot": self.rot}


@dataclass
class TableCalibration:
    path: Path
    points: list[TablePoint] = field(default_factory=list)
    frame_size: tuple[int, int] | None = None
    mode: str | None = None
    fit: Calibration | None = None
    error: str | None = None
    created_at: float | None = None
    reachable: list[tuple[float, float]] = field(default_factory=list)  # table XY the arm itself can touch, m
    reach_step: float = 0.04

    # ------------------------------------------------------------ queries ---
    @property
    def ready(self) -> bool:
        return self.fit is not None

    @property
    def table_z(self) -> float | None:
        return float(np.mean([p.tip[2] for p in self.points])) if self.points else None

    @property
    def rot(self) -> np.ndarray | None:
        return np.array(self.points[0].rot) if self.points else None

    def to_xy(self, u: float, v: float) -> tuple[float, float]:
        """Image pixel -> table point in metres (base frame)."""
        x, y = self.fit.to_mm(u, v)
        return x / 1000.0, y / 1000.0

    def inside(self, xy_m: tuple[float, float], margin_mm: float) -> bool:
        return self.fit.inside_workspace((xy_m[0] * 1000, xy_m[1] * 1000), margin_mm)

    def covers(self, xy_m: tuple[float, float]) -> bool:
        """True when this table point is one the arm can touch. A cup anywhere in that set is allowed."""
        if not self.reachable:
            return False
        pts = np.asarray(self.reachable, dtype=float)
        dist = np.hypot(pts[:, 0] - float(xy_m[0]), pts[:, 1] - float(xy_m[1]))
        return float(dist.min()) <= self.reach_step * 0.75

    def nearest_joints(self, xy_m: tuple[float, float]) -> tuple[float, ...]:
        return min(self.points, key=lambda p: (p.tip[0] - xy_m[0]) ** 2 + (p.tip[1] - xy_m[1]) ** 2).joints

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"points": [{"px": [round(c) for c in p.px], "tip_mm": [round(c * 1000, 1) for c in p.tip]}
                                          for p in self.points],
                               "ready": self.ready, "frame_size": list(self.frame_size) if self.frame_size else None,
                               "mode": self.mode, "error": self.error, "min_points": 4,
                               "table_z_mm": None if self.table_z is None else round(self.table_z * 1000, 1)}
        if self.fit:
            s = self.fit.summary()
            out.update(fit_rms_mm=s["fit_rms_mm"], loo_max_mm=s["loo_max_mm"])
        if self.reachable:
            xs = [p[0] for p in self.reachable]
            ys = [p[1] for p in self.reachable]
            out["table_mm"] = {"width": round((max(xs) - min(xs)) * 1000),
                               "depth": round((max(ys) - min(ys)) * 1000)}
        return out

    # ------------------------------------------------------------ editing ---
    def add(self, px: tuple[float, float], frame_size: tuple[int, int], T: np.ndarray, joints: list[float],
            mode: str) -> None:
        """T = base->tip transform measured on the robot now."""
        R = T[:3, :3]
        tilt = float(np.degrees(np.arccos(np.clip(-R[2, 2], -1, 1))))
        if tilt > MAX_TILT_DEG:
            raise CalibrationError(f"la punta non è verticale (inclinata di {tilt:.0f}°): tienila rivolta in giù")
        if self.points:
            if self.frame_size and tuple(frame_size) != tuple(self.frame_size):
                raise CalibrationError("la risoluzione della telecamera è cambiata: ricomincia la calibrazione")
            if self.mode and mode != self.mode:
                raise CalibrationError(f"calibrazione iniziata in modalità {self.mode}: ricomincia")
            from ..robot.kinematics import angle_between
            d = np.degrees(angle_between(R, self.rot))
            if d > MAX_ORIENT_SPREAD_DEG:
                raise CalibrationError(f"orientamento della punta diverso dal primo punto ({d:.0f}°): sul pendant "
                                       "muovi solo in traslazione")
            if abs(T[2, 3] - self.table_z) > MAX_Z_SPREAD_M:
                raise CalibrationError(f"altezza diversa dagli altri punti di {abs(T[2, 3] - self.table_z) * 1000:.0f} mm:"
                                       " la punta deve toccare il tavolo")
        self.points.append(TablePoint((float(px[0]), float(px[1])), tuple(float(c) for c in T[:3, 3]),
                                      tuple(float(q) for q in joints), R.tolist()))
        self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        self.mode = mode
        self.reachable = []
        self._refit()
        self.save()

    def set_reach(self, cells: list[tuple[float, float]], step: float = 0.04) -> None:
        """Store the arm's own reachable table. This is not derived from a cup."""
        self.reachable = [(float(x), float(y)) for x, y in cells]
        self.reach_step = float(step)
        self.save()

    def undo(self) -> None:
        if self.points:
            self.points.pop()
        self.reachable = []
        self._refit()
        self.save()

    def replace_plane(self, pairs: list[tuple[tuple[float, float], tuple[float, float]]], z: float,
                      rot: list[list[float]], joints: tuple[float, ...], frame_size: tuple[int, int],
                      mode: str) -> None:
        """Install a whole plane at once. Each pair is one pixel and its own base XY.

        ``add`` cannot do this: it stores the tip that is touching the table right now, and a
        marker corner is not under the tip.
        """
        R = np.array(rot, dtype=float).tolist()
        q = tuple(float(v) for v in joints)
        self.points = [TablePoint((float(px[0]), float(px[1])), (float(xy[0]), float(xy[1]), float(z)), q, R)
                       for px, xy in pairs]
        self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        self.mode = mode
        self.reachable = []
        self._refit()
        self.save()

    def clear(self) -> None:
        self.points, self.frame_size, self.mode, self.fit, self.error = [], None, None, None, None
        self.reachable = []
        self.save()

    def _refit(self) -> None:
        self.fit, self.error = None, None
        if len(self.points) < 4:
            return
        try:
            self.fit = Calibration.fit([CalPoint(p.px, (p.tip[0] * 1000, p.tip[1] * 1000)) for p in self.points],
                                       self.frame_size)
            self.created_at = time.time()
        except CalibrationError as e:
            self.error = str(e)

    # -------------------------------------------------------------- files ---
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"points": [p.to_dict() for p in self.points], "mode": self.mode,
                                         "frame_size": self.frame_size, "created_at": self.created_at,
                                         "reachable": self.reachable, "reach_step": self.reach_step}, indent=1),
                             encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "TableCalibration":
        cal = cls(path)
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                cal.points = [TablePoint(tuple(p["px"]), tuple(p["tip"]), tuple(p["joints"]), p["rot"])
                              for p in d.get("points", [])]
                cal.mode = d.get("mode")
                cal.frame_size = tuple(d["frame_size"]) if d.get("frame_size") else None
                cal.created_at = d.get("created_at")
                cal.reachable = [tuple(p) for p in d.get("reachable") or []]
                cal.reach_step = float(d.get("reach_step") or 0.04)
                cal._refit()
            except (OSError, ValueError, KeyError, TypeError) as e:
                cal.error = f"file di calibrazione illeggibile: {e}"
        return cal
