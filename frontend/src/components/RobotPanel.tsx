import type { Layout, Status } from "../services/types";

// Schematic top view: base-joint angle only (not a kinematic rendering).
function TopView({ layout, joints, trays, holding }: { layout: Layout; joints: number[] | null; trays: string[];
  holding: boolean }) {
  const W = 320, H = 190, cx = W / 2, cy = H - 18, R = 140;
  const zones = Object.entries(layout.zones);
  // map base angle so the zone fan is centred and spread over ±70°
  const angs = zones.map(([, a]) => a);
  const mid = (Math.max(...angs) + Math.min(...angs)) / 2;
  const toScreen = (a: number) => -Math.PI / 2 - (a - mid) * 1.4;
  const pt = (a: number, r: number) => [cx + r * Math.cos(toScreen(a)), cy + r * Math.sin(toScreen(a))];
  const base = joints?.[0];
  const reach = joints ? 70 + 55 * Math.min(1, Math.abs(Math.cos(joints[1] ?? 0)) + 0.3) : 90;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="topview" role="img" aria-label="Vista schematica dall'alto">
      <path d={`M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}`} className="reach" />
      {zones.map(([z, a]) => {
        const [x, y] = pt(a, R - 18);
        const has = trays.includes(z);
        return (
          <g key={z}>
            <rect x={x - 30} y={y - 16} width={60} height={32} rx={6} className={`zone ${has ? "has" : ""}`} />
            <text x={x} y={y + 30} className="zone-label">{z}</text>
            {has && <text x={x} y={y + 5} className="tray-label">vassoio</text>}
          </g>
        );
      })}
      {base !== undefined && (() => {
        const [x, y] = pt(base, reach);
        return (
          <g>
            <line x1={cx} y1={cy} x2={x} y2={y} className="arm" />
            <circle cx={x} cy={y} r={holding ? 10 : 7} className={`tool ${holding ? "holding" : ""}`} />
          </g>
        );
      })()}
      <circle cx={cx} cy={cy} r={13} className="base" />
    </svg>
  );
}

export function RobotPanel({ status, layout, joints }: { status: Status | null; layout: Layout | null; joints: number[] | null }) {
  if (!status) return null;
  const r = status.robot;
  const w = status.world;
  const q = joints ?? r.joints;
  return (
    <section className="card robot">
      <h2>Robot <span className="sub">{status.robot_adapter}</span></h2>
      {layout && <TopView layout={layout} joints={q} trays={w.oggetti_presunti.vassoi_in_zona} holding={w.oggetti_presunti.vassoio_sulla_forca} />}
      <div className="kv">
        <div><span>Posa nota</span><b>{w.robot_verificato.posa_nota ?? "—"}</b></div>
        <div><span>Modo</span><b>{r.robot_mode ?? "—"}</b></div>
        <div><span>Sicurezza</span><b className={r.safety && r.safety !== "NORMAL" ? "bad" : ""}>{r.safety ?? "—"}</b></div>
        <div><span>Programma</span><b>{r.program ?? "—"}</b></div>
        {r.remote_control !== null && <div><span>Remote Control</span><b>{r.remote_control ? "sì" : "no"}</b></div>}
        <div><span>Giunti (rad)</span><b className="mono">{q ? q.map((x) => x.toFixed(2)).join("  ") : "—"}</b></div>
      </div>
      <h3>Vassoi <span className="sub">presunti · nessuna telecamera</span></h3>
      <div className="kv">
        <div><span>In zona</span><b>{w.oggetti_presunti.vassoi_in_zona.join(", ") || "nessuno"}</b></div>
        <div><span>Sulla forca</span><b>{w.oggetti_presunti.vassoio_sulla_forca ? "sì (presunto)" : "no"}</b></div>
      </div>
      {status.fault && <div className="fault-box">GUASTO: {status.fault}</div>}
    </section>
  );
}
