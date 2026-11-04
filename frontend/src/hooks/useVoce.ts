import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { EventStream } from "../services/events";
import type { ChatLine, Config, Layout, Status, UiState, VoiceStatus } from "../services/types";

export interface Store {
  conn: "connecting" | "connected" | "reconnecting" | "disconnected";
  status: Status | null;
  config: Config | null;
  layout: Layout | null;
  catalogue: Record<string, any> | null;
  voice: VoiceStatus;
  voiceDetail: string;
  speaking: boolean;
  awaitingReply: boolean;
  chat: ChatLine[];
  joints: number[] | null;
  progress: { done: number; total: number } | null;
  lastTool: { name: string; result: any; ts: number } | null;
  lastReport: { kind: string; text: string; ts: number } | null;
  textMode: boolean;
  execStart: number | null;
}

const initial: Store = {
  conn: "connecting", status: null, config: null, layout: null, catalogue: null, voice: "off", voiceDetail: "",
  speaking: false, awaitingReply: false, chat: [], joints: null, progress: null, lastTool: null, lastReport: null,
  textMode: false, execStart: null,
};

type Action = { t: "event"; ev: any } | { t: "conn"; c: Store["conn"] } | { t: "textMode"; on: boolean };

let seq = 0;
const line = (l: Omit<ChatLine, "id" | "ts">, ts?: number): ChatLine => ({ ...l, id: `l${++seq}`, ts: ts ?? Date.now() / 1000 });

function reducer(s: Store, a: Action): Store {
  if (a.t === "conn") return { ...s, conn: a.c };
  if (a.t === "textMode") return { ...s, textMode: a.on };
  const ev = a.ev;
  switch (ev.type) {
    case "hello":
      return { ...s, status: ev, config: ev.config, layout: ev.layout, catalogue: ev.catalogue,
               voice: ev.voice?.status ?? s.voice, joints: ev.robot?.joints ?? s.joints };
    case "state": {
      const executing = ev.gate === "EXECUTING";
      return { ...s, status: { ...(s.status ?? {}), ...ev }, joints: s.joints ?? ev.robot?.joints,
               execStart: executing ? (s.execStart ?? Date.now()) : null,
               progress: executing ? s.progress : null };
    }
    case "robot":
      return s.status ? { ...s, status: { ...s.status, robot: ev.robot, world: ev.world } } : s;
    case "joints":
      return { ...s, joints: ev.joints };
    case "progress":
      return { ...s, progress: { done: ev.done, total: ev.total } };
    case "voice":
      return { ...s, voice: ev.status, voiceDetail: ev.detail ?? "" };
    case "vad":
      return { ...s, speaking: ev.speaking, awaitingReply: ev.speaking ? s.awaitingReply : true };
    case "transcript": {
      if (ev.role === "user") {
        const chat = [...s.chat];
        const last = chat[chat.length - 1];
        if (last && last.role === "user" && !last.final) chat[chat.length - 1] = { ...last, text: ev.text, final: !!ev.final };
        else if (!(ev.replay && chat.some((c) => c.ts === ev.ts))) chat.push(line({ role: "user", text: ev.text, final: !!ev.final, source: ev.source }, ev.ts));
        return { ...s, chat: chat.slice(-120), awaitingReply: true };
      }
      const chat = [...s.chat];
      const last = chat[chat.length - 1];
      if (last && last.role === "assistant" && !last.final) chat[chat.length - 1] = { ...last, text: last.text + ev.delta };
      else chat.push(line({ role: "assistant", text: ev.delta, final: false, source: ev.mode }));
      return { ...s, chat: chat.slice(-120), awaitingReply: false };
    }
    case "assistant_done": {
      const chat = [...s.chat];
      const last = chat[chat.length - 1];
      if (last && last.role === "assistant" && !last.final) chat[chat.length - 1] = { ...last, text: ev.text, final: true };
      else chat.push(line({ role: "assistant", text: ev.text, final: true, source: ev.mode }, ev.ts));
      return { ...s, chat: chat.slice(-120), awaitingReply: false };
    }
    case "tool":
      return { ...s, lastTool: { name: ev.name, result: ev.result, ts: ev.ts } };
    case "report":
      return { ...s, lastReport: { kind: ev.kind, text: ev.text, ts: ev.ts },
               chat: [...s.chat, line({ role: "system", text: ev.text, final: true, kind: ev.kind }, ev.ts)].slice(-120) };
    case "stop":
      return { ...s, chat: [...s.chat, line({ role: "system", text: `STOP ← ${ev.source}`, final: true, kind: "stop" }, ev.ts)] };
    default:
      return s;
  }
}

export function deriveUiState(s: Store, voiceLocal: string): UiState {
  if (s.conn === "reconnecting") return "RECONNECTING";
  if (s.conn !== "connected" || !s.status) return "DISCONNECTED";
  const g = s.status.gate;
  if (g === "STOPPED") return "STOPPED";
  if (g === "FAULT") return "FAULT";
  if (g === "EXECUTING") return "EXECUTING";
  if (g === "PENDING_CONFIRMATION") return "AWAITING_CONFIRMATION";
  if (s.voice === "reconnecting" || voiceLocal === "reconnecting") return "RECONNECTING";
  const now = Date.now() / 1000;
  if (s.lastReport?.kind === "completato" && now - s.lastReport.ts < 6) return "COMPLETED";
  if (s.lastTool && now - s.lastTool.ts < 20 &&
      ["richiesta_ambigua", "precondizione_non_soddisfatta"].includes(s.lastTool.result?.esito)) return "CLARIFICATION";
  if (s.speaking) return "LISTENING";
  if (s.awaitingReply) return "PROCESSING";
  return "READY";
}

export function useVoce() {
  const [store, dispatch] = useReducer(reducer, initial);
  const es = useRef<EventStream | null>(null);
  useEffect(() => {
    const stream = new EventStream((ev) => dispatch({ t: "event", ev }), (c) => dispatch({ t: "conn", c }));
    es.current = stream;
    stream.start();
    return () => stream.stop();
  }, []);
  const setTextMode = useCallback((on: boolean) => dispatch({ t: "textMode", on }), []);
  return useMemo(() => ({ store, setTextMode }), [store, setTextMode]);
}
