"""The ONLY tools Grok receives, and their dispatch to the safety gate.

No tool can send URScript, joints, raw poses, open connections, change robot
configuration or bypass the gate. Every argument is validated by the gate /
registry; unknown tools are rejected.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..config import Settings
from ..safety.gate import Gate

PROMPT_FILE = Path(__file__).resolve().parents[1] / "voice" / "prompt_it.md"

SKILL_IDS = ["vai_a_posa", "indica", "solleva_vassoio", "appoggia_vassoio", "spingi", "sposta_vassoio",
             "porta_in_ispezione", "smista"]
PERCEPTION = None          # Station, set by main.create_app
LANGS = ["it", "en", "zh", "ja", "am"]
LABELLING: tuple | None = None   # (LabellingService, SortingCycle), set by main.create_app


def _params_schema(s: Settings, poses: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Includi SOLO i parametri detti dall'operatore. Ometti quelli mancanti o incerti.",
        "properties": {
            "posizione": {"type": "string", "enum": s.zones},
            "direzione": {"type": "string", "enum": s.directions},
            "posa": {"type": "string", "enum": poses},
            "origine": {"type": "string", "enum": s.zones},
            "destinazione": {"type": "string", "enum": s.zones},
        },
    }


def tool_schemas(s: Settings, poses: list[str], *, stretch: bool = True) -> list[dict[str, Any]]:
    p = _params_schema(s, poses)
    tools = [
        {"type": "function", "name": "propose_skill",
         "description": "Proponi UNA abilità predefinita. Non muove il robot: crea un'azione in attesa di conferma.",
         "parameters": {"type": "object", "properties": {
             "skill": {"type": "string", "enum": SKILL_IDS}, "parametri": p,
             "evidenza_id": {"type": "string"},
             "lingua": {"type": "string", "enum": LANGS}},
                        "required": ["skill"]}},
        {"type": "function", "name": "confirm",
         "description": "Chiedi l'esecuzione dell'azione in attesa. SOLO dopo che l'operatore ha detto sì nel suo ultimo turno. "
                        "Il sistema verifica la trascrizione dell'operatore.",
         "parameters": {"type": "object", "properties": {"action_id": {"type": "string"}}, "required": ["action_id"]}},
        {"type": "function", "name": "cancel", "description": "Annulla l'azione in attesa.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "stop", "description": "Ferma subito il robot. Usala appena l'operatore dice stop.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "status", "description": "Stato del sistema, del robot e dei vassoi presunti.",
         "parameters": {"type": "object", "properties": {}}},
    ]
    if stretch:
        tools += [
            {"type": "function", "name": "propose_sequence",
             "description": "Proponi da 2 a 5 abilità in sequenza. Ogni movimento richiede la sua conferma.",
             "parameters": {"type": "object", "properties": {"passi": {"type": "array", "minItems": 2, "maxItems": 5,
                            "items": {"type": "object", "properties": {"skill": {"type": "string", "enum": SKILL_IDS},
                                                                       "parametri": p}, "required": ["skill"]}}},
                            "required": ["passi"]}},
            {"type": "function", "name": "save_pose",
             "description": "Salva la posizione attuale del robot con un nome, per vai_a_posa. Non muove il robot.",
             "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]}},
            {"type": "function", "name": "point_at_cup",
             "description": "Indica un bicchiere colorato visto dalla telecamera. Non muove finché l'operatore non conferma.",
             "parameters": {"type": "object", "properties": {"colore": {"type": "string"}}, "required": ["colore"]}},
            {"type": "function", "name": "touch_cup",
             "description": "Proponi di toccare il bordo di un bicchiere visto dalla telecamera calibrata.",
             "parameters": {"type": "object", "properties": {"colore": {"type": "string"}, "posizione": {"type": "string"}},
                            "required": ["colore"]}},
            {"type": "function", "name": "grab_cup",
             "description": "Proponi di prendere un bicchiere di carta con la pinza a semicerchio. Solo se la pinza è montata.",
             "parameters": {"type": "object", "properties": {"colore": {"type": "string"}, "posizione": {"type": "string"}},
                            "required": ["colore"]}},
            {"type": "function", "name": "place_cup",
             "description": "Proponi di posare il bicchiere tenuto dalla pinza.",
             "parameters": {"type": "object", "properties": {}}},
            {"type": "function", "name": "move_tip",
             "description": "Proponi uno spostamento rettilineo della punta. La distanza la legge il backend dalle parole dell'operatore.",
             "parameters": {"type": "object", "properties": {"direzione": {"type": "string"}}, "required": ["direzione"]}},
            {"type": "function", "name": "rotate_joint",
             "description": "Proponi la rotazione di un giunto. L'angolo lo legge il backend dalle parole dell'operatore.",
             "parameters": {"type": "object", "properties": {"giunto": {"type": "string"}, "verso": {"type": "string"}},
                            "required": ["giunto", "verso"]}},
            {"type": "function", "name": "look_cups",
             "description": "Elenca i bicchieri colorati visibili. Non muove il robot.",
             "parameters": {"type": "object", "properties": {}}},
            {"type": "function", "name": "start_inspection",
             "description": "Avvia l'ispezione qualità: prima il pezzo va alla telecamera, poi il verdetto, poi lo smistamento. Ogni movimento si conferma.",
             "parameters": {"type": "object", "properties": {"regola": {"type": "string"}}}},
            {"type": "function", "name": "propose_batch",
             "description": "Più spostamenti o rotazioni in una frase. Una sola conferma li esegue in ordine.",
             "parameters": {"type": "object", "properties": {"passi": {"type": "array"}}, "required": ["passi"]}},
        ]
    pid = {"type": "object", "properties": {"product_id": {"type": "string"}}}
    tools += [
        {"type": "function", "name": "analyse_model_pack",
         "description": "Smart Labelling: stato della specifica di un prodotto letta dal pacco modello (etichette, facce, "
                        "testi, campi incerti, approvazione). Le foto si caricano dall'interfaccia.", "parameters": pid},
        {"type": "function", "name": "validate_label_sheet", "description": "Esito dell'ultima verifica del foglio etichette.",
         "parameters": pid},
        {"type": "function", "name": "request_inspection", "description": "Stato del ciclo e ultima ispezione del pacco.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "explain_inspection",
         "description": "Spiegazione deterministica (it/en) di un'ispezione; senza id = l'ultima.",
         "parameters": {"type": "object", "properties": {"inspection_id": {"type": "string"}}}},
        {"type": "function", "name": "start_sorting_cycle",
         "description": "Verifica se un ciclo di smistamento può partire. NON lo avvia: l'operatore deve premere AVVIA CICLO.",
         "parameters": pid},
        {"type": "function", "name": "locate_object",
         "description": "Cerca un oggetto detto dall'operatore. Non muove il robot: restituisce la zona e un evidenza_id, oppure un chiarimento.",
         "parameters": {"type": "object", "properties": {
             "categoria": {"type": "string"},
             "colore": {"type": "string", "enum": ["rosso", "arancione", "giallo", "verde", "blu", "viola", "rosa",
                                                    "bianco", "nero", "grigio", "marrone", "trasparente"]},
             "testo": {"type": "string"}, "prodotto_id": {"type": "string"},
             "zona": {"type": "string", "enum": s.zones},
             "lingua": {"type": "string", "enum": LANGS}}}},
        {"type": "function", "name": "list_products", "description": "Elenco prodotti del catalogo e delle specifiche. Non muove il robot.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "learn_model_pack",
         "description": "Legge il pacco modello dalla telecamera. Lo stato resta PENDING: le tolleranze si approvano solo nell'interfaccia.",
         "parameters": {"type": "object", "properties": {"nome": {"type": "string"}, "lingua": {"type": "string", "enum": LANGS}}}},
        {"type": "function", "name": "check_label_sheet",
         "description": "Fotografa il foglio etichette e verifica. Non muove il robot.",
         "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "lingua": {"type": "string", "enum": LANGS}}}},
        {"type": "function", "name": "inspect_work_area",
         "description": "Ispeziona il pacco nell'area di lavoro. Non muove il robot.",
         "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "lingua": {"type": "string", "enum": LANGS}}}},
        {"type": "function", "name": "propose_labelling_cycle",
         "description": "Propone il ciclo di etichettatura. Non muove finché l'operatore non conferma a voce.",
         "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "lingua": {"type": "string", "enum": LANGS}},
                        "required": ["product_id"]}},
        {"type": "function", "name": "identify_zone",
         "description": "Legge una zona, o tutte, dalla telecamera. Non muove il robot. Il testo stampato si riporta come letto.",
         "parameters": {"type": "object", "properties": {"zona": {"type": "string", "enum": s.zones}}}},
        {"type": "function", "name": "find_product",
         "description": "Cerca un prodotto per tipo, nome, marca o colore. Non muove il robot. Se ce n'è più di uno, chiedi quale.",
         "parameters": {"type": "object", "properties": {
             "tipo": {"type": "string"}, "nome": {"type": "string"}, "marca": {"type": "string"}, "colore": {"type": "string"}}}},
        {"type": "function", "name": "check_labels",
         "description": "Conformità dell'etichetta nella zona. Non muove il robot. Senza specifica approvata non dire mai conforme.",
         "parameters": {"type": "object", "properties": {"zona": {"type": "string", "enum": s.zones}}, "required": ["zona"]}},
    ]
    return tools


def _remember_lingua(gate: Gate, args: dict[str, Any]) -> str:
    raw = str(args.get("lingua") or "").lower()
    if raw in LANGS:
        gate.operator_lang = raw
        return raw
    cur = getattr(gate, "operator_lang", None)
    return cur if cur in LANGS else "it"


def _labelling_tool(name: str, args: dict[str, Any], gate: Gate | None = None) -> dict[str, Any]:
    if LABELLING is None:
        return {"ok": False, "esito": "labelling_non_disponibile"}
    svc, cycle = LABELLING
    pid = args.get("product_id") or (svc.store.list()[-1]["product_id"] if svc.store.list() else None)
    if name in ("analyse_model_pack", "validate_label_sheet", "start_sorting_cycle") and not pid:
        return {"ok": False, "esito": "nessun_prodotto", "messaggio": "Nessun prodotto: carica le foto del pacco modello."}
    if name == "analyse_model_pack":
        s = svc.get(pid)
        return {"ok": True, "product_id": s.product_id, "product_name": s.product_name, "status": s.approval_status,
                "labels": [{"label_id": l.label_id, "face": l.face, "kind": l.kind, "region": l.region,
                            "inci": l.inci, "uncertain": l.uncertain, "tolerance": l.tolerance.model_dump()} for l in s.labels],
                "blockers": s.approval_blockers()}
    if name == "validate_label_sheet":
        r = svc.sheet_results.get(pid)
        return {"ok": True, "verified": bool(r), **({"decision": r["decision"], "explanation": r["explanation"]} if r else {})}
    if name == "request_inspection":
        return {"ok": True, "cycle": cycle.status(), "last": (svc.history(1) or [None])[0]}
    if name == "explain_inspection":
        iid = args.get("inspection_id") or ((svc.history(1) or [{}])[0].get("inspection_id"))
        if not iid:
            return {"ok": False, "esito": "nessuna_ispezione"}
        r = svc.inspection(iid)
        return {"ok": True, "inspection_id": iid, "decision": r["decision"], "explanation": r["explanation"],
                "findings": [{k: f[k] for k in ("code", "severity", "it", "en")} for f in r["findings"]]}
    if name == "start_sorting_cycle":
        problems = cycle.preflight(pid)
        return {"ok": not problems, "product_id": pid, "problems": problems,
                "istruzione": "Il ciclo parte solo quando l'operatore preme AVVIA CICLO nell'interfaccia e conferma la disposizione."}
    if name == "list_products":
        return {"ok": True, "prodotti": svc.store.list()}
    if name in ("learn_model_pack", "check_label_sheet", "inspect_work_area", "propose_labelling_cycle"):
        return _cycle_voice_tool(name, args, gate, svc, cycle)
    return {"ok": False, "esito": "strumento_sconosciuto"}


def _perception_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if PERCEPTION is None:
        return _no_camera()
    from ..perception.identify import find_in_rows
    rows = PERCEPTION.zone_rows()
    if name == "identify_zone":
        zona = args.get("zona")
        picked = [r for r in rows if r["zona"] == zona] if zona else rows
        return {"ok": True, "zone": picked, "sorgente": (picked or rows or [{}])[0].get("sorgente") if (picked or rows) else None}
    if name == "find_product":
        found = find_in_rows(rows, tipo=args.get("tipo"), nome=args.get("nome"), marca=args.get("marca"))
        return {"ok": found["stato"] == "match", **found, "zone_rows": rows}
    row = next((r for r in rows if r["zona"] == args.get("zona")), None)
    if row is None or row["stato"] != "pack" or not (row.get("prodotto") or {}).get("id"):
        return {"ok": True, "esito": "not_checkable",
                "messaggio": "Non posso giudicare la conformità: questo prodotto non ha una specifica approvata.",
                "zona": row}
    decision = (row.get("conformita") or {}).get("decisione")
    if decision is None:
        return {"ok": True, "esito": "not_checkable",
                "messaggio": "Non posso giudicare la conformità: questo prodotto non ha una specifica approvata.",
                "zona": row}
    return {"ok": True, "decisione": decision, "motivi": row["conformita"].get("motivi"), "zona": row}


def _no_camera() -> dict[str, Any]:
    return {"ok": False, "esito": "nessuna_telecamera",
            "messaggio": "Nessuna telecamera su questa stazione. Non invento ciò che non vedo."}


def _cycle_voice_tool(name: str, args: dict[str, Any], gate: Gate | None, svc: Any, cycle: Any) -> dict[str, Any]:
    if PERCEPTION is None or not getattr(PERCEPTION, "available", False):
        if name != "propose_labelling_cycle":
            return _no_camera()
    pid = args.get("product_id") or args.get("nome")
    if name == "learn_model_pack":
        frame = PERCEPTION.camera.get_frame() if PERCEPTION is not None else None
        if frame is None:
            return _no_camera() if PERCEPTION is None or not PERCEPTION.available else {
                "ok": False, "esito": "nessuna_immagine", "messaggio": "Nessun fotogramma recente."}
        import cv2
        ok, jpg = cv2.imencode(".jpg", frame[0])
        if not ok:
            return {"ok": False, "esito": "nessuna_immagine"}
        spec = svc.learn({"front": jpg.tobytes()}, args.get("nome"))
        return {"ok": True, "esito": "pending", "product_id": spec.product_id, "status": spec.approval_status,
                "istruzione": "Apri la schermata di approvazione. Le tolleranze le inserisce l'operatore."}
    if name == "check_label_sheet":
        frame = PERCEPTION.camera.get_frame() if PERCEPTION is not None else None
        if not pid or frame is None:
            return _no_camera() if frame is None else {"ok": False, "esito": "nessun_prodotto"}
        import cv2
        ok, jpg = cv2.imencode(".jpg", frame[0])
        return svc.verify_sheet(pid, jpg.tobytes()) if ok else {"ok": False, "esito": "nessuna_immagine"}
    if name == "inspect_work_area":
        frame = PERCEPTION.camera.get_frame() if PERCEPTION is not None else None
        if not pid or frame is None:
            return _no_camera() if frame is None else {"ok": False, "esito": "nessun_prodotto"}
        import cv2
        ok, jpg = cv2.imencode(".jpg", frame[0])
        if not ok:
            return {"ok": False, "esito": "nessuna_immagine"}
        result = svc.inspect(pid, {"front": jpg.tobytes()}, use_grok=False)
        return {"ok": True, "decision": result.get("decision"), "explanation": result.get("explanation"),
                "istruzione": "Nessun movimento. Se la decisione è REVIEW, non proporre di accettare."}
    if name == "propose_labelling_cycle":
        if gate is None:
            return {"ok": False, "esito": "gate_assente"}
        if not pid:
            return {"ok": False, "esito": "nessun_prodotto"}
        problems = cycle.preflight(str(pid))
        if problems:
            return {"ok": False, "esito": "preflight", "problems": problems, "messaggio": "; ".join(problems)}
        _remember_lingua(gate, args)
        conferme = {
            "it": (f"Avvio il ciclo per {pid}. Pacco a sinistra, centro e destra liberi. "
                   "Conforme verso destra, non conforme nel contenitore scarti, dubbio: non proseguo. Confermi?"),
            "en": (f"Starting the cycle for {pid}. Pack on the left, centre and right free. "
                   "Compliant goes right, non-compliant goes to the reject box, doubt: I do not continue. Do you confirm?"),
            "zh": f"开始 {pid} 的循环。包装在左边，中间和右边空着。合格到右边，不合格进废料箱，有疑问就不继续。确认吗？",
            "ja": f"{pid} のサイクルを開始します。左にパック、中央と右は空です。適合は右、不適合は廃棄箱、疑わしいときはそこで終わります。確認しますか？",
            "am": f"ለ {pid} ዑደቱን እጀምራለሁ። ጥቅሉ በግራ ነው። ተስማሚ ወደ ቀኝ፣ ያልተስማማ ወደ ውድቅ፣ ጥርጣሬ ቢኖር እቆማለሁ። ታረጋግጣለህ?",
        }
        return gate.propose_cycle(str(pid), conferme, source="grok")
    return {"ok": False, "esito": "strumento_sconosciuto"}


def build_prompt(s: Settings, gate: Gate) -> str:
    st = gate.world.snapshot()["oggetti_presunti"]
    state = json.dumps({"vassoi_presunti_in": st["vassoi_in_zona"], "zone_libere": st["zone_libere"],
                        "vassoio_sulla_forca": st["vassoio_sulla_forca"], "stato": gate.state.value}, ensure_ascii=False)
    wake = ""
    if s.wake_word:
        wake = (f"10. Parola di attivazione: proponi azioni solo se la frase contiene \"{s.wake_word}\". "
                "Stop e risposte sì/no valgono sempre anche senza.")
    cam = s.camera or {}
    if cam.get("available"):
        camera = "La telecamera di stazione c'è: usa locate_object per gli oggetti, e di' solo ciò che lo strumento riporta."
    else:
        camera = "NON ha telecamera: non vede né riconosce gli oggetti. Se ti chiedono di un oggetto, dillo e non indovinare. Le posizioni dei vassoi sono solo presunte dalla configurazione."
    return PROMPT_FILE.read_text().format(poses=", ".join(gate.reg.selectable_poses()), zones=", ".join(s.zones),
                                          directions=", ".join(s.directions), state=state, wake=wake, camera=camera)


async def dispatch(gate: Gate, name: str, args: dict[str, Any], *, source: str = "grok") -> dict[str, Any]:
    """Execute a tool call against the gate. Never raises."""
    if not isinstance(args, dict):
        return {"ok": False, "esito": "argomenti_non_validi"}
    try:
        from ..planning.task_schema import record_tool_call
        record_tool_call(name, args, getattr(gate, "operator_lang", None))
    except Exception:
        pass
    try:
        if name == "propose_skill":
            _remember_lingua(gate, args)
            return gate.propose(args.get("skill"), args.get("parametri") or args.get("params") or {}, source=source,
                                evidenza_id=args.get("evidenza_id"))
        if name == "confirm":
            return await gate.confirm(str(args.get("action_id") or ""), source=source)
        if name == "cancel":
            return gate.cancel(source=source)
        if name == "stop":
            return await gate.stop(f"grok_tool:{source}")
        if name == "status":
            st = gate.status()
            return {"ok": True, "gate": st["gate"], "pending": st["pending"], "world": st["world"],
                    "robot": {k: st["robot"][k] for k in ("connected", "moving", "safety", "robot_mode")}}
        if name == "propose_sequence":
            steps = [{"skill": p.get("skill"), "params": p.get("parametri") or {}} for p in (args.get("passi") or [])
                     if isinstance(p, dict)]
            return gate.propose_sequence(steps, source=source)
        if name == "save_pose":
            return await gate.save_pose(str(args.get("nome") or ""))
        if name == "propose_batch":
            return await gate.propose_batch(args.get("passi") or [], source=source)
        if name == "point_at_cup":
            return await gate.point_at_cup(args.get("colore") or args.get("colour"), source=source)
        if name == "touch_cup":
            return await gate.touch_cup(args.get("colore") or args.get("colour"), zone=args.get("posizione"), source=source)
        if name == "grab_cup":
            return await gate.grab_cup(args.get("colore") or args.get("colour"), zone=args.get("posizione"), source=source)
        if name == "place_cup":
            return await gate.place_cup(source=source)
        if name == "move_tip":
            return await gate.propose_move(args.get("direzione"), source=source)
        if name == "rotate_joint":
            return await gate.propose_rotate(args.get("giunto"), args.get("verso"), source=source)
        if name == "look_cups":
            found = gate.scan_cups()
            return {k: v for k, v in found.items() if k != "frame_id"}
        if name == "start_inspection":
            return gate.start_inspection(args.get("regola") or args.get("rule"), source=source)
        if name in ("analyse_model_pack", "validate_label_sheet", "request_inspection", "explain_inspection",
                    "start_sorting_cycle", "list_products", "learn_model_pack", "check_label_sheet",
                    "inspect_work_area", "propose_labelling_cycle"):
            if name in ("learn_model_pack", "check_label_sheet", "inspect_work_area"):
                return await asyncio.to_thread(_labelling_tool, name, args, gate)
            return _labelling_tool(name, args, gate)
        if name == "locate_object":
            _remember_lingua(gate, args)
            if PERCEPTION is None or not getattr(PERCEPTION, "available", False):
                return {"ok": False, "esito": "nessuna_telecamera",
                        "messaggio": "Questa stazione non ha telecamera. Il braccio sposta solo vassoi, non afferra gli oggetti."}
            return await asyncio.to_thread(PERCEPTION.locate, args)
        if name in ("identify_zone", "find_product", "check_labels"):
            return await asyncio.to_thread(_perception_tool, name, args)
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "esito": "errore_interno", "messaggio": str(e)}
    return {"ok": False, "esito": "strumento_sconosciuto", "messaggio": f"Strumento {name!r} non disponibile."}
