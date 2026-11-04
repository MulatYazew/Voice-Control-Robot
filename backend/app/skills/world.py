"""World model. Robot state comes from feedback (verified); object state is ASSUMED.

There is no camera and no tray sensor: `trays_at` and `holding_tray` are the
software's bookkeeping of what *should* be true if every configured motion
sequence did what it was designed to do. Never present them as sensed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import math


@dataclass
class WorldModel:
    zones: list[str]
    trays_at: set[str] = field(default_factory=set)       # ASSUMED
    holding_tray: bool = False                            # ASSUMED (tray on the fork)
    known_pose: str | None = None                         # VERIFIED from joints when feedback allows
    joints: list[float] | None = None                     # VERIFIED (adapter feedback)
    active_skill: str | None = None
    fault: str | None = None

    @classmethod
    def initial(cls, zones: list[str], trays_at: list[str]) -> "WorldModel":
        return cls(zones=list(zones), trays_at=set(trays_at))

    def apply_effects(self, skill: str, params: dict[str, str]) -> None:
        """Bookkeeping after a motion sequence completed successfully."""
        if skill == "solleva_vassoio":
            self.trays_at.discard(params["posizione"])
            self.holding_tray = True
        elif skill == "appoggia_vassoio":
            self.trays_at.add(params["posizione"])
            self.holding_tray = False
        # vai_a_posa / indica / spingi: no change to the assumed object layout
        # (a push keeps the tray in its zone; direction effect is UNVERIFIED)

    def match_pose(self, joints: list[float] | None, poses: dict[str, Any], tol: float) -> str | None:
        if not joints:
            return None
        for name, p in poses.items():
            if all(abs(a - b) <= tol for a, b in zip(joints, p["joints"])):
                return name
        return None

    def update_joints(self, joints: list[float] | None, poses: dict[str, Any], tol: float) -> None:
        self.joints = [round(j, 4) for j in joints] if joints else None
        self.known_pose = self.match_pose(joints, poses, tol)

    def snapshot(self) -> dict[str, Any]:
        return {
            "robot_verificato": {"posa_nota": self.known_pose, "giunti": self.joints, "skill_attiva": self.active_skill,
                                 "guasto": self.fault},
            "oggetti_presunti": {"vassoi_in_zona": sorted(self.trays_at, key=self.zones.index),
                                 "zone_libere": [z for z in self.zones if z not in self.trays_at],
                                 "vassoio_sulla_forca": self.holding_tray,
                                 "nota": "Posizioni presunte dalla configurazione: nessuna telecamera o sensore."},
        }

    def copy(self) -> "WorldModel":
        return WorldModel(zones=list(self.zones), trays_at=set(self.trays_at), holding_tray=self.holding_tray,
                          known_pose=self.known_pose, joints=list(self.joints) if self.joints else None)


def joints_close(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...], tol: float) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b)) and not any(math.isnan(x) for x in a)
