import { useEffect, useState } from "react";
import type { Config } from "../services/types";

interface Scene {
  available: boolean;
  calibrated: boolean;
  source?: string;
  simulated?: boolean;
  label?: string | null;
  has_frame: boolean;
  candidates: { zona: string; colore: string; bbox: number[]; fonti: string[] }[];
}

interface ZoneRow {
  zona: string;
  stato: string;
  prodotto?: { id?: string | null; nome?: string | null; fonte?: string } | null;
  tipo?: { valore?: string; fonte?: string } | null;
  colore?: { nome?: string } | null;
  etichette?: { fronte?: string; retro?: string };
  conformita?: { decisione?: string | null } | null;
  sorgente?: string;
}

const PRESETS = ["demo", "defects", "ambiguous", "unseen", "empty"];

/** Live view. Arm 3 has no camera. A sim source is a rendered picture, never a real camera. */
export function PerceptionPanel({ config, gate }: { config: Config | null; gate?: string }) {
  const [scene, setScene] = useState<Scene | null>(null);
  const [rows, setRows] = useState<ZoneRow[]>([]);
  const [flash, setFlash] = useState<string>("");
  const [stamp, setStamp] = useState(0);
  const simulated = config?.camera_source === "sim" || scene?.source === "sim";
  const open = !!config?.camera_available || simulated;
  const moving = gate === "EXECUTING";

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const r = await fetch("/api/camera/scene");
        if (!stop && r.ok) setScene(await r.json());
      } catch { /* the panel stays on the last scene */ }
    };
    tick();
    const t = window.setInterval(tick, 1000);
    return () => { stop = true; window.clearInterval(t); };
  }, []);

  useEffect(() => {
    if (!simulated) return;
    let stop = false;
    const tick = async () => {
      const r = await fetch("/api/sim/identify").catch(() => null);
      if (!r || !r.ok || stop) return;
      const body = await r.json();
      const next: ZoneRow[] = body.zone || [];
      setRows((prev) => {
        const changed = next.find((row, i) => row.prodotto?.id && row.prodotto.id !== prev[i]?.prodotto?.id);
        if (changed) {
          setFlash(changed.zona);
          window.setTimeout(() => setFlash(""), 1600);
        }
        return next;
      });
    };
    tick();
    const t = window.setInterval(tick, 2000);
    const frame = window.setInterval(() => setStamp(Date.now()), 300);
    return () => { stop = true; window.clearInterval(t); window.clearInterval(frame); };
  }, [simulated]);

  useEffect(() => {
    if (!open || simulated) return;
    let stop = false;
    let stream: MediaStream | null = null;
    const send = async () => {
      if (stop || !stream) return;
      const video = document.createElement("video");
      video.srcObject = stream;
      await video.play().catch(() => undefined);
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      canvas.getContext("2d")?.drawImage(video, 0, 0);
      const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.7));
      if (blob) await fetch("/api/camera/frame", { method: "POST", body: blob }).catch(() => undefined);
    };
    navigator.mediaDevices?.getUserMedia({ video: true }).then((s) => {
      stream = s;
      const t = window.setInterval(send, 500);
      const done = () => { stop = true; window.clearInterval(t); s.getTracks().forEach((tr) => tr.stop()); };
      (send as unknown as { stop?: () => void }).stop = done;
    }).catch(() => undefined);
    return () => { stop = true; stream?.getTracks().forEach((tr) => tr.stop()); };
  }, [open, simulated]);

  if (!config) return null;
  if (!open) {
    return (
      <section className="card">
        <h2>Percezione</h2>
        <div className="empty">ARM 3 · nessuna telecamera. Il braccio sposta solo vassoi; non può vedere gli oggetti.</div>
      </section>
    );
  }

  const setPreset = (name: string) => {
    fetch("/api/sim/scene/preset", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name }) });
  };

  return (
    <section className="card">
      <h2>Camera / Station <span className="sub">{scene?.calibrated ? "zone calibrate" : scene?.source || ""}</span></h2>
      {simulated && <div className="sim-banner">SIMULATED CAMERA · SYNTHETIC PRODUCTS</div>}
      {simulated && <img className="frame" alt="stazione simulata" src={`/api/camera/frame.jpg?t=${stamp}`} />}
      {!simulated && scene?.has_frame && <img className="frame" alt="fotogramma" src={`/api/camera/frame?t=${Date.now()}`} />}
      {!simulated && !scene?.has_frame && <div className="empty">Nessun fotogramma recente. Senza immagine non c'è movimento.</div>}
      {rows.map((row) => (
        <div key={row.zona} className={flash === row.zona ? "zone-card flash" : "zone-card"}>
          <b>{row.zona}</b> {row.stato === "empty" ? "vuota" : (row.tipo?.valore || "tipo non letto")}
          {" · "}{row.prodotto?.nome || (row.stato === "pack" ? "prodotto sconosciuto" : "")}
          {" · "}{row.colore?.nome || ""}
          {" · "}{row.etichette?.fronte || ""} {row.etichette?.retro || ""}
          {" · "}{row.conformita?.decisione || "conformità non verificabile"}
          {row.prodotto?.fonte && <span className="langbadge">{row.prodotto.fonte}</span>}
          {row.sorgente === "SIMULATED CAMERA · SYNTHETIC PRODUCTS" && <span className="langbadge">SIMULATED</span>}
        </div>
      ))}
      {!simulated && (
        <ul className="cands">
          {(scene?.candidates ?? []).map((c, i) => (
            <li key={i}><b>{c.zona}</b> {c.colore} {(c.fonti ?? []).map((f) => <span key={f} className="langbadge">{f}</span>)}</li>
          ))}
        </ul>
      )}
      {simulated && (
        <div className="mic-actions">
          {PRESETS.map((name) => (
            <button key={name} type="button" disabled={moving} onClick={() => setPreset(name)}>{name}</button>
          ))}
          <button type="button" disabled={moving} onClick={() => fetch("/api/sim/scene/reset", { method: "POST" })}>reset</button>
        </div>
      )}
    </section>
  );
}
