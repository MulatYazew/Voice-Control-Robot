// Smart Labelling API client + browser camera helper.

async function j(r: Response) {
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail ?? r.statusText);
  return d;
}

export const L = {
  status: () => fetch("/api/labelling/status").then(j),
  products: () => fetch("/api/labelling/products").then(j),
  product: (pid: string) => fetch(`/api/labelling/products/${pid}`).then(j),
  samples: () => fetch("/api/labelling/samples").then(j),
  learn: (photos: Record<string, Blob>, name: string) => {
    const f = new FormData();
    Object.entries(photos).forEach(([face, b]) => f.append(face, b, `${face}.jpg`));
    f.append("name", name);
    return fetch("/api/labelling/products", { method: "POST", body: f }).then(j);
  },
  learnSample: (sample_product: string, name: string) =>
    fetch("/api/labelling/products/from-sample", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sample_product, name }) }).then(j),
  approve: (pid: string, body: any) =>
    fetch(`/api/labelling/products/${pid}/approve`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then(j),
  sheet: (pid: string, blob?: Blob, sample?: string) => {
    const f = new FormData();
    if (blob) f.append("file", blob, "sheet.jpg");
    if (sample) f.append("sample", sample);
    return fetch(`/api/labelling/products/${pid}/sheet`, { method: "POST", body: f }).then(j);
  },
  inspect: (pid: string, photos: Record<string, Blob>, useGrok: boolean) => {
    const f = new FormData();
    f.append("product_id", pid);
    f.append("use_grok", String(useGrok));
    Object.entries(photos).forEach(([face, b]) => f.append(face, b, `${face}.jpg`));
    return fetch("/api/labelling/inspect", { method: "POST", body: f }).then(j);
  },
  inspections: () => fetch("/api/labelling/inspections").then(j),
  inspection: (iid: string) => fetch(`/api/labelling/inspections/${iid}`).then(j),
  cycle: () => fetch("/api/labelling/cycle").then(j),
  preflight: (product_id: string) => fetch("/api/labelling/cycle/preflight", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ product_id }) }).then(j),
  startCycle: (product_id: string, layout_confirmed: boolean) => fetch("/api/labelling/cycle/start", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_id, operator_confirmed: true, layout_confirmed }) }).then(j),
  cyclePhoto: (face: string, blob: Blob) => {
    const f = new FormData();
    f.append("face", face);
    f.append("file", blob, `${face}.jpg`);
    return fetch("/api/labelling/cycle/photo", { method: "POST", body: f }).then(j);
  },
  cyclePhotoSample: (face: string, sample: string) => fetch("/api/labelling/cycle/photo-sample", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ face, sample }) }).then(j),
  review: (choice: "reinspect" | "reject") => fetch("/api/labelling/cycle/review", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice }) }).then(j),
  abort: () => fetch("/api/labelling/cycle/abort", { method: "POST" }).then(j),
  file: (pid: string, version: number, name: string) => `/api/labelling/file/products/${pid}/v${version}/${name}`,
  inspFile: (iid: string, name: string) => `/api/labelling/file/inspections/${iid}/${name}`,
  sampleFile: (rel: string) => `/api/labelling/sample-file/${rel}`,
};

export async function sampleBlob(rel: string): Promise<Blob> {
  return (await fetch(L.sampleFile(rel))).blob();
}

/** Browser camera: live preview + JPEG snapshot (requires a user click). */
export class Camera {
  stream: MediaStream | null = null;
  async start(video: HTMLVideoElement) {
    this.stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 } } });
    video.srcObject = this.stream;
    await video.play();
  }
  snapshot(video: HTMLVideoElement): Promise<Blob> {
    const c = document.createElement("canvas");
    c.width = video.videoWidth;
    c.height = video.videoHeight;
    c.getContext("2d")!.drawImage(video, 0, 0);
    return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("snapshot failed"))), "image/jpeg", 0.92));
  }
  stop() { this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null; }
}
