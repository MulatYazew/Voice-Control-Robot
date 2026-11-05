import { useEffect, useState } from "react";
import type { Config } from "../services/types";

interface Scene {
  available: boolean;
  calibrated: boolean;
  source?: string;
  has_frame: boolean;
  candidates: { zona: string; colore: string; bbox: number[]; fonti: string[] }[];
}

/** Live view for Arm 1. Arm 3 has no camera, and the panel says so. */
export function PerceptionPanel({ config }: { config: Config | null }) {
  const [scene, setScene] = useState<Scene | null>(null);
  const open = !!config?.camera_available;

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
    if (!open) return;
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
  }, [open]);

  if (!config) return null;
  if (!open) {
    return (
      <section className="card">
        <h2>Percezione</h2>
        <div className="empty">ARM 3 · nessuna telecamera. Il braccio sposta solo vassoi; non può vedere gli oggetti.</div>
      </section>
    );
  }
  return (
    <section className="card">
      <h2>Percezione <span className="sub">{scene?.calibrated ? "zone calibrate" : "zone non calibrate"}</span></h2>
      {scene?.has_frame && <img className="frame" alt="fotogramma" src={`/api/camera/frame?t=${Date.now()}`} />}
      {!scene?.has_frame && <div className="empty">Nessun fotogramma recente. Senza immagine non c'è movimento.</div>}
      <ul className="cands">
        {(scene?.candidates ?? []).map((c, i) => (
          <li key={i}><b>{c.zona}</b> {c.colore} {(c.fonti ?? []).map((f) => <span key={f} className="langbadge">{f}</span>)}</li>
        ))}
      </ul>
    </section>
  );
}
