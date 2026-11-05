"""Motion, vision and inspection methods from grokarm-ur3e, mixed into the local gate.

The local gate keeps labelling, evidence, bilingual reports and the voice-stop policy.
These methods add the cup gripper, the motion guard, Cartesian jogs and QA inspection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..robot.base import result as _exec_result
from ..skills.amounts import amounts_in_order, angle_deg, distance_cm, is_nudge, is_repeat, length_cm
from ..skills.cartesian import DIRECTIONS, JOINTS, Planner, canon_direction, canon_joint, canon_side
from ..skills.motion_guard import GuardRejected, guard_from_settings, hover_and_lower
from ..skills.registry import SkillError, ValidatedAction
from ..vision.cups import ALL_COLOURS, CupFinder, colour_of
from ..vision.inspector import FAIL, PASS, UNSURE, clean_rule
from ..vision.marker import MarkerFitError, detect, pairs_for_plane
from ..vision.plane import Plane
from ..vision.table_calib import CalibrationError, TableCalibration
from ..vision.texts import t as tr
from ..vision.texts import word
from .gate import GateState, PendingAction, _err

log = logging.getLogger("voce.gate")


@dataclass
class InspectionState:
    rule: str = ""
    running: bool = False
    last: dict[str, Any] | None = None
    last_jpeg: bytes | None = None
    counts: dict[str, int] = field(default_factory=lambda: {PASS: 0, FAIL: 0, UNSURE: 0})

    def public(self) -> dict[str, Any]:
        return {"rule": self.rule, "running": self.running, "last": self.last, "counts": dict(self.counts),
                "has_image": self.last_jpeg is not None}


class MotionMixin:
    def attach_vision(self, camera: Any, inspector: Any) -> None:
        self.camera, self.inspector = camera, inspector
        self.cups = CupFinder(self.s.zones, dict(self.s.robot.get("cups") or {}),
                              (self.s.inspection or {}).get("labels"))

    def scan_cups(self) -> dict[str, Any]:
        """Read-only: which coloured cups the camera sees, and in which zone."""
        if self.camera is None:
            return _err("telecamera_non_disponibile", "Telecamera non disponibile.")
        frame = self.camera.fresh(float((self.s.robot.get("cups") or {}).get("max_frame_age_s", 2.0)))
        if frame is None:
            return _err("telecamera_non_disponibile", "Nessuna immagine recente dalla telecamera.")
        res = self.cups.scan(frame)
        layout, cups = self._cup_layout(res.get("cups") or [], frame)
        res = {**res, "cups": cups, "layout": layout}
        self.bus.publish("cups", **{k: v for k, v in res.items() if k != "cups"})
        return {"ok": True, **res}

    def cups_background(self, *, clear: bool = False) -> dict[str, Any]:
        """Photo of the empty table: enables detection of cups of any colour (white, grey, black)."""
        if self.camera is None:
            return _err("telecamera_non_disponibile", "Telecamera non disponibile.")
        if clear:
            self.cups.clear_background()
            return {"ok": True, "detector": self.cups.detector}
        frame = self.camera.fresh(float((self.s.robot.get("cups") or {}).get("max_frame_age_s", 2.0)))
        if frame is None:
            return _err("telecamera_non_disponibile", "Nessuna immagine recente dalla telecamera.")
        return {**self.cups.capture_background(frame), "detector": self.cups.detector}

    async def point_at_cup(self, colour: Any, *, source: str = "grok") -> dict[str, Any]:
        """Find the cup of that colour in the camera image and propose `indica` on its zone (needs confirmation)."""
        b = self._blocked()
        if b:
            return b
        c = str(colour or "").strip().lower()
        c = "grey" if c == "gray" else c
        c = c if c in ALL_COLOURS else colour_of(c)
        if c is None:
            return _err("richiesta_ambigua", "Di che colore è il bicchiere?", param="colore", opzioni_valide=ALL_COLOURS)
        plane = Plane.load(self.s.data_dir / "plane.json")
        if plane is not None:
            return await self._point_on_plane(c, plane, source)
        scan = self.scan_cups()
        if not scan.get("ok"):
            return scan
        seen = scan["cups"]
        detector = scan.get("detector")
        if not any(x["colore"] == c for x in seen) and self.s.xai_api_key and \
                (self.s.robot.get("cups") or {}).get("grok_fallback", True):
            frame = self.camera.store.latest()
            try:
                seen = await self._io(self.cups.grok_lookup, frame, self.s.xai_api_key, self.s.xai_vision_model,
                                      self.s.xai_base_url, timeout=15)
                detector = "grok"
            except Exception as e:
                log_event(log, "grok cup lookup failed", error=str(e))
            b = self._blocked()
            if b:
                return b
        zones = list(dict.fromkeys(x["zona"] for x in seen if x["colore"] == c))
        if not zones:
            return _err("oggetto_non_trovato", f"Non vedo nessun bicchiere {c}.",
                        visti=[f"{x['colore']} ({x['zona']})" for x in seen])
        if len(zones) > 1:
            return _err("richiesta_ambigua", f"Vedo bicchieri {c} in più zone: quale?", param="posizione",
                        opzioni_valide=zones)
        res = self.propose("indica", {"posizione": zones[0]}, source=source)
        if res.get("ok") and self.pending is not None:
            lang = self.s.lang
            self.pending.action.conferma = tr("cup_question", lang, colour=word("colour", c, lang),
                                              zone=word("zone", zones[0], lang))
            self.pending.action.extra["evidenza"] = {"colore": c, "zona": zones[0]}
            res["frase_conferma"] = self.pending.action.conferma
            res["bicchiere"] = {"colore": c, "zona": zones[0], "rilevatore": detector}
            self.publish_state()
        return res

    # ===================================================== computed motions ===
    async def _operator_text(self) -> str:
        u = self.utterance
        if u is None or u.used_for:
            return ""
        if u.text is None:
            try:
                await asyncio.wait_for(u.done.wait(), 2.5)
            except asyncio.TimeoutError:
                return ""
        return u.text or ""

    async def _operator_amount(self, kind: str) -> float | None:
        """The number in the operator's latest (unused) turn: never a value chosen by the model."""
        text = await self._operator_text()
        if not text and (self.utterance is None or self.utterance.used_for):
            return None
        v = distance_cm(text) if kind == "cm" else angle_deg(text)
        if v is None and is_repeat(text):
            v = self._last_amount.get(kind)
        return v

    async def _joints_now(self) -> list[float] | dict[str, Any]:
        st = await self.refresh_robot()
        if not st.connected or not st.joints:
            return _err("robot_non_disponibile", "Posizione attuale del robot non disponibile.")
        if st.moving:
            return _err("robot_in_movimento", "Il robot si sta muovendo: attendi che sia fermo.")
        return list(st.joints)

    def _propose_computed(self, skill: str, params: dict[str, str], plan: Any, q: list[float], conferma: str,
                          risultato: str, source: str, **extra: Any) -> dict[str, Any]:
        act = ValidatedAction(skill=skill, params=params, motion=plan.motion, timeout_s=plan.timeout_s,
                              conferma=conferma, risultato=risultato, strumento="punta",
                              extra={"start_joints": [round(x, 5) for x in q], **plan.info, **extra})
        res = self._set_pending(act, source)
        res["dettagli"] = plan.info
        return res

    async def propose_move(self, direction: Any, *, source: str = "grok") -> dict[str, Any]:
        """'3 cm a sinistra': straight move of the tip; the distance comes from the operator's words."""
        b = self._blocked()
        if b:
            return b
        d = canon_direction(direction)
        if d is None:
            return _err("richiesta_ambigua", "In che direzione?", param="direzione", opzioni_valide=DIRECTIONS)
        lang = self.s.lang
        cm = await self._operator_amount("cm")
        if cm is None:
            said = await self._operator_text()
            if is_nudge(said) and d == "su":
                return await self.propose_rotate("spalla", "su", source=source)
            if is_nudge(said):
                cm = 10.0
            else:
                return _err("richiesta_ambigua", tr("how_much_cm", lang), param="quantita")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        b = self._blocked()
        if b:
            return b
        try:
            plan = self.planner.translate(q, d, cm, self.table_cal if self.table_cal.points else None)
        except SkillError as e:
            self.bus.publish("proposal_rejected", skill="sposta", params={"direzione": d, "cm": cm}, result=e.to_dict())
            return e.to_dict()
        self._last_amount["cm"] = cm
        amt = f"{round(cm, 1):g}"
        return self._propose_computed("sposta", {"direzione": d, "cm": amt}, plan, q,
                                      tr("move_question", lang, amount=amt, dir=word("dir", d, lang)),
                                      f"Punta spostata di {amt} cm verso {d}.", source)

    async def propose_rotate(self, joint: Any, side: Any, *, source: str = "grok") -> dict[str, Any]:
        """'ruota la base di 30 gradi a sinistra': one joint, the angle comes from the operator's words."""
        b = self._blocked()
        if b:
            return b
        j = canon_joint(joint)
        if j is None:
            return _err("richiesta_ambigua", "Quale giunto?", param="giunto", opzioni_valide=JOINTS)
        sd = canon_side(side)
        if sd is None:
            return _err("richiesta_ambigua", "In che verso?", param="verso",
                        opzioni_valide=["piu", "meno"] + list(((self.s.robot.get("relative") or {})
                                                               .get("joint_sides") or {}).get(j) or {}))
        lang = self.s.lang
        said = await self._operator_text()
        # "肘关节向上十公分" names a joint but states a length: that moves the tip, it does not rotate 10 degrees.
        if said and length_cm(said) is not None:
            if sd in ("sinistra", "destra", "avanti", "indietro", "su", "giu"):
                return await self.propose_move(sd, source=source)
            return _err("richiesta_ambigua", tr("cm_not_degrees", lang))
        deg = await self._operator_amount("deg")
        if deg is None and is_nudge(said):
            deg = 25.0
        if deg is None:
            return _err("richiesta_ambigua", tr("how_much_deg", lang, max=f"{self.planner.max_deg:g}"), param="quantita")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        b = self._blocked()
        if b:
            return b
        try:
            plan = self.planner.rotate(q, j, sd, deg, self.table_cal if self.table_cal.points else None)
        except SkillError as e:
            self.bus.publish("proposal_rejected", skill="ruota", params={"giunto": j, "verso": sd}, result=e.to_dict())
            return e.to_dict()
        self._last_amount["deg"] = deg
        amt = f"{round(deg, 1):g}"
        return self._propose_computed("ruota", {"giunto": j, "verso": sd, "gradi": amt}, plan, q,
                                      tr("rotate_question", lang, joint=word("joint", j, lang), amount=amt,
                                         dir=word("dir", sd, lang)),
                                      f"Giunto {j} ruotato di {amt} gradi ({sd}).", source)

    async def propose_batch(self, steps: list[dict[str, Any]], *, source: str = "grok") -> dict[str, Any]:
        """Several tip moves and joint turns from one sentence. One yes runs them all, in order."""
        b = self._blocked()
        if b:
            return b
        if not steps or len(steps) > 5:
            return _err("sequenza_non_valida", "La sequenza deve avere da 2 a 5 passi.")
        parsed: list[tuple[str, str, str | None]] = []
        for st in steps:
            kind = str(st.get("kind") or st.get("tipo") or "")
            if kind in ("move", "sposta"):
                d = canon_direction(st.get("direzione") or st.get("direction"))
                if d is None:
                    return _err("richiesta_ambigua", "In che direzione?", param="direzione", opzioni_valide=DIRECTIONS)
                parsed.append(("move", d, None))
            elif kind in ("rotate", "ruota"):
                j = canon_joint(st.get("giunto") or st.get("joint"))
                sd = canon_side(st.get("verso") or st.get("side"))
                if j is None or sd is None:
                    return _err("richiesta_ambigua", "Quale giunto e in che verso?", param="giunto", opzioni_valide=JOINTS)
                parsed.append(("rotate", j, sd))
            else:
                return _err("sequenza_non_valida", "Ogni passo è uno spostamento della punta o la rotazione di un giunto.")
        said = await self._operator_text()
        amounts = amounts_in_order(said)
        lang = self.s.lang
        if len(amounts) != len(parsed) or any(
                (kind == "move" and unit != "cm") or (kind == "rotate" and unit != "deg")
                for (kind, _, _), (unit, _) in zip(parsed, amounts)):
            return _err("richiesta_ambigua", tr("batch_amounts", lang, n=len(parsed)), param="quantita")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        b = self._blocked()
        if b:
            return b
        q0 = list(q)
        motion: list = []
        timeout = 0.0
        lines: list[str] = []
        cal = self.table_cal if self.table_cal.points else None
        try:
            for (kind, target, side), (_, amt) in zip(parsed, amounts):
                shown = f"{round(amt, 1):g}"
                if kind == "move":
                    plan = self.planner.translate(q, target, amt, cal)
                    lines.append(tr("step_move", lang, amount=shown, dir=word("dir", target, lang)))
                else:
                    plan = self.planner.rotate(q, target, side, amt, cal)
                    lines.append(tr("step_rotate", lang, joint=word("joint", target, lang), amount=shown,
                                    dir=word("dir", side or "", lang)))
                motion.extend(plan.motion)
                timeout += plan.timeout_s
                q = list(plan.motion[-1].joints)
        except SkillError as e:
            self.bus.publish("proposal_rejected", skill="sequenza", params={"passi": len(parsed)}, result=e.to_dict())
            return e.to_dict()
        phrase = tr("batch_question", lang, n=len(parsed), steps="；".join(f"{i + 1}. {s}" for i, s in enumerate(lines)))
        plan = type("Batch", (), {"motion": motion, "timeout_s": timeout, "info": {}})()
        return self._propose_computed("sequenza", {"passi": str(len(parsed))}, plan, q0, phrase,
                                      f"Sequenza di {len(parsed)} movimenti completata.", source)

    def _two_zone_plan(self, q: list[float], origin: str, dest: str):
        first = self.planner.point_above(q, self.planner.zone_xy(origin), descend=False)
        q2 = first.motion[-1].joints
        if q2 is None:
            return first
        second = self.planner.point_above(q2, self.planner.zone_xy(dest), descend=False)
        return self.planner._join(first, second)

    async def propose_transfer(self, origin: Any, dest: Any, *, source: str = "grok") -> dict[str, Any]:
        """Point at one zone, then the other. One command, no gripper."""
        b = self._blocked()
        if b:
            return b
        o, d = self.reg.normalize("zona", origin), self.reg.normalize("zona", dest)
        if not o or not d or o == d:
            return _err("richiesta_ambigua", "从哪边指到哪边？左右要是两个不同的区域。", param="posizione",
                        opzioni_valide=list(self.s.zones))
        if self.s.gripper_on:
            return _err("pinza_non_collegata", "夹爪开关是开的，但这台控制器没有夹爪驱动，所以不会抓。关掉开关就用手腕指。")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        try:
            plan = self._two_zone_plan(list(q), o, d)
        except SkillError as e:
            plan = await self._transfer_guard(list(q), o, d)
            if plan is None:
                return e.to_dict()
        lang = self.s.lang
        phrase = tr("transfer_question", lang, origin=word("zone", o, lang), dest=word("zone", d, lang))
        return self._propose_computed("indica_tra", {"origine": o, "destinazione": d}, plan, list(q), phrase,
                                      "Polso sull'altra zona.", source)

    async def _point_on_plane(self, colour: str, plane: Plane, source: str) -> dict[str, Any]:
        """Cup footprint on the saved tape plane. Proposes a point. Does not confirm or move."""
        if self.camera is None:
            return _err("telecamera_non_disponibile", "Telecamera non disponibile.")
        frame = self.camera.fresh(float((self.s.robot.get("cups") or {}).get("max_frame_age_s", 2.0)))
        if frame is None:
            return _err("telecamera_non_disponibile", "Nessuna immagine recente dalla telecamera.")
        loc = plane.locate(frame.image, colour)
        if not loc.get("ok"):
            return _err(loc.get("esito", "oggetto_non_trovato"), loc.get("messaggio", ""))
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        if self.s.gripper_on:
            return _err("pinza_non_collegata",
                        "夹爪开关是开的，但这台控制器还没有接上夹爪，所以不会抓。关掉开关就用手腕指。")
        xy = tuple(loc["xy_m"])
        try:
            plan = self.planner.point_above(q, xy)
        except SkillError as e:
            plan = await self._hover_any_pose(q, xy)
            if plan is None:
                return e.to_dict()
        phrase = "用手腕指到杯子上方，停在杯口上面大约 1 厘米。"
        res = self._propose_computed(
            "indica_bicchiere", {}, plan, q, phrase, "Polso sopra il bicchiere.", source,
            evidenza={"colore": colour, "xy_m": [round(float(v), 4) for v in xy]})
        if res.get("ok"):
            res["bicchiere"] = {"colore": colour, "xy_m": [round(v, 4) for v in loc["xy_m"]],
                                "rilevatore": "plane", "pinza": False}
        if source in ("rules", "ui") and res.get("ok"):
            return await self._run_operator_command(res)
        return res

    async def _run_operator_command(self, res: dict[str, Any]) -> dict[str, Any]:
        """The operator's own command authorizes this plan. Stop and a fault still block it."""
        pa = self.pending
        if pa is None or pa.id != res.get("action_id"):
            return res
        st = await self.refresh_robot()
        if self.pending is not pa or self.state != GateState.PENDING_CONFIRMATION:
            return _err("stato_cambiato", "状态变了，没有动。")
        if not st.ok_for_motion:
            self.cancel(source="operatore")
            return _err("robot_non_disponibile",
                        f"机器人现在不能动：{st.detail or st.safety or st.robot_mode}。")
        pa.used = True
        self.pending = None
        self._cancel = threading.Event()
        self.world.active_skill = pa.action.skill
        self.history.add(kind="confermata", action=pa.id, skill=pa.action.skill, params=pa.action.params,
                         text=pa.source)
        self._transition(GateState.EXECUTING, f"comando operatore {pa.source}", action=pa.id)
        self._exec_task = asyncio.ensure_future(self._execute(pa, self._cancel, self.clock()))
        return {"ok": True, "esito": "esecuzione_avviata", "action_id": pa.id, "skill": pa.action.skill,
                "bicchiere": res.get("bicchiere"), "messaggio": "按你的命令开始。急停仍然有效。"}

    async def point_at_saved_cup(self, *, source: str = "ui") -> dict[str, Any]:
        """Propose the hover written in the temporary calibration. Does not confirm and does not move."""
        b = self._blocked()
        if b:
            return b
        path = self.s.data_dir / "table_calibration.temporary.json"
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            xy = tuple(float(v) for v in saved["cup_xy_m"])
        except (OSError, ValueError, KeyError, TypeError):
            return _err("non_calibrato", "没有这次的临时标定。")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        try:
            plan = self.planner.point_above(q, xy)
        except SkillError as e:
            plan = await self._hover_any_pose(q, xy)
            if plan is None:
                return e.to_dict()
        phrase = "先绕到杯子上方，再降到杯口上面大约 1 厘米，不压下去。可以吗？"
        return self._propose_computed("indica_bicchiere", {}, plan, q, phrase,
                                      "Polso sopra il bicchiere, senza scendere.", source)

    async def _hover_any_pose(self, q: list[float], xy: tuple[float, float]):
        """Fallback when the arc planner refuses the start pose (e.g. home, tool not pointing down): the fastest
        safe joint route over every IK branch and tool heading to straight above xy, then the same slow descent."""
        from ..skills.cartesian import Plan
        rim = float((self.s.robot.get("touch") or {}).get("object_height_m", 0.09)) + 0.015
        loop = asyncio.get_running_loop()
        try:
            motion, info = await loop.run_in_executor(
                None, lambda: hover_and_lower(self.motion_guard, q, xy, hover_flange_z=max(0.16, rim + 0.04),
                                              low_flange_z=rim, speeds=self.s.motion))
        except GuardRejected as e:
            log.info("hover fallback refused: %s", e.message)
            return None
        return Plan(motion, timeout_s=info["timeout_s"],
                    info={"xy_mm": [round(xy[0] * 1000, 1), round(xy[1] * 1000, 1)], "rim_z_mm": round(rim * 1000, 1),
                          "percorso": "guard", "vias": info["vias"]})

    async def touch_cup(self, colour: Any, *, zone: Any = None, source: str = "grok") -> dict[str, Any]:
        """Find the cup in the calibrated camera image and propose: above it, slow descent to the rim, back up."""
        b = self._blocked()
        if b:
            return b
        c = str(colour or "").strip().lower()
        c = "grey" if c == "gray" else c
        c = c if c in ALL_COLOURS else colour_of(c)
        if c is None:
            return _err("richiesta_ambigua", "Di che colore è il bicchiere?", param="colore", opzioni_valide=ALL_COLOURS)
        if not self.table_cal.ready:
            return _err("non_calibrato", tr("marker_need", self.s.lang))
        scan = self.scan_cups()
        if not scan.get("ok"):
            return scan
        frame = self.camera.store.latest()
        if frame is not None and self.table_cal.frame_size and tuple(frame.size) != tuple(self.table_cal.frame_size):
            return _err("non_calibrato", "La risoluzione della telecamera è cambiata dalla calibrazione: ricalibra.")
        seen = [x for x in scan["cups"] if x["colore"] == c]
        z = self.reg.normalize("zona", zone) if zone else None
        if z:
            seen = [x for x in seen if x["zona"] == z]
        if not seen:
            hint = "" if scan.get("detector") == "background" else \
                " Per bicchieri bianchi, grigi o neri scatta prima la foto del tavolo vuoto."
            return _err("oggetto_non_trovato", f"Non vedo nessun bicchiere {c}.{hint}",
                        visti=[f"{x['colore']} ({x['zona']})" for x in scan["cups"]])
        if len(seen) > 1:
            where = "；".join(x.get("posto") or x["zona"] for x in seen)
            return _err("richiesta_ambigua", tr("cups_same_colour", self.s.lang, n=len(seen),
                                                colour=word("colour", c, self.s.lang), where=where),
                        param="posizione", opzioni_valide=sorted({x["zona"] for x in seen}))
        cup = seen[0]
        if (self.s.robot.get("touch") or {}).get("cup_point") == "bottom":
            x0, y0, w, h = cup["bbox"]
            u, v = x0 + w / 2, y0 + h
        else:
            u, v = cup["px"]
        xy = self.table_cal.to_xy(u, v)
        height = await self._stereo_height_m(frame, cup)
        if height is None:
            height = self._cup_height_m(frame, cup)
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        b = self._blocked()
        if b:
            return b
        try:
            plan = self.planner.touch(q, xy, self.table_cal, height_m=height)
        except SkillError as e:
            if str(e) == "fuori_dal_tavolo":
                return _err(e.code, tr("outside_table", self.s.lang))
            self.bus.publish("proposal_rejected", skill="tocca", params={"colore": c}, result=e.to_dict())
            return e.to_dict()
        lang = self.s.lang
        x_mm, y_mm = plan.info["xy_mm"]
        res = self._propose_computed("tocca", {"colore": c}, plan, q,
                                     tr("touch_question", lang, colour=word("colour", c, lang), x=f"{x_mm:.0f}",
                                        y=f"{y_mm:.0f}"),
                                     f"Bicchiere {c} toccato a X {x_mm:.0f} mm, Y {y_mm:.0f} mm.", source,
                                     px=[round(u), round(v)])
        if res.get("ok"):
            res["bicchiere"] = {"colore": c, "px": [round(u), round(v)], "xy_mm": [x_mm, y_mm],
                                "rilevatore": scan.get("detector")}
        return res

    # ============================================================ grasp ===
    _GRAB_Q = {
        "it": "Vedo il bicchiere {colour} a X {x} mm, Y {y} mm. Passo sopra gli altri bicchieri, scendo dietro, infilo "
              "l'anello alla base e lo sollevo di {lift} mm. Confermi?",
        "en": "I see the {colour} cup at X {x} mm, Y {y} mm. I will pass above the other cups, come down behind it, slide "
              "the ring around its base and lift it {lift} mm. Proceed?",
        "zh": "看到{colour}纸杯在 X {x} 毫米、Y {y} 毫米。我会从其他杯子上方过去，在它后面降下，把半圆环套住杯底，再托起 {lift} 毫米。确认吗？",
    }

    def _grab_error(self, e: SkillError) -> dict[str, Any]:
        key, *nums = str(e).split(":")
        if key == "anello_sconosciuto":
            return _err(e.code, "Non so da che lato pende l'anello: metti il braccio con la flangia orizzontale e "
                                "l'anello verso il basso, oppure scrivi gripper.offset_axis_flange in robot.yaml.")
        if key == "anello_troppo_stretto":
            return _err(e.code, f"Il bicchiere è largo {nums[0]} mm dove passa l'anello (accetta {nums[1]} mm): "
                                "misuralo e scrivi grasp.cup_bottom_diameter_m in robot.yaml.")
        return e.to_dict()

    async def grab_cup(self, colour: Any, *, zone: Any = None, source: str = "grok") -> dict[str, Any]:
        """Find the cup in the calibrated camera and propose: over the other cups, down behind it, slide the ring
        around its base, lift. Checked by the motion guard with the gripper model and every cup as an obstacle."""
        from ..skills.grasp import plan_grasp
        b = self._blocked()
        if b:
            return b
        c = str(colour or "").strip().lower()
        c = "grey" if c == "gray" else c
        c = c if c in ALL_COLOURS else colour_of(c)
        if c is None:
            return _err("richiesta_ambigua", "Di che colore è il bicchiere?", param="colore", opzioni_valide=ALL_COLOURS)
        if not self.table_cal.ready:
            return _err("non_calibrato", tr("marker_need", self.s.lang))
        scan = self.scan_cups()
        if not scan.get("ok"):
            return scan
        frame = self.camera.store.latest()
        seen = [x for x in scan["cups"] if x["colore"] == c]
        z = self.reg.normalize("zona", zone) if zone else None
        if z:
            seen = [x for x in seen if x["zona"] == z]
        if not seen:
            return _err("oggetto_non_trovato", f"Non vedo nessun bicchiere {c}.",
                        visti=[f"{x['colore']} ({x['zona']})" for x in scan["cups"]])
        if len(seen) > 1:
            return _err("richiesta_ambigua", tr("cups_same_colour", self.s.lang, n=len(seen),
                                                colour=word("colour", c, self.s.lang),
                                                where="；".join(x.get("posto") or x["zona"] for x in seen)),
                        param="posizione", opzioni_valide=sorted({x["zona"] for x in seen}))
        bottom = (self.s.robot.get("touch") or {}).get("cup_point") == "bottom"

        def table_xy(cup: dict[str, Any]) -> tuple[float, float]:
            if bottom:
                x0, y0, w, h = cup["bbox"]
                return self.table_cal.to_xy(x0 + w / 2, y0 + h)
            return self.table_cal.to_xy(*cup["px"])

        cup = seen[0]
        xy = table_xy(cup)
        others = [table_xy(x) for x in scan["cups"] if x is not cup]
        height = await self._stereo_height_m(frame, cup)
        if height is None:
            height = self._cup_height_m(frame, cup)
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        try:
            plan = await asyncio.to_thread(plan_grasp, self.motion_guard, self.s, q, xy, height_m=height,
                                           others=others)
        except SkillError as e:
            self.bus.publish("proposal_rejected", skill="prendi", params={"colore": c}, result=e.to_dict())
            return self._grab_error(e)
        lang = self.s.lang if self.s.lang in self._GRAB_Q else "it"
        x_mm, y_mm = plan.info["xy_mm"]
        res = self._propose_computed("prendi", {"colore": c}, plan, q,
                                     self._GRAB_Q[lang].format(colour=word("colour", c, lang), x=f"{x_mm:.0f}",
                                                               y=f"{y_mm:.0f}", lift=f"{plan.info['sollevamento_mm']:.0f}"),
                                     f"Bicchiere {c} sollevato di {plan.info['sollevamento_mm']:.0f} mm.", source)
        if res.get("ok"):
            self._held = dict(plan.place)   # type: ignore[attr-defined]
            res["bicchiere"] = {"colore": c, "xy_mm": [x_mm, y_mm]}
        return res

    async def place_cup(self, *, source: str = "grok") -> dict[str, Any]:
        """Put the carried cup back down where it was picked and slide the ring out."""
        from ..skills.grasp import plan_place
        b = self._blocked()
        if b:
            return b
        held = getattr(self, "_held", None)
        if not held:
            return _err("precondizione_non_soddisfatta", "Non sto tenendo nessun bicchiere.")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        try:
            plan = plan_place(q, held, self.s)
        except SkillError as e:
            return e.to_dict()
        conf = {"zh": "把杯子放回原处并退出半圆环。确认吗？",
                "en": "I will put the cup back down where it was and slide the ring out. Proceed?"}.get(
            self.s.lang, "Rimetto giù il bicchiere dove l'ho preso e sfilo l'anello. Confermi?")
        return self._propose_computed("posa_bicchiere", {}, plan, q, conf, "Bicchiere posato.", source)

    # ======================================================== calibration ===
    async def calibration_add(self, u: Any, v: Any) -> dict[str, Any]:
        """Record one calibration point: pixel clicked by the operator + tip position measured on the robot NOW."""
        if self.state == GateState.EXECUTING:
            return _err("occupato", "Il braccio si sta muovendo.")
        if self.camera is None:
            return _err("telecamera_non_disponibile", "Telecamera non disponibile.")
        frame = self.camera.fresh(2.0)
        if frame is None:
            return _err("telecamera_non_disponibile", "Nessuna immagine recente dalla telecamera.")
        try:
            u, v = float(u), float(v)
        except (TypeError, ValueError):
            return _err("punto_non_valido", "Coordinate del punto non valide.")
        w, h = frame.size
        if not (0 <= u < w and 0 <= v < h):
            return _err("punto_non_valido", "Il punto è fuori dall'immagine.")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        try:
            self.table_cal.add((u, v), (w, h), self.planner.kin.fk(q), q, self.s.mode)
        except CalibrationError as e:
            return _err("calibrazione_rifiutata", str(e))
        self.history.add(kind="calibrazione", points=len(self.table_cal.points))
        self.publish_state()
        return {"ok": True, **self.table_cal.public()}

    _CORNER_MSG = {"punta_non_verticale": "tip_not_vertical", "angolo_lontano": "corner_too_far",
                   "area_troppo_piccola": "area_too_small"}

    def _corner_error(self, err: SkillError) -> dict[str, Any]:
        key = self._CORNER_MSG.get(str(err))
        if key:
            return _err(err.code, tr(key, self.s.lang))
        return err.to_dict()

    async def calibration_corners(self) -> dict[str, Any]:
        """Rectangle centred on the tip now. Coordinates stay here; the UI only receives millimetre offsets."""
        if self.state == GateState.EXECUTING:
            return _err("occupato", "Il braccio si sta muovendo.")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        try:
            corners = self.planner.table_corners(q)
        except SkillError as e:
            return self._corner_error(e)
        self._calib_corners = corners
        return {"ok": True, "corners": [{"i": c["i"], "name": c["name"], "dx_mm": c["dx_mm"], "dy_mm": c["dy_mm"],
                                         "label": word("corner_name", c["name"], self.s.lang)} for c in corners]}

    async def propose_calib_corner(self, index: Any, *, source: str = "ui") -> dict[str, Any]:
        b = self._blocked()
        if b:
            return b
        try:
            i = int(index)
        except (TypeError, ValueError):
            i = -1
        corners = self._calib_corners or []
        if not 0 <= i < len(corners):
            return _err("richiesta_ambigua", tr("corners_first", self.s.lang))
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        b = self._blocked()
        if b:
            return b
        c = corners[i]
        try:
            plan = self.planner.goto_table_point(q, c["xyz"], c["rot"])
        except SkillError as e:
            return self._corner_error(e)
        lang = self.s.lang
        info = plan.info
        phrase = tr("corner_question", lang, n=i + 1, name=word("corner_name", c["name"], lang),
                    dx=f"{info['dx_mm']:g}", dy=f"{info['dy_mm']:g}", lift=f"{info['lift_mm']:g}")
        res = self._propose_computed("angolo_calibrazione", {"angolo": str(i + 1)}, plan, q, phrase,
                                     f"Punta all'angolo {i + 1}. Clicca la punta nell'immagine.", source)
        if res.get("ok") and source == "ui":
            self.mark_question_asked(res["action_id"])
        return res

    async def confirm_from_button(self, action_id: str | None = None) -> dict[str, Any]:
        """The operator pressed the on-screen confirm. That sentence is their own, not the model's."""
        pa = self.pending
        if pa is None:
            return _err("nessuna_azione_in_attesa", tr("corners_first", self.s.lang))
        if action_id and action_id != pa.id:
            return _err("azione_diversa", "L'azione è cambiata: chiedi di nuovo lo spostamento.")
        if pa.question_asked_at is None:
            self.mark_question_asked(pa.id)
        self.note_transcript("是的", source="button")
        return await self.confirm(pa.id, source="button")

    async def go_calib_corner(self, index: Any) -> dict[str, Any]:
        """One tap on a corner: the operator asked for that move, so it is proposed and confirmed together."""
        proposed = await self.propose_calib_corner(index, source="ui")
        if not proposed.get("ok"):
            return proposed
        return await self.confirm_from_button(proposed["action_id"])

    def _cup_layout(self, cups: list[dict[str, Any]], frame: Any) -> tuple[str, list[dict[str, Any]]]:
        """Left-to-right and, once the table is calibrated, front-to-back. No coordinates leave this method."""
        lang = self.s.lang
        if not cups:
            return "", []
        cal = self.table_cal
        use_table = bool(cal.ready and cal.frame_size and tuple(frame.size) == tuple(cal.frame_size))
        rows = []
        for c in cups:
            if use_table:
                x, y = cal.to_xy(float(c["px"][0]), float(c["px"][1]))
            else:
                x, y = 0.0, float(c["px"][0])
            rows.append((c, x, y))
        by_left = sorted(rows, key=lambda row: row[2])
        by_front = sorted(rows, key=lambda row: row[1]) if use_table else []
        left_rank = {id(c): i + 1 for i, (c, _x, _y) in enumerate(by_left)}
        front_rank = {id(c): i + 1 for i, (c, _x, _y) in enumerate(by_front)}
        out = []
        for c, _x, _y in rows:
            if use_table:
                posto = tr("cup_place", lang, left=left_rank[id(c)], front=front_rank[id(c)], n=len(rows))
            else:
                posto = tr("cup_place_image", lang, left=left_rank[id(c)], n=len(rows))
            out.append({**c, "posto": posto})
        names = "、".join(word("colour", c["colore"], lang) for c, _x, _y in by_left)
        if use_table:
            front_names = "、".join(word("colour", c["colore"], lang) for c, _x, _y in by_front)
            layout = tr("cup_layout", lang, lr=names, fb=front_names)
        else:
            layout = tr("cup_layout_image", lang, lr=names)
        return layout, out

    def calibration_edit(self, action: str) -> dict[str, Any]:
        if action == "undo":
            self.table_cal.undo()
        elif action == "clear":
            self.table_cal.clear()
            self._marker = {"center": None, "axis": None}
        self.publish_state()
        return {"ok": True, **self.table_cal.public(), "marker": self.marker_status()}

    def _camera_matrix(self, size: tuple[int, int]) -> np.ndarray | None:
        raw = dict(self.s.robot.get("camera_intrinsics") or {})
        vals = [raw.get("fx"), raw.get("fy"), raw.get("cx"), raw.get("cy")]
        if any(v is None for v in vals):
            return None
        return np.array([[float(vals[0]), 0.0, float(vals[2])],
                         [0.0, float(vals[1]), float(vals[3])],
                         [0.0, 0.0, 1.0]], dtype=np.float64)

    def _cup_height_m(self, frame, cup: dict[str, Any]) -> float | None:
        """Rim height above the table. None keeps the fixed cup height: the lens is not measured yet."""
        if frame is None or not cup.get("bbox"):
            return None
        matrix = self._camera_matrix(frame.size)
        if matrix is None:
            return None
        from ..vision.height import HeightError, pose_from_marker, standing_xyz
        found = detect(frame.image)
        if found is None:
            return None
        x0, y0, w, h = cup["bbox"]
        try:
            rvec, tvec = pose_from_marker(found["corners"], self._marker_size_m(), matrix)
            _x, _y, height = standing_xyz((x0 + w / 2.0, y0 + h), (x0 + w / 2.0, y0), rvec, tvec, matrix)
        except HeightError:
            return None
        return height

    async def _stereo_height_m(self, frame, cup: dict[str, Any]) -> float | None:
        """Rim height measured by the stereo depth service (python -m stereo_depth serve).

        Off unless `stereo.url` (config/robot.yaml) or STEREO_URL is set. Any failure, a different
        image size, or a height outside the plausible range returns None, and the existing paths decide.
        The request is async with a short timeout so STOP is never delayed.
        """
        import os
        cfg = dict(self.s.robot.get("stereo") or {})
        url = (os.getenv("STEREO_URL") or cfg.get("url") or "").rstrip("/")
        if not url or frame is None or not cup.get("bbox"):
            return None
        x0, y0, w, h = (float(v) for v in cup["bbox"])
        try:
            import httpx
            async with httpx.AsyncClient(timeout=float(cfg.get("timeout_s", 1.5))) as client:
                r = await client.get(f"{url}/api/height", params={"x0": x0, "y0": y0, "w": w, "h": h})
            data = r.json()
        except Exception as e:
            log.warning("stereo height unavailable: %s", e)
            return None
        if not data.get("ok"):
            log.info("stereo height refused: %s", data.get("error"))
            return None
        if list(data.get("frame_size") or []) != list(frame.size):
            log.warning("stereo frame %s != camera frame %s: use CAMERA_URL=%s/frame.jpg",
                        data.get("frame_size"), frame.size, url)
            return None
        height = float(data["height_m"])
        lo, hi = float(cfg.get("min_height_m", 0.03)), float(cfg.get("max_height_m", 0.35))
        if not lo <= height <= hi:
            return None
        self.history.add(kind="stereo_height", height_mm=round(height * 1000), points=data.get("points"),
                         plane=data.get("plane"))
        return height

    def _marker_size_m(self) -> float:
        return float((self.s.robot.get("marker") or {}).get("size_m", 0.05))

    def marker_status(self) -> dict[str, Any]:
        seen = False
        try:
            frame = self.camera.store.latest() if self.camera is not None else None
            if frame is not None:
                seen = detect(frame.image) is not None
        except Exception:
            log.exception("marker detect")
            seen = False
        return {"seen": seen, "center": self._marker.get("center") is not None,
                "axis": self._marker.get("axis") is not None,
                "size_mm": round(self._marker_size_m() * 1000)}

    async def marker_record(self, which: str) -> dict[str, Any]:
        """Store the tip on the marker centre or on the arrow, then fit the plane when both exist.

        Neither button moves the arm. The operator jogs the tip there on the pendant.
        """
        if which not in ("center", "axis"):
            return _err("punto_non_valido", "Punto del marcatore sconosciuto.")
        if self.state == GateState.EXECUTING:
            return _err("occupato", "Il braccio si sta muovendo.")
        q = await self._joints_now()
        if isinstance(q, dict):
            return q
        T = self.planner.kin.fk(q)
        R = T[:3, :3]
        tilt = float(np.degrees(np.arccos(np.clip(-R[2, 2], -1, 1))))
        if tilt > 20.0:
            return _err("calibrazione_rifiutata", tr("marker_tilt", self.s.lang))
        sample = {"xy": (float(T[0, 3]), float(T[1, 3])), "z": float(T[2, 3]),
                  "rot": R.tolist(), "joints": tuple(float(v) for v in q)}
        other_name = "axis" if which == "center" else "center"
        other = self._marker.get(other_name)
        if other is not None:
            if abs(sample["z"] - float(other["z"])) > 0.015:
                return _err("calibrazione_rifiutata", tr("marker_height", self.s.lang))
            from ..robot.kinematics import angle_between
            if float(np.degrees(angle_between(R, np.array(other["rot"])))) > 5.0:
                return _err("calibrazione_rifiutata", tr("marker_orient", self.s.lang))
        self._marker[which] = sample
        if self._marker.get("center") and self._marker.get("axis"):
            return await self._marker_fit()
        self.publish_state()
        return {"ok": True, **self.table_cal.public(), "marker": self.marker_status()}

    async def _marker_fit(self) -> dict[str, Any]:
        if self.camera is None:
            return _err("telecamera_non_disponibile", tr("no_frame", self.s.lang))
        frame = self.camera.fresh(2.0)
        if frame is None:
            return _err("telecamera_non_disponibile", tr("no_frame", self.s.lang))
        found = detect(frame.image)
        if found is None:
            return _err("marcatore_non_visto", tr("marker_not_seen", self.s.lang))
        center, axis = self._marker["center"], self._marker["axis"]
        try:
            pairs = pairs_for_plane(found["corners"], center["xy"], axis["xy"], self._marker_size_m())
        except MarkerFitError:
            return _err("marcatore_misura", tr("marker_size", self.s.lang, mm=round(self._marker_size_m() * 1000)))
        except CalibrationError as e:
            return _err("calibrazione_rifiutata", str(e))
        self.table_cal.replace_plane(pairs, center["z"], center["rot"], center["joints"], frame.size, self.s.mode)
        if not self.table_cal.ready:
            return _err("calibrazione_rifiutata", self.table_cal.error or tr("marker_need", self.s.lang))
        try:
            cells = self.planner.table_reach(center["joints"], np.array(center["rot"]), float(center["z"]))
        except SkillError as e:
            return self._corner_error(e)
        self.table_cal.set_reach(cells)
        self.history.add(kind="calibrazione", points=len(self.table_cal.points), marker=True)
        self.publish_state()
        if not self.table_cal.ready:
            return _err("calibrazione_rifiutata", self.table_cal.error or tr("marker_need", self.s.lang))
        return {"ok": True, **self.table_cal.public(), "marker": self.marker_status()}

    def calibration_image(self) -> bytes | None:
        """Latest frame with the calibration points (and the calibrated area) drawn on it."""
        import cv2
        import numpy as np
        frame = self.camera.store.latest() if self.camera else None
        if frame is None:
            return None
        img = frame.image.copy()
        cal = self.table_cal
        if cal.ready:
            inv = np.linalg.inv(cal.fit.homography)
            if cal.reachable:
                hull = cv2.convexHull(np.array(cal.reachable, dtype=np.float32) * 1000).reshape(-1, 2).astype(np.float64)
            else:
                hull = cal.fit.workspace_polygon().astype(np.float64)
            px = cv2.perspectiveTransform(hull.reshape(-1, 1, 2), inv).reshape(-1, 2).astype(np.int32)
            cv2.polylines(img, [px], True, (60, 200, 60), 2)
        for i, p in enumerate(cal.points):
            c = (int(p.px[0]), int(p.px[1]))
            cv2.drawMarker(img, c, (0, 0, 255), cv2.MARKER_CROSS, 22, 2)
            cv2.putText(img, str(i + 1), (c[0] + 8, c[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None

    @property
    def motion_guard(self):
        if self._motion_guard is None:
            self._motion_guard = guard_from_settings(self.s)
        return self._motion_guard

    def _named_unverified(self, act: ValidatedAction) -> bool:
        """True when this skill still points at joints that were never recorded on this arm."""
        sk = self.reg.skills.get(act.skill)
        if sk is None or self.s.mode != "real":
            return False
        for st in sk.sequenza:
            if st.get("move") == "wait":
                continue
            name = st["posa"].format(**act.params)
            if not (self.s.poses.get(name) or {}).get("verificata_reale"):
                return True
        return False

    async def _plan_named(self, act: ValidatedAction, joints: list[float] | None) -> tuple[list | None, str]:
        """Replace an unrecorded zone sequence with a plan from the current joints.

        Home and the waiting pose keep their joint list. A planning failure is an error and sends nothing.
        """
        if not self._named_unverified(act):
            return None, ""
        if not joints:
            return None, "还不知道手臂现在的位置，所以这条指令没有发出去。"
        loop = asyncio.get_running_loop()
        try:
            plan = await loop.run_in_executor(
                None, lambda: self.planner.command_plan(list(joints), act.skill, dict(act.params)))
        except SkillError as e:
            plan = await self._named_guard_plan(list(joints), act)
            if plan is None:
                return None, str(e)
        if plan is None:
            return None, ""
        act.timeout_s = max(float(act.timeout_s), float(plan.timeout_s))
        act.extra.update(plan.info)
        return list(plan.motion), ""

    async def _named_guard_plan(self, joints: list[float], act: ValidatedAction):
        """When the arc cannot leave this pose, search a safe joint route to the same zone."""
        from ..skills.cartesian import Plan
        params = act.params
        zone = params.get("posizione")
        if act.skill == "vai_a_posa" and str(params.get("posa", "")).endswith("_sopra"):
            zone = str(params["posa"])[: -len("_sopra")]
        if zone not in ("sinistra", "centro", "destra"):
            return None
        xy = self.planner.zone_xy(zone)
        place = act.skill in ("appoggia_vassoio", "smista")
        hover_z, low_z = (0.18, 0.12) if place else (0.18, 0.16)
        loop = asyncio.get_running_loop()
        try:
            motion, info = await loop.run_in_executor(
                None, lambda: hover_and_lower(
                    self.motion_guard, joints, xy, hover_flange_z=hover_z, low_flange_z=low_z, speeds=self.s.motion))
        except GuardRejected:
            return None
        if act.skill in ("indica", "vai_a_posa"):
            motion = [m for m in motion if m.move != "movel"]
        elif act.skill in ("solleva_vassoio", "porta_in_ispezione", "spingi"):
            hover_steps = [m for m in motion if m.move != "movel"]
            q2 = hover_steps[-1].joints if hover_steps else None
            if q2 is None:
                return None
            try:
                if act.skill == "spingi":
                    from ..vision.plane import TAUGHT_TAPE_M
                    ys = [p[1] for p in TAUGHT_TAPE_M.values()]
                    extra = self.planner.translate(q2, params["direzione"], (max(ys) - min(ys)) / 3.0 * 100.0)
                else:
                    extra = self.planner.translate(q2, "su", 15.0)
            except SkillError:
                return None
            motion = hover_steps + list(extra.motion)
        return Plan(motion, timeout_s=float(info["timeout_s"]) + 12,
                    info={"xy_mm": [round(xy[0] * 1000, 1), round(xy[1] * 1000, 1)], "percorso": "guard"})

    async def _transfer_guard(self, q: list[float], origin: str, dest: str):
        """Wrist over one zone, then the other, when the straight arc cannot start."""
        from ..skills.cartesian import Plan
        loop = asyncio.get_running_loop()
        motion: list = []
        timeout = 0.0
        cur = list(q)
        for zone in (origin, dest):
            xy = self.planner.zone_xy(zone)
            start = list(cur)
            try:
                steps, info = await loop.run_in_executor(
                    None, lambda xy=xy, start=start: hover_and_lower(
                        self.motion_guard, start, xy, hover_flange_z=0.18, low_flange_z=0.16,
                        speeds=self.s.motion))
            except GuardRejected:
                return None
            hover = [m for m in steps if m.move != "movel"]
            if not hover or hover[-1].joints is None:
                return None
            motion.extend(hover)
            timeout += float(info["timeout_s"])
            cur = list(hover[-1].joints)
        return Plan(motion, timeout_s=timeout + 12, info={"percorso": "guard"})

    async def _recheck_evidence(self, act: ValidatedAction, joints: list[float] | None) -> str:
        """Before a cup move, look again. No evidence means this check does not apply."""
        ev = dict((act.extra or {}).get("evidenza") or {})
        if not ev or self.camera is None:
            return ""
        frame = self.camera.fresh(float((self.s.robot.get("cups") or {}).get("max_frame_age_s", 2.0)))
        if frame is None:
            return "没有新的画面，没有动。"
        if "xy_m" in ev:
            plane = Plane.load(self.s.data_dir / "plane.json")
            if plane is None:
                return ""
            loc = plane.locate(frame.image, str(ev.get("colore") or ""))
            if not loc.get("ok"):
                return str(loc.get("messaggio") or "没有再看到那个杯子，没有动。")
            old, new = ev["xy_m"], loc["xy_m"]
            if float(np.hypot(float(new[0]) - float(old[0]), float(new[1]) - float(old[1]))) <= 0.03:
                return ""
            if not joints:
                return "杯子挪了，没有当前位置，没有动。"
            try:
                plan = self.planner.point_above(joints, (float(new[0]), float(new[1])))
            except SkillError:
                plan = await self._hover_any_pose(joints, (float(new[0]), float(new[1])))
                if plan is None:
                    return "杯子挪了，新位置走不过去，没有动。"
            act.motion = plan.motion
            act.extra["evidenza"]["xy_m"] = [round(float(v), 4) for v in new]
            return ""
        colour, zone = ev.get("colore"), ev.get("zona")
        if colour and zone:
            seen = self.scan_cups()
            if not seen.get("ok"):
                return str(seen.get("messaggio") or "没有新的画面，没有动。")
            if not any(c.get("colore") == colour and c.get("zona") == zone for c in seen.get("cups") or []):
                return f"没有再看到{colour}在原来的区域，没有动。"
        return ""

    async def _guarded_motion(self, act: ValidatedAction, cancel: threading.Event) -> tuple[list | None, str]:
        """Re-check the confirmed motion from the robot's ACTUAL joints right before sending it.

        Blocked direct moves get safe via points (same final target); anything still unsafe is not sent.
        Fails closed: a guard error means no motion. ``guard.enabled: false`` sends the motion unchanged.
        """
        guard_on = bool((self.s.robot.get("guard") or {}).get("enabled", True))
        joints: list[float] | None = None
        if guard_on or self.s.mode == "real":
            st = await self.refresh_robot()
            joints = list(st.joints) if st.joints else None
        why = await self._recheck_evidence(act, joints)
        if why:
            return None, why
        planned, err = await self._plan_named(act, joints)
        if err:
            return None, err
        if planned is not None:
            act.motion = planned
        if not guard_on or not joints:
            return list(act.motion), ""
        loop = asyncio.get_running_loop()
        try:
            r = await loop.run_in_executor(None, self.motion_guard.secure, list(st.joints), list(act.motion))
        except GuardRejected as e:
            self.history.add(kind="percorso_rifiutato", skill=act.skill, code=e.code, reason=e.message)
            return None, f"percorso non sicuro: {e.message}"
        except Exception as e:  # fail closed
            log.exception("motion guard")
            return None, f"controllo del percorso non riuscito ({type(e).__name__}): nessun movimento"
        if cancel.is_set():
            return None, "fermato prima del movimento"
        if r.vias_added or r.replaced:
            self.history.add(kind="percorso_sicuro", skill=act.skill, vias=r.vias_added, replaced=r.replaced,
                             plan_ms=r.plan_ms)
        self.metrics.record("guard_plan_ms", r.plan_ms, skill=act.skill)
        return r.motion, ""
    def start_inspection(self, rule: str | None, *, source: str = "grok") -> dict[str, Any]:
        """Set the natural-language rule and bring the next part to the camera (that motion needs a confirmation).

        If the part is already held at the camera pose, inspect it again without moving."""
        b = self._blocked()
        if b:
            return b
        insp = self.s.inspection
        if not insp:
            return _err("ispezione_non_configurata", "Ispezione non configurata in robot.yaml.")
        if self.camera is None or self.inspector is None:
            return _err("telecamera_non_disponibile", "Telecamera o ispettore non disponibili.")
        rule = clean_rule(rule) or self.inspection.rule
        if not rule:
            return _err("regola_mancante", "Serve la regola di ispezione, ad esempio: scarta i pezzi con graffi o "
                                           "anomalie.")
        self.inspection.rule = rule
        self.history.add(kind="regola_ispezione", rule=rule, source=source)
        self.publish_state()
        if self.world.holding_tray and self.world.known_pose == insp["inspect_pose"]:
            if self.state == GateState.PENDING_CONFIRMATION:
                self.cancel(source=f"{source}:nuova_ispezione")
            self._begin_inspection()
            return {"ok": True, "esito": "ispezione_avviata", "regola": rule,
                    "istruzione": "Il pezzo è già davanti alla telecamera: di' solo 'Analizzo.' L'esito arriverà in "
                                  "un [RAPPORTO SISTEMA]."}
        res = self.propose("porta_in_ispezione", {"posizione": insp["input_zone"]}, source=source)
        if res.get("ok") and self.pending is not None:
            self.pending.action.conferma = tr("bring", self.s.lang, tray=self.s.zone_label(insp["input_zone"]))
            res["frase_conferma"] = self.pending.action.conferma
            self.publish_state()
        res["regola"] = rule
        return res

    def part_ready(self, *, operator: str = "ui") -> dict[str, Any]:
        """Operator declares a new part in the input tray (bookkeeping only, never moves the robot)."""
        insp = self.s.inspection
        if not insp:
            return _err("ispezione_non_configurata", "Ispezione non configurata.")
        if self.state == GateState.EXECUTING or self.inspection.running:
            return _err("occupato", "Attendi la fine del movimento o dell'ispezione.")
        if self.world.holding_tray:
            return _err("forca_occupata", "La forca ha ancora un pezzo: deposita prima quello.")
        self.world.trays_at.add(insp["input_zone"])
        self.history.add(kind="pezzo_pronto", zone=insp["input_zone"], operator=operator)
        self.publish_state()
        return {"ok": True, "esito": "pezzo_pronto", "zona": insp["input_zone"]}

    def _begin_inspection(self) -> None:
        self.inspection.running = True
        self.bus.publish("inspection", phase="started", rule=self.inspection.rule)
        self.publish_state()
        self._inspect_task = asyncio.ensure_future(self._run_inspection())

    async def _wait_frame(self, after: float, deadline: float) -> Any:
        while True:
            frame = self.camera.store.latest()
            if frame is not None and frame.received_at >= after:
                return frame
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.1)

    async def _run_inspection(self) -> None:
        insp, lang, rule = self.s.inspection, self.s.lang, self.inspection.rule
        settle, max_age = float(insp.get("settle_s", 0.8)), float(insp.get("max_frame_age_s", 2.0))
        result: dict[str, Any] | None = None
        frame = None
        try:
            t0 = time.monotonic()
            await asyncio.sleep(settle)
            frame = await self._wait_frame(t0 + settle, t0 + settle + max_age)
            if frame is None:
                result = {"status": "NO_FRAME", "reason": tr("no_frame", lang), "source": "camera"}
            else:
                loop = asyncio.get_running_loop()
                try:
                    r = await asyncio.wait_for(loop.run_in_executor(self._io_pool, self.inspector.inspect, frame.image,
                                                                    rule, lang), 25)
                    result = r.to_dict()
                except asyncio.TimeoutError:
                    result = {"status": UNSURE, "reason": "", "source": getattr(self.inspector, "name", "?"),
                              "error": "timeout"}
        finally:
            self.inspection.running = False
        self._finish_inspection(result, frame, rule, lang)

    def _finish_inspection(self, result: dict[str, Any], frame: Any, rule: str, lang: str) -> None:
        insp = self.s.inspection
        status = result["status"]
        if status == UNSURE and result.get("error") and not result.get("reason"):
            result["reason"] = tr("vlm_error", lang, error=result["error"][:120])
        result.update(rule=rule, at=time.time(), frame_id=frame.frame_id if frame else None)
        self.inspection.last = result
        if frame is not None:
            self.inspection.last_jpeg = frame.jpeg
        if status in self.inspection.counts:
            self.inspection.counts[status] += 1
        self.history.add(kind="ispezione", status=status, reason=result["reason"], rule=rule,
                         source=result.get("source"))
        self._log_inspection(result, frame)
        self.bus.publish("inspection", phase="done", **result)
        self.publish_state()
        if self.state != GateState.IDLE:          # STOP / fault while the model was thinking: no proposal
            return
        if status == "NO_FRAME":
            self._report("ispezione", result["reason"], inspection=result)
            return
        pass_tray, fail_tray = self.s.zone_label(insp["pass_zone"]), self.s.zone_label(insp["fail_zone"])
        if status not in (PASS, FAIL):
            self._report("ispezione", "ISPEZIONE INCERTA. Leggi all'operatore: " +
                         tr("unsure", lang, reason=result["reason"], pass_tray=pass_tray, fail_tray=fail_tray) +
                         f" Se decide, proponi smista con posizione {insp['pass_zone']} (approvato) o "
                         f"{insp['fail_zone']} (scartato).", inspection=result)
            return
        zone = insp["pass_zone"] if status == PASS else insp["fail_zone"]
        try:
            act = self.reg.validate("smista", {"posizione": zone}, self.world)
        except SkillError as e:
            self._report("ispezione", f"Esito {status}: {result['reason']} Deposito non proponibile: {e}",
                         inspection=result)
            return
        act.conferma = tr("question", lang, verdict=tr(f"verdict_{status}", lang), reason=result["reason"],
                          tray=self.s.zone_label(zone))
        res = self._set_pending(act, "inspection")
        if not self._report_cbs:                  # no Grok session: the question is shown in the UI right now
            self.mark_question_asked(res["action_id"])
        self._report("ispezione", f"ISPEZIONE {status}, proposta {res['action_id']} in attesa di conferma. Leggi "
                                  f"ESATTAMENTE all'operatore: «{act.conferma}» Poi aspetta la sua risposta; con un sì "
                                  f"chiama confirm({res['action_id']}).",
                     inspection=result, say=act.conferma, action_id=res["action_id"])

    def _log_inspection(self, result: dict[str, Any], frame: Any) -> None:
        try:
            d = self.s.data_dir / "inspections"
            d.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            if frame is not None:
                (d / f"{stamp}-{frame.frame_id}.jpg").write_bytes(frame.jpeg)
                result = {**result, "image": f"{stamp}-{frame.frame_id}.jpg"}
            with (d / "inspections.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
        except OSError:  # pragma: no cover
            log.exception("could not write the inspection log")
