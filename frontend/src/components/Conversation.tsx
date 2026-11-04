import { useEffect, useRef, useState } from "react";
import { textCommand } from "../services/api";
import type { ChatLine } from "../services/types";

export function Conversation({ chat, textMode, setTextMode, grokAvailable }: {
  chat: ChatLine[]; textMode: boolean; setTextMode: (on: boolean) => void; grokAvailable: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [engine, setEngine] = useState<"rules" | "grok">("rules");
  const [err, setErr] = useState("");
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [chat]);

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setText(""); setErr("");
    try { await textCommand(t, engine); } catch (e: any) { setErr(e.message); }
  };

  return (
    <section className="card conv">
      <div className="card-head">
        <h2>Conversazione</h2>
        <label className="switch">
          <input type="checkbox" checked={textMode} onChange={(e) => setTextMode(e.target.checked)} />
          <span>Modalità testo</span>
        </label>
      </div>
      <div className="chat" ref={box} aria-live="polite">
        {chat.length === 0 && <div className="empty">Avvia l'assistente e prova: «Solleva il vassoio a sinistra».</div>}
        {chat.map((l) => (
          <div key={l.id} className={`line ${l.role} ${l.final ? "" : "partial"} ${l.kind ?? ""} ${l.source?.startsWith("TEXT") || l.source === "text" ? "textsrc" : ""}`}>
            {l.role === "system" ? <span className="sys">{l.text}</span> : l.text}
          </div>
        ))}
      </div>
      {textMode && (
        <div className="textmode">
          <div className="textmode-banner">TEXT MODE — NOT VOICE · solo sviluppo e valutazione</div>
          <form onSubmit={send}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Scrivi un comando…" maxLength={300} />
            <select value={engine} onChange={(e) => setEngine(e.target.value as any)}>
              <option value="rules">regole</option>
              <option value="grok" disabled={!grokAvailable}>Grok (testo)</option>
            </select>
            <button type="submit">Invia</button>
          </form>
          {err && <div className="hint bad">{err}</div>}
        </div>
      )}
    </section>
  );
}
