"""Deterministic safety gate.

    IDLE ──propose──▶ PENDING_CONFIRMATION ──confirm (validated)──▶ EXECUTING ──result──▶ IDLE
      ▲                     │ cancel / expiry / voice loss                │ unknown/failed ─▶ FAULT
      └─────────────────────┘                                             │
    any state ──STOP──▶ STOPPED      STOPPED/FAULT ──operator reset (robot verified)──▶ IDLE

Grok (or the text-mode rules) may only *propose* and *ask* to confirm.
Authorisation is decided here from the operator's own transcript.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..config import Settings
from ..monitoring.events import EventBus, History, Metrics, log_event
from ..robot.base import COMPLETED, FAILED, NOT_STARTED, STOPPED, UNKNOWN, ExecutionResult, RobotAdapter, RobotStatus
from ..skills.registry import Registry, SkillError, ValidatedAction
from ..skills.world import WorldModel, joints_close
from . import phrases

log = logging.getLogger("voce.gate")


class GateState(str, Enum):
    IDLE = "IDLE"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    STOPPED = "STOPPED"
    FAULT = "FAULT"


@dataclass
class Utterance:
    id: int
    t: float                      # monotonic time the operator's turn was committed
    text: str | None = None       # transcript (may arrive slightly after the commit)
    source: str = "voice"         # voice | text
    used_for: str | None = None   # action id it confirmed (one-time)
    done: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class PendingAction:
    id: str
    action: ValidatedAction
    created: float
    source: str
    question_asked_at: float | None = None
    used: bool = False
    plan_id: str | None = None
    step_index: int = 0
    step_total: int = 1
    remaining: list[dict[str, Any]] = field(default_factory=list)   # raw later steps (re-validated each time)
    kind: str = "skill"                                          # skill | sequence | cycle

    def public(self, ttl: float, now: float) -> dict[str, Any]:
        out = {"id": self.id, **self.action.public(), "expires_in_s": max(0.0, round(ttl - (now - self.created), 1)),
               "ttl_s": ttl, "question_asked": self.question_asked_at is not None, "source": self.source,
               "plan_id": self.plan_id, "step": self.step_index + 1, "steps": self.step_total}
        frasi = (self.action.extra or {}).get("conferme") or {}
        if frasi:
            out["frasi"] = frasi
        return out


def _err(code: str, msg: str, **kw: Any) -> dict[str, Any]:
    return {"ok": False, "esito": code, "messaggio": msg, **kw}


class Gate:
    STATUS_POLL_S = 0.5

    def __init__(self, settings: Settings, registry: Registry, adapter: RobotAdapter, bus: EventBus,
                 metrics: Metrics | None = None, history: History | None = None,
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        self.s = settings
        self.reg = registry
        self.robot = adapter
        self.bus = bus
        self.metrics = metrics or Metrics(path=None)
        self.history = history or History()
        self.clock = clock
        self.state = GateState.IDLE
        self.pending: PendingAction | None = None
        self.world = WorldModel.initial(settings.zones, settings.robot["workspace"].get("trays_assumed_at", []))
        self.robot_status: RobotStatus = RobotStatus(connected=False, moving=False, detail="non ancora interrogato")
        self.last_result: ExecutionResult | None = None
        self.last_stop: dict[str, Any] | None = None
        self.fault_reason: str | None = None
        self.stop_on_voice_loss = bool(settings.robot["safety"].get("stop_on_voice_loss", True))
        self._utt_ids = itertools.count(1)
        self._act_ids = itertools.count(1)
        self._plan_ids = itertools.count(1)
        self.utterance: Utterance | None = None
        self._exec_task: asyncio.Task | None = None
        self._cancel: threading.Event | None = None
        self._exec_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="robot-exec")
        self._io_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="robot-io")
        self._bg: list[asyncio.Task] = []
        self._report_cbs: list[Callable[[dict[str, Any]], Any]] = []
        self.cycle: dict[str, Any] | None = None

    # ================================================================ infra ===
    def _transition(self, new: GateState, reason: str, **data: Any) -> None:
        old = self.state
        self.state = new
        log_event(log, f"{old.value} -> {new.value}: {reason}", old=old.value, new=new.value, reason=reason, **data)
        self.bus.publish("gate", old=old.value, new=new.value, reason=reason)
        self.publish_state()

    def publish_state(self) -> None:
        self.bus.publish("state", **self.status())

    def status(self) -> dict[str, Any]:
        now = self.clock()
        return {"gate": self.state.value,
                "pending": self.pending.public(self.s.ttl_s, now) if self.pending else None,
                "world": self.world.snapshot(), "robot": self.robot_status.to_dict(), "robot_adapter": self.robot.name,
                "mode": self.s.mode, "fault": self.fault_reason, "last_stop": self.last_stop,
                "last_result": self.last_result.to_dict() if self.last_result else None,
                "metrics": self.metrics.summary(), "history": self.history.list()[:30],
                "cycle": self.cycle}

    def on_report(self, cb: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        self._report_cbs.append(cb)
        return lambda: self._report_cbs.remove(cb) if cb in self._report_cbs else None

    def _report(self, kind: str, text: str, text_en: str | None = None, **data: Any) -> None:
        ev = self.bus.publish("report", kind=kind, text=text, text_en=text_en or text, **data)
        for cb in list(self._report_cbs):
            try:
                r = cb(ev)
                if asyncio.iscoroutine(r):
                    asyncio.ensure_future(r)
            except Exception:  # pragma: no cover
                log.exception("report callback failed")

    async def _io(self, fn: Callable, *args: Any, timeout: float = 3.0) -> Any:
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(loop.run_in_executor(self._io_pool, fn, *args), timeout)

    async def start(self) -> None:
        """Safe startup: connect, read state, never resume anything."""
        self.pending = None
        try:
            await self._io(self.robot.connect, timeout=float(self.s.robot["connections"].get("connect_timeout_s", 3)) + 1)
        except Exception as e:
            self.robot_status = RobotStatus(connected=False, moving=False, detail=f"connessione fallita: {e}")
            log_event(log, "robot connect failed", error=str(e))
        await self.refresh_robot()
        loop = asyncio.get_running_loop()
        if hasattr(self.robot, "listeners"):
            last = [0.0]

            def on_joints(q: list[float]) -> None:
                now = time.time()
                if now - last[0] >= 0.05:
                    last[0] = now
                    loop.call_soon_threadsafe(lambda: self.bus.publish("joints", joints=[round(x, 4) for x in q]))
            self.robot.listeners.cbs.append(on_joints)
        if hasattr(self.robot, "progress_cb"):
            def on_progress(done: int, total: int, pose: str | None) -> None:
                loop.call_soon_threadsafe(lambda: self.bus.publish("progress", done=done, total=total, pose=pose))
            self.robot.progress_cb = on_progress
        self._bg.append(asyncio.ensure_future(self._poll_loop()))
        self.publish_state()

    async def shutdown(self) -> None:
        """Safe shutdown: stop motion if any, drop pending, disconnect."""
        for t in self._bg:
            t.cancel()
        if self.state == GateState.EXECUTING:
            await self.stop("shutdown")
        self.pending = None
        try:
            await self._io(self.robot.disconnect)
        except Exception:
            pass
        self._exec_pool.shutdown(wait=False, cancel_futures=True)
        self._io_pool.shutdown(wait=False, cancel_futures=True)

    async def refresh_robot(self) -> RobotStatus:
        try:
            st = await self._io(self.robot.status, timeout=2.5)
        except Exception as e:
            st = RobotStatus(connected=False, moving=False, detail=f"stato non disponibile: {e}")
        changed = st.to_dict() != self.robot_status.to_dict()
        self.robot_status = st
        if st.joints and self.state != GateState.EXECUTING:
            self.world.update_joints(st.joints, self.s.poses, float(self.s.robot["motion"]["joint_tolerance_rad"]))
        if changed:
            self.bus.publish("robot", robot=st.to_dict(), world=self.world.snapshot())
        return st

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.STATUS_POLL_S)
            try:
                self._expire_pending()
                await self.refresh_robot()   # also while EXECUTING (live "moving"); joint bookkeeping waits
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("poll failed")

    # ========================================================== utterances ===
    def note_turn_committed(self, source: str = "voice") -> Utterance:
        """The operator finished a turn (VAD commit); its transcript may follow."""
        u = Utterance(id=next(self._utt_ids), t=self.clock(), source=source)
        self.utterance = u
        return u

    def note_transcript(self, text: str | None, *, final: bool = True, source: str = "voice",
                        stop_allowed: bool = True) -> bool:
        """Operator transcript. Returns True if it triggered STOP (handled independently of Grok)."""
        stop = stop_allowed and phrases.is_stop(text)
        if final:
            u = self.utterance
            if u is None or u.text is not None or u.source != source:
                u = self.note_turn_committed(source)
            u.text = text or ""
            u.done.set()
            self.bus.publish("transcript", role="user", text=text, final=True, source=source)
            self.history.add(kind="utterance", source=source, text=text)
        if stop:
            asyncio.ensure_future(self.stop(f"transcript:{source}", detail=text or ""))
        return stop

    def note_partial(self, text: str | None, *, source: str = "voice") -> bool:
        """Interim transcript: only used to catch STOP as early as possible."""
        if phrases.is_stop(text) and self.state != GateState.STOPPED:
            asyncio.ensure_future(self.stop(f"transcript:{source}", detail=f"(parziale) {text}"))
            return True
        return False

    # ============================================================ proposal ===
    def _blocked(self) -> dict[str, Any] | None:
        if self.state == GateState.STOPPED:
            return _err("sistema_fermo", "Sistema in STOP: serve il ripristino da parte dell'operatore dall'interfaccia.")
        if self.state == GateState.FAULT:
            return _err("guasto", f"Sistema in GUASTO ({self.fault_reason}): serve l'intervento dell'operatore.")
        if self.state == GateState.EXECUTING:
            return _err("occupato", "Il braccio sta eseguendo un movimento: attendi il risultato o usa il pulsante rosso.")
        return None

    def propose(self, skill: Any, params: dict[str, Any] | None, *, source: str = "grok",
                evidenza_id: str | None = None) -> dict[str, Any]:
        b = self._blocked()
        if b:
            return b
        try:
            act = self.reg.validate(skill, params, self.world)
        except SkillError as e:
            self.bus.publish("proposal_rejected", skill=str(skill), params=params, result=e.to_dict())
            return e.to_dict()
        if evidenza_id:
            err = self._check_evidence(evidenza_id, act.params)
            if err:
                self.bus.publish("proposal_rejected", skill=act.skill, params=act.params, result=err)
                return err
            self._decorate_evidence(act, evidenza_id)
        return self._set_pending(act, source)

    def propose_cycle(self, product_id: str, conferme: dict[str, str], *, source: str = "grok") -> dict[str, Any]:
        """Pending action of kind cycle. The same confirm() path authorises SortingCycle.start."""
        b = self._blocked()
        if b:
            return b
        act = ValidatedAction(
            skill="ciclo_etichettatura", params={"product_id": product_id}, motion=[], timeout_s=120,
            conferma=conferme.get("it") or "Avvio il ciclo di etichettatura. Confermi?",
            risultato="Ciclo avviato.",
            conferma_en=conferme.get("en") or "Starting the labelling cycle. Do you confirm?",
            risultato_en="Cycle started.",
            extra={"conferme": dict(conferme), "kind": "cycle", "product_id": product_id},
        )
        res = self._set_pending(act, source)
        res["kind"] = "cycle"
        return res

    def _check_evidence(self, eid: str, params: dict[str, str]) -> dict[str, Any] | None:
        store = getattr(self, "evidence", None)
        ev = store.get(eid) if store is not None else None
        if not ev:
            return _err("evidenza_scaduta", "L'evidenza visiva è assente o più vecchia di 15 secondi. Nessun movimento.")
        zone = params.get("origine") or params.get("posizione")
        if zone and ev.get("zona") != zone:
            return _err("evidenza_scaduta", "L'evidenza non è sulla zona richiesta. Nessun movimento.")
        return None

    def _decorate_evidence(self, act: ValidatedAction, eid: str) -> None:
        from ..i18n import evidence_sentence
        ev = self.evidence.get(eid) or {}
        cand = ev.get("candidato") or {}
        cosa = " ".join(x for x in (cand.get("categoria"), cand.get("colore"), cand.get("testo")) if x) or "un oggetto"
        conf = f"{max(0.0, 1.0 - float(cand.get('colore_de') or 30) / 100):.2f}"
        prova = "colore verificato" if "CV" in (cand.get("fonti") or []) else "osservazione"
        conferme = act.extra.setdefault("conferme", {"it": act.conferma, "en": act.conferma_en})
        for lang in list(conferme):
            prefix = evidence_sentence(lang, cosa=cosa, zona=str(ev.get("zona") or ""), prova=prova, conf=conf)
            if prefix:
                conferme[lang] = prefix + " " + conferme[lang]
        act.conferma = conferme.get("it", act.conferma)
        act.conferma_en = conferme.get("en", act.conferma_en)
        act.extra["evidenza_id"] = eid
        if ev.get("zona"):
            self.world.observed[str(ev["zona"])] = time.time()

    def propose_sequence(self, steps: list[dict[str, Any]], *, source: str = "grok") -> dict[str, Any]:
        """P2: validate a whole sequence (on a simulated world copy). Each movement still needs its own confirmation."""
        b = self._blocked()
        if b:
            return b
        if not steps or len(steps) > 5:
            return _err("sequenza_non_valida", "La sequenza deve avere da 1 a 5 passi.")
        sim = self.world.copy()
        acts: list[ValidatedAction] = []
        for i, st in enumerate(steps):
            try:
                a = self.reg.validate(st.get("skill"), st.get("params") or st.get("parametri"), sim)
            except SkillError as e:
                d = e.to_dict()
                d["passo"] = i + 1
                return d
            sim.apply_effects(a.skill, a.params)
            acts.append(a)
        plan_id = f"S{next(self._plan_ids)}"
        res = self._set_pending(acts[0], source, plan_id=plan_id, step_total=len(acts),
                                remaining=[{"skill": a.skill, "params": a.params} for a in acts[1:]])
        res["piano"] = [{"passo": i + 1, "skill": a.skill, "parametri": a.params, "descrizione": a.conferma}
                        for i, a in enumerate(acts)]
        res["istruzione"] = ("Descrivi il piano completo, poi chiedi conferma SOLO per il passo 1. "
                             "Ogni passo successivo richiederà una nuova conferma.")
        return res

    def _set_pending(self, act: ValidatedAction, source: str, *, plan_id: str | None = None, step_index: int = 0,
                     step_total: int = 1, remaining: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        replaced = self.pending.id if self.pending else None
        pa = PendingAction(id=f"A{next(self._act_ids)}", action=act, created=self.clock(), source=source,
                           plan_id=plan_id, step_index=step_index, step_total=step_total, remaining=remaining or [],
                           kind=str(act.extra.get("kind") or "skill"))
        self.pending = pa
        self._transition(GateState.PENDING_CONFIRMATION, f"proposta {pa.id} {act.skill}",
                         action=pa.id, skill=act.skill, params=act.params, replaced=replaced)
        self.history.add(kind="proposta", action=pa.id, skill=act.skill, params=act.params, source=source)
        conferme = act.extra.get("conferme") or {"it": act.conferma, "en": act.conferma_en}
        out = {"ok": True, "esito": "in_attesa_di_conferma", "action_id": pa.id, "skill": act.skill,
               "kind": pa.kind, "parametri": act.params, "frase_conferma": act.conferma,
               "frase_conferma_en": act.conferma_en, "scade_in_s": self.s.ttl_s,
               "istruzione": "Leggi la frase_conferma_<lingua> all'operatore, parola per parola, e aspetta. "
                             "Non chiamare confirm finché l'operatore non ha risposto sì in un nuovo turno."}
        for lang, sent in conferme.items():
            out[f"frase_conferma_{lang}"] = sent
        out["frase_conferma_it"] = conferme.get("it", act.conferma)
        return out

    def mark_question_asked(self, action_id: str | None = None) -> None:
        pa = self.pending
        if pa and pa.question_asked_at is None and (action_id is None or action_id == pa.id):
            pa.question_asked_at = self.clock()
            log_event(log, "confirmation question asked", action=pa.id)
            self.publish_state()

    def _expire_pending(self) -> bool:
        pa = self.pending
        if pa and self.clock() - pa.created > self.s.ttl_s:
            self.pending = None
            self.history.add(kind="scaduta", action=pa.id, skill=pa.action.skill)
            if self.state == GateState.PENDING_CONFIRMATION:
                self._transition(GateState.IDLE, f"proposta {pa.id} scaduta")
            self._report("scaduta", "La proposta è scaduta, ripeti il comando.",
                         text_en="The proposal expired, please repeat the command.")
            return True
        return False

    def cancel(self, *, source: str = "grok") -> dict[str, Any]:
        pa = self.pending
        self.pending = None
        if pa:
            self.history.add(kind="annullata", action=pa.id, skill=pa.action.skill, source=source)
        if self.state == GateState.PENDING_CONFIRMATION:
            self._transition(GateState.IDLE, f"annullata da {source}")
        return {"ok": True, "annullata": pa.id if pa else None}

    # ======================================================== confirmation ===
    async def confirm(self, action_id: str | None, *, source: str = "grok") -> dict[str, Any]:
        t_req = self.clock()
        if self.state in (GateState.STOPPED, GateState.FAULT, GateState.EXECUTING):
            return self._blocked()  # type: ignore[return-value]
        pa = self.pending
        if pa is None:
            return _err("nessuna_azione_in_attesa", "Non c'è nessuna azione in attesa di conferma.")
        if action_id and action_id != pa.id:
            return _err("azione_diversa", f"L'azione in attesa è {pa.id}: l'azione è cambiata, serve una nuova conferma.")
        if pa.used:
            return _err("duplicato", "Conferma già utilizzata: ignorata.")
        if self._expire_pending():
            return _err("scaduta", "La proposta è scaduta. Va riproposta.")
        if pa.question_asked_at is None:
            return _err("domanda_non_posta", "Prima chiedi conferma all'operatore e aspetta la sua risposta.")
        u = self.utterance
        if u is None or u.t <= pa.question_asked_at:
            return _err("nessuna_risposta", "L'operatore non ha ancora risposto alla domanda di conferma.")
        if u.text is None:  # transcript still in flight (it lags the commit by ~0.7 s)
            try:
                await asyncio.wait_for(u.done.wait(), 2.5)
            except asyncio.TimeoutError:
                return _err("trascrizione_mancante", "Trascrizione della risposta non disponibile: chiedi di ripetere.")
        if u.used_for:
            return _err("duplicato", "Questa risposta ha già autorizzato un'azione.")
        verdict = phrases.confirmation_verdict(u.text)
        if verdict != phrases.VALID:
            self.history.add(kind="conferma_rifiutata", action=pa.id, text=u.text, verdict=verdict, source=source)
            self.bus.publish("confirmation_rejected", action=pa.id, text=u.text, verdict=verdict)
            return _err("conferma_non_valida", f"La risposta “{u.text}” non è una conferma esplicita ({verdict}).",
                        verdetto=verdict)
        st = await self.refresh_robot()
        # anything may have happened while awaiting (STOP, new proposal, expiry)
        if self.pending is not pa or self.state != GateState.PENDING_CONFIRMATION or pa.used:
            return _err("stato_cambiato", "Lo stato è cambiato durante la conferma: nessun movimento.")
        if not st.ok_for_motion:
            return _err("robot_non_disponibile", f"Robot non disponibile: {st.detail or st.safety or st.robot_mode}.")
        recheck = getattr(self, "recheck", None)
        if recheck is not None:
            t_check = time.perf_counter()
            problem = recheck(pa)
            # A slow check (Grok vision) must not sit between yes and motion. Only a fast deterministic answer counts.
            if problem and (time.perf_counter() - t_check) <= 0.3:
                self.pending = None
                if self.state == GateState.PENDING_CONFIRMATION:
                    self._transition(GateState.IDLE, "percezione cambiata prima del movimento")
                return problem if isinstance(problem, dict) else _err("percezione_cambiata", str(problem))
        if pa.kind == "cycle":
            pa.used = True
            u.used_for = pa.id
            product = pa.action.params.get("product_id", "")
            self.pending = None
            if self.state == GateState.PENDING_CONFIRMATION:
                self._transition(GateState.IDLE, f"ciclo confermato {pa.id}")
            starter = getattr(self, "cycle_starter", None)
            if starter is None:
                return _err("ciclo_non_disponibile", "Il ciclo non è collegato. Nessun movimento.")
            lang = getattr(self, "operator_lang", "it") or "it"
            return await starter(str(product), f"voice:{lang}")
        # ---- authorised: exactly this action, exactly once
        pa.used = True
        u.used_for = pa.id
        self.metrics.record("confirmation_response_ms", (t_req - pa.question_asked_at) * 1000, action=pa.id)
        self.history.add(kind="confermata", action=pa.id, skill=pa.action.skill, params=pa.action.params, text=u.text)
        self.pending = None
        self._cancel = threading.Event()
        self.world.active_skill = pa.action.skill
        self._transition(GateState.EXECUTING, f"confermata {pa.id} da “{u.text}”", action=pa.id)
        self._exec_task = asyncio.ensure_future(self._execute(pa, self._cancel, t_req))
        return {"ok": True, "esito": "esecuzione_avviata", "action_id": pa.id,
                "istruzione": "Di' solo 'Procedo.' Il risultato reale arriverà in un messaggio [RAPPORTO SISTEMA]."}

    # =========================================================== execution ===
    async def _execute(self, pa: PendingAction, cancel: threading.Event, t_req: float) -> None:
        act = pa.action
        loop = asyncio.get_running_loop()
        timeout = act.timeout_s + float(self.s.robot["safety"].get("execution_timeout_margin_s", 5))
        res: ExecutionResult | None = None
        try:
            res = await asyncio.wait_for(
                loop.run_in_executor(self._exec_pool, self.robot.execute, act.skill, act.params, act.motion, cancel,
                                     act.timeout_s), timeout)
        except asyncio.TimeoutError:
            cancel.set()
            await self._robot_stop("execution timeout")
            self._fault(f"timeout dopo {timeout:.0f}s: esito sconosciuto", pa)
            return
        except Exception as e:
            cancel.set()
            await self._robot_stop("execution exception")
            self._fault(f"errore di esecuzione: {e}", pa)
            return
        finally:
            self.world.active_skill = None

        self.last_result = res
        self.metrics.record("robot_execution_ms", res.duration * 1000, skill=act.skill)
        self.metrics.record("request_to_completion_ms", (self.clock() - t_req) * 1000, skill=act.skill)
        st = await self.refresh_robot()
        if st.joints:  # refresh_robot skips joint bookkeeping while EXECUTING
            self.world.update_joints(st.joints, self.s.poses, float(self.s.robot["motion"]["joint_tolerance_rad"]))

        if res.outcome == COMPLETED:
            last_target = next((m.joints for m in reversed(act.motion) if m.joints), None)
            q = st.joints or res.final_robot_state.get("joints")
            tol = float(self.s.robot["motion"]["joint_tolerance_rad"]) * 3
            if last_target and q and not joints_close(q, last_target, tol):
                self._fault("posizione finale diversa dal target: esito incerto", pa)
                return
            self.world.apply_effects(act.skill, act.params)
            self.note_motion_outcome(act.skill, act.params, "completato")
            self.history.add(kind="completata", action=pa.id, skill=act.skill, params=act.params,
                             duration_s=res.duration)
            if self.state == GateState.EXECUTING:
                self._transition(GateState.IDLE, f"{pa.id} completata")
            text = (f"COMPLETATO {pa.id}: {act.risultato} Eseguita la sequenza di movimento configurata in "
                    f"{res.duration:.1f}s, confermata dal feedback del robot. Non ci sono sensori sul vassoio: il suo "
                    f"stato è presunto. Posa attuale: {self.world.known_pose or 'non nominata'}.")
            if pa.remaining and self.state == GateState.IDLE:
                nxt = pa.remaining[0]
                try:
                    a2 = self.reg.validate(nxt["skill"], nxt["params"], self.world)
                except SkillError as e:
                    self._report("completato", text + f" Sequenza interrotta: passo successivo non valido ({e}).",
                                 result=res.to_dict())
                    return
                self._set_pending(a2, pa.source, plan_id=pa.plan_id, step_index=pa.step_index + 1,
                                  step_total=pa.step_total, remaining=pa.remaining[1:])
                text += (f" Passo {pa.step_index + 2} di {pa.step_total} pronto ({self.pending.id}): "
                         f"{a2.conferma} Serve una NUOVA conferma.")
            self._report("completato", text, text_en=f"DONE {pa.id}: {act.risultato_en} Configured motion sequence executed in "
                         f"{res.duration:.1f}s, confirmed by robot feedback. No sensor on the tray: its state is assumed.",
                         result=res.to_dict())
        elif res.outcome == STOPPED:
            self.note_motion_outcome(act.skill, act.params, "uncertain")
            for _ in range(20):           # program state can lag the halt by a few ms: report observed standstill
                if not st.moving:
                    break
                await asyncio.sleep(0.05)
                st = await self.refresh_robot()
            if self.state != GateState.STOPPED:
                self._transition(GateState.STOPPED, f"{pa.id} interrotta")
            self.history.add(kind="interrotta", action=pa.id, skill=act.skill, steps=res.steps_completed)
            self._report("fermato", f"Robot in sicurezza durante {pa.id} ({act.skill}) dopo {res.steps_completed}/{res.steps_total} "
                                    f"movimenti. Robot: {self._robot_brief(st)}. Serve RIPRISTINA dall'interfaccia.",
                         text_en=f"Robot halted safely during {pa.id} ({act.skill}) after {res.steps_completed}/{res.steps_total} movements. "
                                 f"Press RESET (RIPRISTINA) in the interface.", result=res.to_dict())
        elif res.outcome == NOT_STARTED:
            self.note_motion_outcome(act.skill, act.params, "uncertain")
            self.history.add(kind="non_eseguita", action=pa.id, skill=act.skill, error=res.error)
            if self.state == GateState.EXECUTING:
                self._transition(GateState.IDLE, f"{pa.id} non avviata: {res.error}")
            self._report("non_eseguito", f"NON ESEGUITO {pa.id}: {res.error}. Il braccio non si è mosso.",
                         text_en=f"NOT EXECUTED {pa.id}: {res.error}. The arm did not move.", result=res.to_dict())
        else:  # FAILED or UNKNOWN
            self._fault(f"{res.outcome}: {res.error}", pa, res)

    def note_motion_outcome(self, skill: str, params: dict, outcome: str) -> None:
        """Tell a simulated station about a verified result. Anything else must not move the scene."""
        fn = getattr(self, "on_verified_motion", None)
        if fn is None:
            return
        try:
            fn(skill, params, outcome)
        except Exception:
            log.exception("scene update failed")

    def _fault(self, reason: str, pa: PendingAction | None, res: ExecutionResult | None = None) -> None:
        self.fault_reason = reason
        self.world.fault = reason
        self.note_motion_outcome(pa.action.skill if pa else "", pa.action.params if pa else {}, "uncertain")
        self.world.known_pose = None
        self.pending = None
        self.history.add(kind="guasto", action=pa.id if pa else None, reason=reason)
        if self.state != GateState.STOPPED:
            self._transition(GateState.FAULT, reason)
        self._report("guasto", f"Si è verificato un guasto: {reason}. Serve RIPRISTINA dall'interfaccia. La sessione resta in ascolto.",
                     text_en=f"A fault occurred: {reason}. Press RESET. The session keeps listening.",
                     result=res.to_dict() if res else None)

    def _robot_brief(self, st: RobotStatus) -> str:
        parts = ["in movimento" if st.moving else "in posizione", f"posa {self.world.known_pose or 'non nominata'}"]
        if st.safety:
            parts.append(f"sicurezza {st.safety}")
        if not st.connected:
            parts = ["NON connesso"]
        return ", ".join(parts)

    # ================================================================= STOP ===
    async def _robot_stop(self, why: str) -> dict[str, Any]:
        """Robot stop on a dedicated thread (never queued behind the running motion)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def run() -> None:
            try:
                ack = self.robot.stop()
                loop.call_soon_threadsafe(fut.set_result, ack.to_dict())
            except Exception as e:  # stop must never raise into the caller
                loop.call_soon_threadsafe(fut.set_result, {"acknowledged": False, "detail": f"stop fallito: {e}"})

        threading.Thread(target=run, name="robot-stop", daemon=True).start()
        try:
            return await asyncio.wait_for(fut, 3.0)
        except asyncio.TimeoutError:
            return {"acknowledged": False, "detail": "nessuna risposta allo stop entro 3s"}

    async def stop(self, source: str, *, detail: str = "") -> dict[str, Any]:
        t0 = time.time()
        m0 = self.clock()
        if self._cancel:
            self._cancel.set()                 # 0. invalidate the running authorisation
        robot_ack = asyncio.ensure_future(self._robot_stop(source))   # 1. request robot stop immediately
        pa = self.pending                       # 3-5. cancel pending / queued, invalidate authorisation
        self.pending = None
        self.end_cycle(f"STOP da {source}")
        self.note_motion_outcome("", {}, "uncertain")
        if self.utterance:
            self.utterance.used_for = self.utterance.used_for or "STOP"
        was = self.state
        if was != GateState.STOPPED:            # 6-7. STOPPED + broadcast
            self._transition(GateState.STOPPED, f"STOP da {source}", source=source, detail=detail,
                             cancelled=pa.id if pa else None)
        self.bus.publish("stop", source=source, detail=detail, at=t0, was=was.value)
        ack = await robot_ack
        st = await self.refresh_robot()
        # robot ack = stop commands accepted by the controller (adapter-measured); total = until feedback read back
        total_ms = round((time.time() - t0) * 1000, 1)
        ack_ms = ack.get("ack_latency_ms", total_ms)
        info = {"source": source, "detail": detail, "requested_at": t0, "previous_state": was.value,
                "robot_ack": ack, "ack_latency_ms": ack_ms, "halt_latency_ms": ack.get("halt_latency_ms"),
                "total_latency_ms": total_ms,
                "robot_status": st.to_dict(), "cancelled_pending": pa.id if pa else None}
        if was != GateState.STOPPED or self.last_stop is None:
            self.last_stop = info          # keep the pathway that actually stopped the robot first
        kind = source.split(":")[0]
        self.metrics.record(f"stop_ack_ms[{kind}]", ack_ms, source=source)
        if ack.get("halt_latency_ms") is not None:
            self.metrics.record(f"stop_halt_ms[{kind}]", ack["halt_latency_ms"], source=source)
        log_event(log, "STOP", transcript=detail, reason=source, **{k: v for k, v in info.items() if k != "robot_status"})
        self.history.add(kind="stop", source=source, previous=was.value, ack_ms=ack_ms, robot=self._robot_brief(st))
        self.bus.publish("stop_ack", **info)
        self.publish_state()
        if was not in (GateState.EXECUTING, GateState.STOPPED):  # executing: _execute() reports; already stopped: no repeat
            self._report("fermato", f"Robot in sicurezza ({source}). "
                         + (f"Proposta {pa.id} annullata. " if pa else "")
                         + f"Robot: {self._robot_brief(st)}. Nuovi movimenti in attesa di RIPRISTINA.",
                         text_en=f"Robot halted safely ({source}). " + (f"Proposal {pa.id} cancelled. " if pa else "")
                         + "New movements blocked until RESET.")
        _ = m0
        return {"ok": True, "esito": "fermato", **{k: info[k] for k in ("ack_latency_ms", "previous_state")},
                "robot": self._robot_brief(st)}

    # ================================================================ reset ===
    async def reset(self, *, operator: str = "ui") -> dict[str, Any]:
        """Deliberate operator action only (never from voice). Verifies the robot first; never moves it."""
        if self.state == GateState.EXECUTING:
            return _err("occupato", "Impossibile ripristinare durante un movimento.")
        if self.state not in (GateState.STOPPED, GateState.FAULT):
            return {"ok": True, "esito": "gia_pronto", "gate": self.state.value}
        st = await self.refresh_robot()
        if not st.connected:
            try:
                await self._io(self.robot.connect, timeout=5)
            except Exception as e:
                return _err("robot_non_connesso", f"Robot non raggiungibile: {e}")
            st = await self.refresh_robot()
        if not st.connected:
            return _err("robot_non_connesso", st.detail or "Robot non connesso.")
        if st.moving:
            return _err("robot_in_movimento", "Il robot risulta ancora in movimento: attendere l'arresto.")
        if not st.ok_for_motion:
            return _err("robot_non_pronto", f"Robot non pronto (modo {st.robot_mode}, sicurezza {st.safety}). "
                                            "Risolvere sul teach pendant, poi ripristinare.")
        prev = self.state
        self.fault_reason = None
        self.world.fault = None
        self.pending = None
        if self.utterance:
            self.utterance.used_for = self.utterance.used_for or "RESET"
        self._transition(GateState.IDLE, f"ripristino operatore ({operator}) da {prev.value}", operator=operator)
        self.history.add(kind="ripristino", operator=operator, previous=prev.value)
        self._report("ripristino", f"Sistema ripristinato dall'operatore. Robot {self._robot_brief(st)}.",
                     text_en="System reset by the operator.")
        return {"ok": True, "esito": "ripristinato", "gate": self.state.value, "robot": st.to_dict()}

    # ==================================================== labelling cycle ===
    # The operator explicitly authorises ONE production cycle for ONE approved product. While it is active,
    # only the predefined cycle movements may run without a per-move voice confirmation; each still goes
    # through validation, the one-at-a-time rule, timeouts, FAULT handling and STOP. STOP ends the authorisation.
    CYCLE_ALLOWED = {("solleva_vassoio", (("posizione", "sinistra"),)), ("appoggia_vassoio", (("posizione", "centro"),)),
                     ("solleva_vassoio", (("posizione", "centro"),)), ("appoggia_vassoio", (("posizione", "destra"),)),
                     ("spingi", (("direzione", "scarto"), ("posizione", "centro")))}

    def authorize_cycle(self, product_id: str, operator: str) -> dict[str, Any]:
        b = self._blocked()
        if b:
            return b
        if self.pending:
            return _err("occupato", "C'è un'azione in attesa di conferma.")
        if not self.robot_status.ok_for_motion:
            return _err("robot_non_disponibile", f"Robot non pronto: {self.robot_status.detail}")
        cid = f"C{next(self._plan_ids)}"
        self.cycle = {"id": cid, "product_id": product_id, "operator": operator, "active": True, "started_at": time.time()}
        self.history.add(kind="ciclo_autorizzato", cycle=cid, product=product_id, operator=operator)
        log_event(log, "cycle authorised", cycle=cid, product=product_id, operator=operator)
        self.publish_state()
        return {"ok": True, "cycle_id": cid}

    def end_cycle(self, reason: str) -> None:
        c = getattr(self, "cycle", None)
        if c and c["active"]:
            c["active"] = False
            c["ended"] = reason
            self.history.add(kind="ciclo_terminato", cycle=c["id"], reason=reason)
            log_event(log, "cycle ended", cycle=c["id"], reason=reason)
            self.publish_state()

    async def run_cycle_step(self, cycle_id: str, skill: str, params: dict[str, str]) -> ExecutionResult | dict[str, Any]:
        """Execute one predefined cycle movement and WAIT for the real result."""
        c = getattr(self, "cycle", None)
        if not c or not c["active"] or c["id"] != cycle_id:
            return _err("ciclo_non_autorizzato", "Nessun ciclo autorizzato attivo.")
        key = (skill, tuple(sorted(params.items())))
        if key not in self.CYCLE_ALLOWED:
            return _err("movimento_non_previsto", f"{skill} {params} non fa parte del ciclo.")
        b = self._blocked()
        if b or self.pending:
            return b or _err("occupato", "Azione in attesa.")
        try:
            act = self.reg.validate(skill, params, self.world)
        except SkillError as e:
            return e.to_dict()
        st = await self.refresh_robot()
        if not st.ok_for_motion:
            return _err("robot_non_disponibile", f"Robot non pronto: {st.detail or st.safety}")
        pa = PendingAction(id=f"A{next(self._act_ids)}", action=act, created=self.clock(), source=f"ciclo:{cycle_id}", used=True)
        self._cancel = threading.Event()
        self.world.active_skill = act.skill
        self.history.add(kind="confermata", action=pa.id, skill=act.skill, params=act.params, text=f"ciclo {cycle_id}")
        self._transition(GateState.EXECUTING, f"ciclo {cycle_id}: {act.skill} {act.params}", action=pa.id)
        t_req = self.clock()
        self._exec_task = asyncio.ensure_future(self._execute(pa, self._cancel, t_req))
        await self._exec_task
        return self.last_result if self.last_result and self.last_result.skill == act.skill else _err("esito_sconosciuto", "")

    # ============================================================ P2: poses ===
    async def save_pose(self, name: str) -> dict[str, Any]:
        """Record the robot's CURRENT joints under a new name (never joints from the conversation)."""
        import re

        import yaml
        b = self._blocked()
        if b or self.state == GateState.PENDING_CONFIRMATION:
            return b or _err("occupato", "C'è un'azione in attesa di conferma.")
        key = re.sub(r"[^a-z0-9_]", "", str(name).strip().lower().replace(" ", "_"))
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", key or ""):
            return _err("nome_non_valido", "Nome non valido: usa una o due parole semplici.")
        existing = self.s.poses.get(key)
        if existing and existing.get("fonte") != "voce":
            return _err("nome_riservato", f"'{key}' è una posa configurata: scegli un altro nome.")
        st = await self.refresh_robot()
        if not st.connected or st.moving or not st.joints:
            return _err("robot_non_disponibile", "Posizione attuale non disponibile.")
        pose = {"joints": [round(x, 5) for x in st.joints], "selezionabile": True, "alias": [key.replace("_", " ")],
                "fonte": "voce", "verificata_reale": self.s.mode == "real"}
        path = self.s.data_dir / "user_poses.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
        data.setdefault("poses", {})[key] = pose
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        self.s.poses[key] = pose
        self.history.add(kind="posa_salvata", name=key, joints=pose["joints"])
        self.publish_state()
        return {"ok": True, "esito": "posa_salvata", "nome": key, "nota": "Salvata, non eseguita. Usare vai_a_posa."}

    # ======================================================= voice session ===
    async def on_voice_disconnected(self, reason: str, *, stop_motion: bool | None = None) -> None:
        """Clear pending. A Grok-only drop passes stop_motion=False. The browser socket keeps the fail-safe."""
        pa = self.pending
        self.pending = None
        if self.utterance:
            self.utterance.used_for = self.utterance.used_for or "DISCONNECT"
        self.utterance = None
        if pa:
            self.history.add(kind="annullata", action=pa.id, reason=f"voce disconnessa: {reason}")
        halt = self.stop_on_voice_loss if stop_motion is None else stop_motion
        if self.state == GateState.PENDING_CONFIRMATION:
            self._transition(GateState.IDLE, f"voce disconnessa ({reason}): proposta {pa.id if pa else ''} annullata")
        elif self.state == GateState.EXECUTING and halt:
            await self.stop("voice_lost", detail=reason)
        self.publish_state()
