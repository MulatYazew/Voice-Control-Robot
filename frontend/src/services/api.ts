// REST calls. STOP goes straight to the backend: it never depends on the voice session or Grok.

export async function stopRobot(source: "button" | "space"): Promise<any> {
  const r = await fetch("/api/stop", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source }), keepalive: true,
  });
  return r.json();
}

export async function resetSystem(): Promise<{ ok: boolean; messaggio?: string }> {
  const r = await fetch("/api/reset", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operator_confirmed: true }),
  });
  return r.json();
}

export async function textCommand(text: string, engine: "rules" | "grok", lang = "auto"): Promise<any> {
  const r = await fetch("/api/text-command", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, engine, lang, request_id: crypto.randomUUID() }),
  });
  if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
  return r.json();
}

export async function fetchEval(): Promise<any> {
  return (await fetch("/api/eval/latest")).json();
}

async function post(path: string, body: unknown = {}): Promise<any> {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.json();
}

export const scanCups = async () => (await fetch("/api/vision/cups")).json();
export const pointAtCup = (colour: string) => post("/api/vision/point", { colour });
export const grabCup = (colour: string) => post("/api/vision/grab", { colour });
export const placeCup = () => post("/api/vision/place");
export const startInspection = (rule: string) => post("/api/inspection/start", { rule: rule || null });
export const partReady = () => post("/api/world/part-ready");

// Cell camera + calibration + inspection (from grokarm-ur3e, Apache-2.0)
export const touchCup = (colour: string) => post("/api/vision/touch", { colour });
export const cupsBackground = (clear = false) => post(`/api/vision/cups/background${clear ? "?clear=true" : ""}`);
export const calibration = async () => (await fetch("/api/calibration")).json();
export const addCalibPoint = (u: number, v: number) => post("/api/calibration/point", { u, v });
export const editCalibration = (action: "undo" | "clear") => post(`/api/calibration/${action}`);
export const markerRecord = (which: "center" | "axis") => post(`/api/calibration/marker/${which}`);
export const computeCorners = () => post("/api/calibration/corners");
export const gotoCorner = (index: number) => post("/api/calibration/goto", { index });
export async function pushFrame(jpeg: Blob): Promise<void> {
  await fetch("/api/camera/frame", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: jpeg });
}
