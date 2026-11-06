import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "../i18n/vision";
import { calibration, cupsBackground, editCalibration, markerRecord, pointAtCup, pushFrame, scanCups, touchCup } from "../services/api";
import type { CameraStatus } from "../services/types";

const PUSH_MS = 220;
const STORE_KEY = "voce.webcam";

interface SeenCup { colore: string; zona: string; frac?: [number, number, number, number]; posto?: string }

export function CameraPanel({ camera, source, zoneLabels, canPoint, phoneUrls }: { camera: CameraStatus | null | undefined;
  source: "browser" | "url"; zoneLabels?: Record<string, string>; canPoint: boolean; phoneUrls?: string[] }) {
  const { t } = useT();
  const [cupsOn, setCupsOn] = useState(false);
  const [cups, setCups] = useState<SeenCup[] | null>(null);
  const [scanId, setScanId] = useState(0);
  const [detector, setDetector] = useState<string>("colour");
  const [calib, setCalib] = useState(false);
  const [cal, setCal] = useState<{ points?: unknown[]; ready?: boolean; error?: string | null; fit_rms_mm?: number;
    table_mm?: { width: number; depth: number };
    marker?: { seen?: boolean; center?: boolean; axis?: boolean } } | null>(null);
  const [layout, setLayout] = useState("");
  useEffect(() => {
    if (!cupsOn) return;
    let alive = true;
    const run = async () => {
      try {
        const r = await scanCups();
        if (!alive) return;
        if (r.ok) { setCups(r.cups); setLayout(r.layout ?? ""); setScanId(r.frame_id); setDetector(r.detector ?? "colour"); setErr(""); } else { setCups(null); setLayout(""); setErr(r.messaggio ?? ""); }
      } catch { /* next tick */ }
    };
    run();
    const h = window.setInterval(run, 450);
    return () => { alive = false; window.clearInterval(h); };
  }, [cupsOn]);
  useEffect(() => {
    if (!calib) return;
    let alive = true;
    const run = async () => {
      try {
        const r = await calibration();
        if (alive && r.ok) setCal(r);
      } catch { /* next tick */ }
    };
    run();
    const h = window.setInterval(run, 2000);
    return () => { alive = false; window.clearInterval(h); };
  }, [calib]);
  const point = async (colour: string) => {
    const r = await pointAtCup(colour).catch((e) => ({ ok: false, messaggio: String(e) }));
    if (!r.ok) setErr(r.messaggio ?? r.esito ?? "");
  };
  const touch = async (colour: string) => {
    const r = await touchCup(colour).catch((e) => ({ ok: false, messaggio: String(e) }));
    if (!r.ok) setErr(r.messaggio ?? r.esito ?? "");
  };
  const recordMark = async (which: "center" | "axis") => {
    const r = await markerRecord(which).catch((e) => ({ ok: false, messaggio: String(e) }));
    if (!r.ok) setErr(r.messaggio ?? r.esito ?? "");
    else { setCal(r); setErr(""); }
  };
  const video = useRef<HTMLVideoElement>(null);
  const preview = useRef<HTMLImageElement>(null);
  const view = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState({ l: 0, t: 0, w: 0, h: 0 });
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<number | null>(null);
  const busy = useRef(false);
  const [on, setOn] = useState(false);
  const [err, setErr] = useState("");
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string>(() => localStorage.getItem(`${STORE_KEY}.device`) ?? "");
  const [res, setRes] = useState("");
  const [tick, setTick] = useState(0);

  const stereoSize = async (track: MediaStreamTrack) => {
    const wide: MediaTrackConstraints[] = [
      { width: { ideal: 3200 }, height: { ideal: 1200 } },
      { width: { ideal: 2560 }, height: { ideal: 720 } },
      { width: { ideal: 1600 }, height: { ideal: 600 } },
      { width: { ideal: 1280 }, height: { ideal: 480 } },
    ];
    for (const c of wide) {
      try { await track.applyConstraints(c); } catch { /* this size is not in the driver's list */ }
      const s = track.getSettings();
      if ((s.width ?? 0) >= (s.height ?? 1) * 1.8) return;
    }
  };

  const stop = useCallback(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((tr) => tr.stop());
    stream.current = null;
    setOn(false);
    setRes("");
    localStorage.setItem(STORE_KEY, "off");
  }, []);

  const start = useCallback(async (id = deviceId) => {
    setErr("");
    try {
      stream.current?.getTracks().forEach((tr) => tr.stop());
      let list = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
      if (list.some((d) => !d.label)) {
        const probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        probe.getTracks().forEach((tr) => tr.stop());
        list = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
      }
      const elp = list.find((d) => /elp|stereo|usb3dgs|megapixel usb/i.test(d.label));
      const chosen = list.some((d) => d.deviceId === id) ? id : (elp?.deviceId || list[0]?.deviceId || "");
      if (chosen && chosen !== id) setDeviceId(chosen);
      const videoConstraints: MediaTrackConstraints = {
        deviceId: chosen ? { exact: chosen } : undefined,
        width: { ideal: 2560 }, height: { ideal: 720 }, aspectRatio: { ideal: 32 / 9 },
      };
      const s = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
      const track = s.getVideoTracks()[0];
      if (track) await stereoSize(track);
      stream.current = s;
      if (video.current) { video.current.srcObject = s; await video.current.play().catch(() => undefined); }
      const st = track?.getSettings();
      setRes(st?.width && st?.height ? `${st.width}×${st.height}` : "");
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput"));
      setOn(true);
      localStorage.setItem(STORE_KEY, "on");
      const canvas = document.createElement("canvas");
      if (timer.current) window.clearInterval(timer.current);
      timer.current = window.setInterval(() => {
        const v = video.current;
        if (!v || busy.current || v.videoWidth === 0) return;
        const scale = Math.min(1, 640 / v.videoWidth);
        canvas.width = Math.round(v.videoWidth * scale);
        canvas.height = Math.round(v.videoHeight * scale);
        canvas.getContext("2d")?.drawImage(v, 0, 0, canvas.width, canvas.height);
        busy.current = true;
        canvas.toBlob((b) => {
          if (!b) { busy.current = false; return; }
          pushFrame(b).catch(() => undefined).finally(() => { busy.current = false; });
        }, "image/jpeg", 0.55);
      }, PUSH_MS);
    } catch (e: any) {
      setErr(`${t("cam_denied")}: ${e?.message ?? e}`);
      setOn(false);
    }
  }, [deviceId, t]);

  useEffect(() => {
    if (source === "browser" && localStorage.getItem(STORE_KEY) === "on") start();
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  useEffect(() => {
    if (source === "browser" && on) return;
    const h = window.setInterval(() => setTick((x) => x + 1), 300);
    return () => window.clearInterval(h);
  }, [source, on]);

  useEffect(() => {
    const place = () => {
      const box = view.current;
      const live = on && video.current && video.current.videoWidth ? video.current : null;
      const still = preview.current && preview.current.naturalWidth ? preview.current : null;
      const el = live ?? still;
      if (!box || !el) return;
      const nw = live ? live.videoWidth : still!.naturalWidth;
      const nh = live ? live.videoHeight : still!.naturalHeight;
      const r = box.getBoundingClientRect();
      const scale = Math.min(r.width / nw, r.height / nh);
      const dw = nw * scale, dh = nh * scale;
      setFit({ l: (r.width - dw) / 2, t: (r.height - dh) / 2, w: dw, h: dh });
    };
    place();
    window.addEventListener("resize", place);
    const id = window.setInterval(place, 500);
    return () => { window.removeEventListener("resize", place); window.clearInterval(id); };
  }, [on, cupsOn, calib, tick, scanId]);

  const age = camera?.frame?.age_s;
  return (
    <section className="card camera">
      <div className="card-head">
        <h2>{t("camera")} <span className="sub">{source === "browser" ? t("cam_browser") : t("cam_url")}{res ? ` · ${res}` : ""}</span></h2>
        {camera?.frame && <span className={`badge dot ${age !== undefined && age < 2 ? "ok" : "warn"}`}>
          {t("cam_age", { age: (age ?? 0).toFixed(1) })}</span>}
      </div>
      <div className="cam-view" ref={view}>
        {source === "browser" ? (
          <video ref={video} muted playsInline autoPlay className={on ? "" : "hidden"} />
        ) : null}
        {((source === "url" && camera?.frame) || (source === "browser" && !on && camera?.frame)) &&
          <img ref={preview} src={`/api/camera/frame.jpg?t=${tick}`} alt={t("camera")} />}
        {cupsOn && !calib && fit.w > 0 && (
          <div className="cup-live" style={{ left: fit.l, top: fit.t, width: fit.w, height: fit.h }}>
            {(cups ?? []).map((c, i) => c.frac && (
              <i key={i} className={`c-${c.colore}`} style={{
                left: `${c.frac[0] * 100}%`, top: `${c.frac[1] * 100}%`,
                width: `${c.frac[2] * 100}%`, height: `${c.frac[3] * 100}%` }} />
            ))}
          </div>
        )}
        {((source === "browser" && !on && !camera?.frame) || (source === "url" && !camera?.frame)) &&
          <div className="cam-empty">{source === "url" ? t("cam_url_hint") : t("cam_none")}</div>}
      </div>
      {source === "browser" && (
        <div className="cam-actions">
          {on ? <button onClick={stop}>{t("cam_stop")}</button> : <button className="primary" onClick={() => start()}>{t("cam_start")}</button>}
          {devices.length > 1 && (
            <select value={deviceId} aria-label={t("cam_device")} onChange={(e) => {
              setDeviceId(e.target.value); localStorage.setItem(`${STORE_KEY}.device`, e.target.value); start(e.target.value);
            }}>
              {devices.map((d, i) => <option key={d.deviceId} value={d.deviceId}>{d.label || `${t("cam_device")} ${i + 1}`}</option>)}
            </select>
          )}
        </div>
      )}
      <label className="switch cups-switch">
        <input type="checkbox" checked={calib} onChange={(e) => { setCalib(e.target.checked); if (e.target.checked) setCupsOn(false); }} />
        <span>{t("calib")}</span>
        {calib && cal && <span className="sub">{cal.ready ? t("calib_ready") : t("calib_points", { n: cal.points?.length ?? 0 })}
          {cal.fit_rms_mm != null ? ` · ${t("calib_err", { mm: cal.fit_rms_mm })}` : ""}</span>}
      </label>
      {calib && (
        <div className="cup-tools">
          <img className="marker-sheet" src="/api/calibration/marker.png" alt={t("calib")} />
          <button onClick={() => recordMark("center")}>{t("marker_center")}{cal?.marker?.center ? " ✓" : ""}</button>
          <button onClick={() => recordMark("axis")}>{t("marker_axis")}{cal?.marker?.axis ? " ✓" : ""}</button>
          <button onClick={async () => { const r = await editCalibration("clear"); if (r.ok) setCal(r); }}>{t("calib_clear")}</button>
          <span className="sub">{cal?.marker?.seen ? t("marker_seen") : t("marker_missing")}</span>
        </div>
      )}
      {calib && cal?.table_mm && <div className="hint">{t("table_reach", { w: cal.table_mm.width, d: cal.table_mm.depth })}</div>}
      {calib && <div className="hint">{cal?.error || t("calib_hint")}</div>}
      <label className="switch cups-switch">
        <input type="checkbox" checked={cupsOn} onChange={(e) => { setCupsOn(e.target.checked); if (e.target.checked) setCalib(false); if (!e.target.checked) setCups(null); }} />
        <span>{t("cups")}</span>
      </label>
      {cupsOn && (
        <div className="cup-tools">
          <span className="sub">{detector === "background" ? t("det_background") : t("det_colour")}</span>
          <button onClick={async () => { const r = await cupsBackground(); if (!r.ok) setErr(r.messaggio ?? ""); else setDetector(r.detector); }}>
            {t("cups_bg")}</button>
          {detector === "background" && <button onClick={async () => { const r = await cupsBackground(true); setDetector(r.detector ?? "colour"); }}>
            {t("cups_bg_clear")}</button>}
        </div>
      )}
      {cupsOn && detector !== "background" && <div className="hint">{t("cups_bg_hint")}</div>}
      {cupsOn && <div className="hint">{t("touch_hint")}</div>}
      {phoneUrls && phoneUrls.length > 0 && <div className="hint">{t("phone_open")} {phoneUrls.join(" · ")}</div>}
      {cupsOn && layout && <div className="hint">{layout}</div>}
      {cupsOn && cups && (cups.length === 0 ? <div className="empty">{t("cups_none")}</div> : (
        <ul className="cup-list">
          {cups.map((c, i) => (
            <li key={i}>
              <span className={`cup-dot c-${c.colore}`} aria-hidden />
              <b>{t(`colour_${c.colore}` as any)}</b>
              <span className="sub">{zoneLabels?.[c.zona] ? `${zoneLabels[c.zona]} · ` : ""}{c.zona}{c.posto ? ` · ${c.posto}` : ""}</span>
              <span className="cup-actions">
                <button disabled={!canPoint} onClick={() => point(c.colore)}>{t("point")}</button>
                <button disabled={!canPoint} onClick={() => touch(c.colore)}>{t("touch")}</button>
              </span>
            </li>
          ))}
        </ul>
      ))}
      {err && <div className="hint bad">{err}</div>}
      {camera?.error && <div className="hint bad">{camera.error}</div>}
    </section>
  );
}
