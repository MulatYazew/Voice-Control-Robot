import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n/strings";
import { Camera, L, sampleBlob } from "../services/labelling";

const FACES = ["front", "back", "left", "right", "cap", "bottom"];
const FACE_IT: Record<string, string> = { front: "fronte", back: "retro", left: "sinistra", right: "destra", cap: "tappo", bottom: "fondo" };

export function Decision({ d }: { d?: string | null }) {
  const t = useT();
  if (!d) return null;
  const cls = d === "COMPLIANT" ? "ok" : d === "NON_COMPLIANT" ? "bad" : "warn";
  return <span className={`decision ${cls}`}>{t(d as any)}</span>;
}

/** Shared browser camera (one stream for the whole screen). */
function useCamera() {
  const video = useRef<HTMLVideoElement>(null);
  const cam = useRef(new Camera());
  const [on, setOn] = useState(false);
  const [err, setErr] = useState("");
  const start = async () => {
    try { await cam.current.start(video.current!); setOn(true); setErr(""); } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => () => cam.current.stop(), []);
  const snap = () => cam.current.snapshot(video.current!);
  return { video, on, err, start, snap };
}

function CameraBox({ cam }: { cam: ReturnType<typeof useCamera> }) {
  const t = useT();
  return (
    <div className="cambox">
      <video ref={cam.video} playsInline muted className={cam.on ? "" : "hidden"} />
      {!cam.on && <button onClick={cam.start}>{t("start_camera")}</button>}
      {cam.err && <div className="hint bad">{cam.err}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ learn ---
function LearnCard({ cam, samples, onLearned }: { cam: ReturnType<typeof useCamera>; samples: any; onLearned: (s: any) => void }) {
  const t = useT();
  const [photos, setPhotos] = useState<Record<string, Blob>>({});
  const [face, setFace] = useState("front");
  const [name, setName] = useState("");
  const [sample, setSample] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const add = (f: string, b: Blob) => setPhotos((p) => ({ ...p, [f]: b }));
  const analyse = async () => {
    setBusy(true); setErr("");
    try {
      const spec = sample && !Object.keys(photos).length ? await L.learnSample(sample, name || sample) : await L.learn(photos, name);
      onLearned(spec); setPhotos({});
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <section className="card">
      <h2>{t("learn_new")}</h2>
      <p className="hint">{t("learn_help")}</p>
      <CameraBox cam={cam} />
      <div className="row">
        <select value={face} onChange={(e) => setFace(e.target.value)}>
          {FACES.map((f) => <option key={f} value={f}>{f} · {FACE_IT[f]}</option>)}
        </select>
        <button disabled={!cam.on} onClick={async () => add(face, await cam.snap())}>{t("capture_face")}</button>
        <label className="upload">{t("or_upload")}
          <input type="file" accept="image/*" onChange={(e) => e.target.files?.[0] && add(face, e.target.files[0])} />
        </label>
      </div>
      <div className="thumbs">
        {Object.entries(photos).map(([f, b]) => (
          <figure key={f}><img src={URL.createObjectURL(b)} alt={f} /><figcaption>{f}</figcaption></figure>
        ))}
      </div>
      <div className="row">
        <select value={sample} onChange={(e) => setSample(e.target.value)}>
          <option value="">{t("use_sample")}…</option>
          {Object.keys(samples ?? {}).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <input placeholder={t("product_name_hint")} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" disabled={busy || (!Object.keys(photos).length && !sample)} onClick={analyse}>
          {busy ? t("analysing") : t("analyse")}
        </button>
      </div>
      {err && <div className="hint bad">{err}</div>}
    </section>
  );
}

// ------------------------------------------------------------------- spec ---
export function SpecView({ pid, onChanged }: { pid: string; onChanged?: () => void }) {
  const t = useT();
  const [spec, setSpec] = useState<any>(null);
  const [tol, setTol] = useState<Record<string, { position_pct: string; angle_deg: string }>>({});
  const [conf, setConf] = useState<Record<string, string[]>>({});
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [err, setErr] = useState("");
  const load = () => L.product(pid).then((s) => {
    setSpec(s);
    setTol(Object.fromEntries(s.labels.map((l: any) => [l.label_id, {
      position_pct: l.tolerance.position_pct?.toString() ?? "", angle_deg: l.tolerance.angle_deg?.toString() ?? "" }])));
    setConf({}); setEdits({});
  });
  useEffect(() => { load(); }, [pid]);
  if (!spec) return null;
  const approved = spec.approval_status === "approved";
  const approve = async () => {
    if (!window.confirm(t("approve_confirm"))) return;
    setErr("");
    try {
      await L.approve(pid, {
        operator_confirmed: true, operator: "operatore",
        tolerances: Object.fromEntries(Object.entries(tol).filter(([, v]) => v.position_pct && v.angle_deg)
          .map(([k, v]) => [k, { position_pct: Number(v.position_pct), angle_deg: Number(v.angle_deg) }])),
        confirmed: conf, edits,
      });
      await load(); onChanged?.();
    } catch (e: any) { setErr(e.message); }
  };
  const edit = (lid: string, k: string, v: any) => setEdits((e) => ({ ...e, [lid]: { ...(e[lid] ?? {}), [k]: v } }));
  return (
    <section className="card spec">
      <div className="card-head">
        <h2>{t("spec")} · {spec.product_name ?? spec.product_id} <span className="sub">v{spec.version}</span></h2>
        <span className={`decision ${approved ? "ok" : "warn"}`}>{approved ? t("status_approved") : t("status_pending")}</span>
      </div>
      <div className="hint">{[spec.brand, spec.format, spec.packaging_type].filter(Boolean).join(" · ")}
        {spec.source?.grok && ` · ${spec.source.grok.model} ${spec.source.grok.latency_s}s`}</div>
      {spec.notes?.length > 0 && <ul className="notes">{spec.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
      <div className="faces">
        {Object.entries(spec.faces).map(([f, img]: any) => (
          <figure key={f}><img src={L.file(spec.product_id, spec.version, img)} alt={f} /><figcaption>{f}</figcaption></figure>
        ))}
      </div>
      {spec.labels.map((l: any) => (
        <div key={l.label_id} className="label-spec">
          <div className="ls-head">
            <b>{t("label")} {l.label_id}</b>
            <span className="param"><b>{t("face")}</b> {l.face}</span>
            <span className="param"><b>{t("kind")}</b>
              {approved ? l.kind : (
                <select value={edits[l.label_id]?.kind ?? l.kind} onChange={(e) => edit(l.label_id, "kind", e.target.value)}>
                  <option value="front_brand">front_brand</option><option value="ingredients_legal">ingredients_legal</option>
                  <option value="other">other</option>
                </select>)}
            </span>
            <span className="param"><b>{t("region")}</b> {l.region} · {Object.entries(l.position).map(([k, v]: any) => `${k} ${(+v).toFixed(2)}`).join(" ")}</span>
          </div>
          {l.reference_crop && <img className="crop" src={L.file(spec.product_id, spec.version, l.reference_crop)} alt="" />}
          <div className="kv2">
            <div><span>{t("text")}</span>
              {approved ? <pre>{l.reference_text}</pre> :
                <textarea rows={4} value={edits[l.label_id]?.reference_text ?? l.reference_text}
                  onChange={(e) => edit(l.label_id, "reference_text", e.target.value)} />}
            </div>
            {l.grok_text && <div><span>{t("grok_read")}</span><pre>{l.grok_text}</pre></div>}
            {l.inci && <div><span>{t("inci")} ({l.inci.length})</span><pre>{l.inci.join(", ")}</pre></div>}
            {l.legal_text && <div><span>{t("legal")}</span><pre>{l.legal_text}</pre></div>}
            {(l.product_name || l.brand) && <div><span>Brand / name</span><pre>{[l.brand, l.product_name].filter(Boolean).join(" — ")}</pre></div>}
          </div>
          {!approved && l.uncertain.length > 0 && (
            <div className="uncertain"><b>{t("uncertain")}:</b>
              {l.uncertain.map((u: string) => (
                <label key={u}><input type="checkbox" checked={conf[l.label_id]?.includes(u) ?? false}
                  onChange={(e) => setConf((c) => ({ ...c, [l.label_id]: e.target.checked
                    ? [...(c[l.label_id] ?? []), u] : (c[l.label_id] ?? []).filter((x) => x !== u) }))} /> {t("confirm_field")} {u}</label>
              ))}
            </div>)}
          <div className="tol"><b>{t("tolerance")}</b>
            {approved ? <span>±{l.tolerance.position_pct}% · ±{l.tolerance.angle_deg}° ({l.tolerance.set_by})</span> : (<>
              <label>{t("tol_pos")} <input type="number" step="0.5" min="0.5" max="50" value={tol[l.label_id]?.position_pct ?? ""}
                onChange={(e) => setTol((x) => ({ ...x, [l.label_id]: { ...x[l.label_id], position_pct: e.target.value } }))} /></label>
              <label>{t("tol_ang")} <input type="number" step="0.5" min="0.5" max="45" value={tol[l.label_id]?.angle_deg ?? ""}
                onChange={(e) => setTol((x) => ({ ...x, [l.label_id]: { ...x[l.label_id], angle_deg: e.target.value } }))} /></label>
            </>)}
          </div>
        </div>
      ))}
      {!approved && (<>
        {spec.approval_blockers?.length > 0 && <div className="hint">{t("blockers")}: {spec.approval_blockers.join(" · ")}</div>}
        <button className="primary" onClick={approve}>{t("approve")}</button>
      </>)}
      {err && <div className="hint bad">{err}</div>}
    </section>
  );
}

// ------------------------------------------------------------- inspection ---
export function InspectionView({ r }: { r: any }) {
  const t = useT();
  const lang = document.documentElement.lang === "en" ? "en" : "it";
  if (!r) return null;
  return (
    <div className="inspection">
      <div className="insp-head"><Decision d={r.decision} /> <span className="sub">{r.inspection_id} · v{r.spec_version} · {r.duration_s}s</span></div>
      <p className="explain">{r.explanation?.[lang]}</p>
      <ul className="findings">
        {r.findings.filter((f: any) => f.code !== "ok").map((f: any, i: number) => (
          <li key={i} className={f.severity === "NON_COMPLIANT" ? "bad" : f.severity === "REVIEW_REQUIRED" ? "warn" : "ok"}>{f[lang]}</li>))}
      </ul>
      <div className="faces">
        {Object.entries(r.labels ?? {}).map(([lid, info]: any) => (
          <figure key={lid}><img src={L.inspFile(r.inspection_id, `label_${lid}.jpg`)} alt={lid}
            onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} />
            <figcaption>{lid} · {info.offset_pct != null && `Δ${info.offset_pct}% · ${info.angle_deg}°`}</figcaption>
            {info.ocr?.text && <details><summary>{t("ocr_read")}</summary><pre>{info.ocr.text}</pre></details>}</figure>))}
      </div>
      {r.grok_opinion && <p className="hint">Grok: {r.grok_opinion.verdict} {r.grok_opinion[`reason_${lang}`] ?? ""}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ main ---
export function SmartLabelling() {
  const t = useT();
  const cam = useCamera();
  const [samples, setSamples] = useState<any>({});
  const [products, setProducts] = useState<any[]>([]);
  const [pid, setPid] = useState("");
  const [sheet, setSheet] = useState<any>(null);
  const [sheetSample, setSheetSample] = useState("sheet");
  const [cycle, setCycle] = useState<any>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [layout, setLayout] = useState(false);
  const [insp, setInsp] = useState<any>(null);
  const [caseSel, setCaseSel] = useState("correct_1");
  const [sampleProd, setSampleProd] = useState("");
  const [err, setErr] = useState("");
  const refresh = () => L.products().then((p) => { setProducts(p); if (!pid && p.length) setPid(p[p.length - 1].product_id); });
  useEffect(() => { L.samples().then(setSamples); refresh(); }, []);
  useEffect(() => {
    const tm = window.setInterval(async () => {
      const c = await L.cycle().catch(() => null);
      setCycle(c);
      if (c?.inspection_id && c.inspection_id !== insp?.inspection_id) setInsp(await L.inspection(c.inspection_id));
    }, 1000);
    return () => window.clearInterval(tm);
  }, [insp?.inspection_id]);
  useEffect(() => { if (pid) L.preflight(pid).then((r) => setProblems(r.problems)); }, [pid, sheet, cycle?.phase]);
  // simulation: the sample feed follows the learned product when it came from the dataset
  useEffect(() => { const base = pid.replace(/_\d+$/, ""); if (samples[base]) setSampleProd(base); }, [pid, samples]);
  const prod = products.find((p) => p.product_id === pid);
  const run = async (fn: () => Promise<any>) => { setErr(""); try { return await fn(); } catch (e: any) { setErr(e.message); } };
  const lang = document.documentElement.lang === "en" ? "en" : "it";
  const sp = sampleProd || Object.keys(samples)[0] || "";
  const busyPhase = cycle && !["IDLE", "DONE", "ABORTED"].includes(cycle.phase);

  return (
    <main className="grid">
      <div className="col">
        <LearnCard cam={cam} samples={samples} onLearned={(s) => { refresh(); setPid(s.product_id); }} />
        {products.length > 0 && (
          <div className="row"><b>{t("select_product")}</b>
            <select value={pid} onChange={(e) => setPid(e.target.value)}>
              {products.map((p) => <option key={p.product_id} value={p.product_id}>{p.product_name ?? p.product_id} · v{p.version} · {p.approval_status}</option>)}
            </select></div>)}
        {pid && <SpecView key={pid} pid={pid} onChanged={refresh} />}
      </div>
      <div className="col">
        <section className="card">
          <h2>{t("sheet")}</h2>
          <div className="row">
            <button disabled={!cam.on || prod?.approval_status !== "approved"}
              onClick={() => run(async () => setSheet(await L.sheet(pid, await cam.snap())))}>{t("capture_face")}</button>
            <label className="upload">{t("or_upload")}<input type="file" accept="image/*" disabled={prod?.approval_status !== "approved"}
              onChange={(e) => e.target.files?.[0] && run(async () => setSheet(await L.sheet(pid, e.target.files![0])))} /></label>
            <select value={sheetSample} onChange={(e) => setSheetSample(e.target.value)}>
              {["sheet", "sheet_wrong_product", "sheet_missing_back"].map((s) => <option key={s}>{s}</option>)}
            </select>
            <button disabled={prod?.approval_status !== "approved" || !sp}
              onClick={() => run(async () => setSheet(await L.sheet(pid, undefined, `${sp}/reference/${sheetSample}.jpg`)))}>
              {t("use_sample")}: {sp}</button>
          </div>
          {sheet && <p className={`explain ${sheet.ok ? "ok" : "bad"}`}><b>{sheet.decision}</b> — {sheet.explanation[lang]}</p>}
        </section>

        <section className="card cycle">
          <div className="card-head"><h2>{t("cycle")}</h2>
            {cycle && <span className="sub">{cycle.phase} · {t("accepted")} {cycle.counters.accepted} · {t("rejected")} {cycle.counters.rejected} · {t("review")} {cycle.counters.review}</span>}</div>
          {!busyPhase && (<>
            {problems.length > 0 && <ul className="notes">{problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
            <label className="row"><input type="checkbox" checked={layout} onChange={(e) => setLayout(e.target.checked)} /> {t("layout_ok")}</label>
            <button className="primary big" disabled={!pid || problems.length > 0 || !layout}
              onClick={() => window.confirm(t("start_cycle_confirm")) && run(() => L.startCycle(pid, layout))}>{t("start_cycle")}</button>
          </>)}
          {cycle && cycle.phase !== "IDLE" && <p className="explain">{cycle.message?.[lang]}</p>}
          {cycle?.phase === "AWAITING_PHOTOS" && (
            <div>
              <b>{t("photos_needed")}:</b> {cycle.required_faces.map((f: string) => (
                <span key={f} className={`param ${cycle.photos_received.includes(f) ? "done" : ""}`}>{f}
                  <button disabled={!cam.on} onClick={() => run(async () => L.cyclePhoto(f, await cam.snap()))}>{t("capture_face")}</button>
                </span>))}
              <div className="row">
                <select value={sp} onChange={(e) => setSampleProd(e.target.value)}>{Object.keys(samples).map((p) => <option key={p}>{p}</option>)}</select>
                <select value={caseSel} onChange={(e) => setCaseSel(e.target.value)}>{(samples[sp]?.test ?? []).map((c: string) => <option key={c}>{c}</option>)}</select>
                <button onClick={() => run(async () => { for (const f of cycle.required_faces) await L.cyclePhotoSample(f, `${sp}/test/${caseSel}_${f}.jpg`); })}>
                  {t("use_sample")}</button>
              </div>
            </div>)}
          {cycle?.phase === "HOLD_REVIEW" && (
            <div className="row"><button onClick={() => run(() => L.review("reinspect"))}>{t("reinspect")}</button>
              <button className="danger" onClick={() => run(() => L.review("reject"))}>{t("reject")}</button></div>)}
          {busyPhase && <button onClick={() => run(L.abort)}>{t("abort_cycle")}</button>}
          {insp && <InspectionView r={insp} />}
        </section>

        <section className="card">
          <h2>{t("inspect_manual")}</h2>
          <div className="row">
            <select value={sp} onChange={(e) => setSampleProd(e.target.value)}>{Object.keys(samples).map((p) => <option key={p}>{p}</option>)}</select>
            <select value={caseSel} onChange={(e) => setCaseSel(e.target.value)}>{(samples[sp]?.test ?? []).map((c: string) => <option key={c}>{c}</option>)}</select>
            <button disabled={prod?.approval_status !== "approved"} onClick={() => run(async () => {
              const ph: Record<string, Blob> = {};
              for (const f of ["front", "back"]) ph[f] = await sampleBlob(`${sp}/test/${caseSel}_${f}.jpg`);
              setInsp(await L.inspect(pid, ph, true));
            })}>{t("use_sample")}</button>
          </div>
        </section>
        {err && <div className="hint bad">{err}</div>}
      </div>
    </main>
  );
}

export function InspectionHistory() {
  const t = useT();
  const [items, setItems] = useState<any[]>([]);
  const [sel, setSel] = useState<any>(null);
  const lang = document.documentElement.lang === "en" ? "en" : "it";
  useEffect(() => { L.inspections().then(setItems); }, []);
  return (
    <main className="grid">
      <section className="card"><h2>{t("nav_hist")}</h2>
        {items.length === 0 ? <div className="empty">{t("no_inspections")}</div> : (
          <ul className="insp-list">{items.map((i) => (
            <li key={i.inspection_id} onClick={() => L.inspection(i.inspection_id).then(setSel)}>
              <Decision d={i.decision} /> <span className="mono">{new Date(i.ts * 1000).toLocaleString()}</span>
              <span>{i.product_id} v{i.spec_version}</span><span className="sub">{i.explanation?.[lang]?.slice(0, 120)}</span>
            </li>))}</ul>)}
      </section>
      <section className="card">{sel ? <InspectionView r={sel} /> : <div className="empty">—</div>}</section>
    </main>
  );
}

export function ProductSpecs() {
  const t = useT();
  const [products, setProducts] = useState<any[]>([]);
  const [pid, setPid] = useState("");
  useEffect(() => { L.products().then((p) => { setProducts(p); if (p.length) setPid(p[0].product_id); }); }, []);
  return (
    <main className="grid">
      <section className="card"><h2>{t("nav_specs")}</h2>
        {products.length === 0 ? <div className="empty">{t("no_products")}</div> : (
          <ul className="insp-list">{products.map((p) => (
            <li key={p.product_id} className={p.product_id === pid ? "on" : ""} onClick={() => setPid(p.product_id)}>
              <span className={`decision ${p.approval_status === "approved" ? "ok" : "warn"}`}>{p.approval_status}</span>
              <b>{p.product_name ?? p.product_id}</b> <span className="sub">v{p.version} · {p.labels.join(", ")}</span></li>))}</ul>)}
      </section>
      {pid && <SpecView key={pid} pid={pid} />}
    </main>
  );
}
