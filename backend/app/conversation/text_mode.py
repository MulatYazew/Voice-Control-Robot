"""TEXT MODE — NOT VOICE.

A conversation driven by typed text and the deterministic rules parser, so the
whole pipeline (validation, clarification, confirmation, execution, STOP) can
be exercised when Grok is unavailable. Development and evaluation only.
"""

from __future__ import annotations

from typing import Any

from ..safety.gate import Gate, GateState
from .rules import Intent, detect_language, parse

TEXT_MODE_LABEL = "TEXT MODE — NOT VOICE"
LANGS = ("it", "en", "zh", "ja", "am")

T = {
    "stopped": {
        "it": "Fermato. Nuovi movimenti bloccati fino a RIPRISTINA.",
        "en": "Stopped. New movements blocked until RESET.",
        "zh": "已停止。在按下复位之前不能做新的动作。",
        "ja": "停止しました。復帰するまで新しい動作はできません。",
        "am": "ቆሟል። እስኪመለስ ድረስ አዲስ እንቅስቃሴ የለም።",
    },
    "explicit": {
        "it": "Serve una conferma esplicita: sì o no?",
        "en": "I need an explicit confirmation: yes or no?",
        "zh": "需要明确的确认：是或否？",
        "ja": "はっきりした確認が必要です。はい、ですか、いいえですか？",
        "am": "ግልጽ ማረጋገጫ ያስፈልጋል፡ አዎ ወይስ አይ?",
    },
    "proceed": {"it": "Procedo.", "en": "Proceeding.", "zh": "开始执行。", "ja": "実行します。", "am": "እቀጥላለሁ።"},
    "bad_confirm": {"it": "Conferma non valida.", "en": "Confirmation not valid.", "zh": "确认无效。", "ja": "確認が無効です。", "am": "ማረጋገጫው ተቀባይነት የለውም።"},
    "cancelled": {
        "it": "Annullato. Il robot non si muove.",
        "en": "Cancelled. The robot will not move.",
        "zh": "已取消。机器人不会动。",
        "ja": "取り消しました。ロボットは動きません。",
        "am": "ተሰርዟል። ሮቦቱ አይንቀሳቀስም።",
    },
    "nothing": {"it": "Non c'è nulla da annullare.", "en": "There is nothing to cancel.", "zh": "没有要取消的动作。", "ja": "取り消す動作はありません。", "am": "የሚሰረዝ ነገር የለም።"},
    "refuse": {
        "it": "Non posso farlo. So solo: andare in una posa, indicare una zona, sollevare, appoggiare o spingere un vassoio.",
        "en": "I can't do that. I can only: go to a pose, point at a zone, lift, put down or push a tray.",
        "zh": "这个我做不到。我只能：去一个姿势、指向一个区域、抬起、放下或推动托盘。不能直接抓取物体。",
        "ja": "それはできません。できるのは、ポーズへ移動、ゾーンを指す、トレイを持ち上げる・置く・押すだけです。物を直接つかむことはできません。",
        "am": "ይህን ማድረግ አልችልም። ወደ አቀማመጥ መሄድ፣ ክልል ማመልከት፣ ትሪ ማንሳት፣ ማስቀመጥ ወይም መግፋት ብቻ ነው።",
    },
    "vague": {
        "it": "Non ho capito cosa spostare e dove. Vuoi sollevare, appoggiare o spingere un vassoio, oppure indicare una zona? Zone: sinistra, centro, destra.",
        "en": "I didn't understand what to move and where. Do you want to lift, put down or push a tray, or point at a zone? Zones: left, centre, right.",
        "zh": "我没听清要移动什么、移到哪里。区域是：左边、中间、右边。",
        "ja": "何をどこへ動かすか分かりません。ゾーンは左、中央、右です。",
        "am": "ምን ወደ የት እንደሚንቀሳቀስ አልገባኝም። ክልሎች፡ ግራ፣ መሃል፣ ቀኝ።",
    },
    "repeat": {"it": "Non ho capito. Puoi ripetere?", "en": "I didn't understand. Could you repeat?", "zh": "没听清。请再说一次。", "ja": "聞き取れませんでした。もう一度お願いします。", "am": "አልገባኝም። ትደግመዋለህ?"},
    "options": {"it": "Opzioni", "en": "Options", "zh": "选项", "ja": "選択肢", "am": "አማራጮች"},
    "no_camera": {
        "it": "Non ho una telecamera su questa stazione, quindi non posso vedere l'oggetto. Il braccio sposta solo vassoi, non afferra gli oggetti.",
        "en": "This station has no camera, so I cannot see the object. The arm only moves trays; it cannot grasp objects.",
        "zh": "这个工位没有相机，所以我看不见物体。机械臂只能移动托盘，不能直接抓取。",
        "ja": "このステーションにカメラはないので、物は見えません。アームはトレイだけを動かします。",
        "am": "በዚህ ጣቢያ ካሜራ የለም። ክንዱ ትሪ ብቻ ያንቀሳቅሳል፣ እቃ አይይዝም።",
    },
    "label_ui": {
        "it": "L'approvazione delle tolleranze resta sullo schermo. Posso spiegare lo stato, non inventare una tolleranza.",
        "en": "Tolerance approval stays on the screen. I can explain the status; I never invent a tolerance.",
        "zh": "公差要在界面上批准。我可以说明状态，但不会编造公差。",
        "ja": "公差の承認は画面で行います。状態は説明できますが、公差は作りません。",
        "am": "መቻቻል በማያ ገጹ ላይ ይጸድቃል። ሁኔታውን ማብራራት እችላለሁ።",
    },
}


class TextConversation:
    def __init__(self, gate: Gate) -> None:
        self.gate = gate
        self.context: dict[str, Any] | None = None   # last incomplete intent (for clarifications/corrections)
        self.lang = "it"

    def t(self, key: str) -> str:
        block = T[key]
        return block.get(self.lang) or block["en"]

    def _phrase(self, res: dict[str, Any]) -> str:
        if self.lang == "en":
            return res.get("frase_conferma_en") or res.get("frase_conferma") or ""
        if self.lang == "it":
            return res.get("frase_conferma") or ""
        return res.get(f"frase_conferma_{self.lang}") or res.get("frase_conferma") or ""

    async def handle(self, text: str, lang: str = "auto") -> dict[str, Any]:
        g = self.gate
        self.lang = lang if lang in LANGS else detect_language(text, self.lang)
        g.operator_lang = self.lang
        g.note_turn_committed("text")
        stopped = g.note_transcript(text, source="text")
        if stopped:
            self.context = None
            return self._out(text, Intent("stop"), self.t("stopped"), None)

        intent = parse(text, poses=g.s.poses, pending=g.pending is not None)

        if intent.kind == "confirm":
            if intent.verdict != "valida":
                return self._out(text, intent, self.t("explicit"), None)
            res = await g.confirm(g.pending.id if g.pending else None, source="rules")
            reply = self.t("proceed") if res.get("ok") else res.get("messaggio", self.t("bad_confirm"))
            return self._out(text, intent, reply, res)

        if intent.kind == "cancel":
            res = g.cancel(source="rules")
            self.context = None
            return self._out(text, intent, self.t("cancelled") if res.get("annullata") else self.t("nothing"), res)

        if intent.kind == "scene":
            self.context = None
            perceive = getattr(g, "perceive", None)
            answer = getattr(perceive, "answer_scene", None)
            if answer is None:
                return self._out(text, intent, self.t("no_camera"), {"ok": False, "esito": "nessuna_telecamera"})
            res = answer(intent.params)
            proposal = res.get("propose")
            if proposal:
                out = await self._propose(text, Intent("skill", skill=proposal["skill"], params=proposal["params"]))
                out["reply"] = f"{res.get('messaggio', '')} {out['reply']}".strip()
                return out
            return self._out(text, intent, res.get("messaggio") or self.t("repeat"), res)

        if intent.kind == "locate":
            self.context = None
            perceive = getattr(g, "perceive", None)
            if perceive is None or not getattr(perceive, "available", False):
                return self._out(text, intent, self.t("no_camera"), {"ok": False, "esito": "nessuna_telecamera"})
            found = perceive.locate(intent.params)
            if found.get("ok") and intent.params.get("azione") == "porta":
                dest = g.s.robot.get("workspace", {}).get("delivery_zone", "destra")
                return await self._propose(text, Intent("skill", skill="sposta_vassoio",
                                                        params={"origine": found["zona"], "destinazione": dest}))
            return self._out(text, intent, found.get("messaggio") or self.t("repeat"), found)

        if intent.kind in ("label_learn", "label_sheet", "label_cycle", "label_explain"):
            self.context = None
            hook = getattr(g, "label_voice", None)
            if intent.kind == "label_cycle" and hook is not None:
                res = hook(intent, self.lang)
                if res.get("ok"):
                    g.mark_question_asked(res.get("action_id"))
                    return self._out(text, intent, self._phrase(res), res)
                return self._out(text, intent, res.get("messaggio") or self.t("label_ui"), res)
            return self._out(text, intent, self.t("label_ui"), {"ok": False, "esito": "solo_interfaccia"})

        if intent.kind == "out_of_scope":
            self.context = None
            return self._out(text, intent, self.t("refuse"), None)

        if intent.kind == "clarify":
            if intent.params and self.context:     # answer to the previous question: merge
                skill = self.context["skill"]
                params = {**self.context["params"], **self._map_param(skill, intent.params)}
                return await self._propose(text, Intent("skill", skill=skill, params=params))
            self.context = None
            return self._out(text, intent, self.t("vague"), None)

        if intent.kind == "skill":
            if g.pending:  # a new command while one is pending = correction: replace it
                g.cancel(source="rules:correzione")
            return await self._propose(text, intent)

        return self._out(text, intent, self.t("repeat"), None)

    def _map_param(self, skill: str, p: dict[str, str]) -> dict[str, str]:
        z = p.get("posizione")
        if skill == "vai_a_posa" and z:
            return {"posa": f"{z}_sopra"}
        if skill == "spingi" and z and "posizione" in (self.context or {}).get("params", {}):
            return {"direzione": z}
        return p

    async def _propose(self, text: str, intent: Intent) -> dict[str, Any]:
        g = self.gate
        params = dict(intent.params)
        evidenza = params.pop("evidenza_id", None)
        res = g.propose(intent.skill, params, source="rules", evidenza_id=evidenza)
        if res.get("ok"):
            self.context = None
            g.mark_question_asked(res["action_id"])
            return self._out(text, intent, self._phrase(res), res)
        if res.get("esito") in ("richiesta_ambigua", "precondizione_non_soddisfatta"):
            self.context = {"skill": intent.skill, "params": intent.params}
            opts = res.get("opzioni_valide") or []
            q = res["messaggio"] + (f" {self.t('options')}: {', '.join(opts)}." if opts else "")
            return self._out(text, intent, q, res)
        return self._out(text, intent, res.get("messaggio", "Richiesta non valida."), res)

    def _out(self, text: str, intent: Intent, reply: str, result: dict[str, Any] | None) -> dict[str, Any]:
        self.gate.bus.publish("assistant_done", text=reply, mode=TEXT_MODE_LABEL)
        return {"mode": TEXT_MODE_LABEL, "input": text, "intent": intent.to_dict(), "reply": reply, "result": result,
                "gate": self.gate.state.value, "language": self.lang}

    @property
    def blocked(self) -> bool:
        return self.gate.state in (GateState.STOPPED, GateState.FAULT)
