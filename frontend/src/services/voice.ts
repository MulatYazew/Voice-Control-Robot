// Browser voice channel: mic (AudioWorklet, PCM16 24 kHz) -> /ws/voice -> backend -> Grok Voice.
// The browser never talks to xAI and never holds an API key.

export type VoiceState = "off" | "starting" | "live" | "paused" | "reconnecting" | "error";

export interface VoiceCallbacks {
  onState: (s: VoiceState, detail?: string) => void;
  onLevel: (rms: number) => void;
  onWake?: (state: "on" | "not supported") => void;
}

export class VoiceClient {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;
  private ws: WebSocket | null = null;
  private playHead = 0;
  private playing = new Set<AudioBufferSourceNode>();
  private wanted = false;
  private ended = false;
  private paused = false;
  private retry = 0;
  private lastRx = 0;
  private pingTimer = 0;
  private wake: { release: () => Promise<void> } | null = null;
  private online = () => this.reconnectNow();
  private visible = () => { if (document.visibilityState === "visible") { this.reconnectNow(); this.keepAwake(); } };

  constructor(private cb: VoiceCallbacks, private lang: () => string = () => "auto") {}

  /** Must be called from a user gesture (click): requests mic permission and starts streaming. */
  async start(): Promise<void> {
    this.wanted = true;
    this.ended = false;
    this.cb.onState("starting");
    window.addEventListener("online", this.online);
    document.addEventListener("visibilitychange", this.visible);
    try {
      await this.openMic();
      this.connect();
      this.keepAwake();
    } catch (err: any) {
      this.cb.onState("error", err?.message ?? String(err));
      this.teardown();
    }
  }

  private async openMic(): Promise<void> {
    this.ctx = this.ctx ?? new AudioContext();
    this.ctx.onstatechange = () => { if (this.ctx && this.ctx.state !== "running") this.ctx.resume().catch(() => undefined); };
    if (this.ctx.state !== "running") await this.ctx.resume();
    await this.ctx.audioWorklet.addModule("/mic-worklet.js");
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const track = this.stream.getAudioTracks()[0];
    if (track) track.onended = () => { if (this.wanted && !this.ended) window.setTimeout(() => this.openMic().catch(() => undefined), 500); };
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "mic-processor");
    this.node.port.onmessage = (e) => {
      const { pcm, level } = e.data as { pcm: ArrayBuffer | null; level: number };
      this.cb.onLevel(level);
      if (pcm && this.ws?.readyState === WebSocket.OPEN && !this.paused) this.ws.send(pcm);
    };
    src.connect(this.node); // not routed to the speakers
  }

  private keepAwake(): void {
    const nav = navigator as Navigator & { wakeLock?: { request: (k: string) => Promise<{ release: () => Promise<void> }> } };
    if (!nav.wakeLock) { this.cb.onWake?.("not supported"); return; }
    nav.wakeLock.request("screen").then((s) => { this.wake = s; this.cb.onWake?.("on"); }).catch(() => this.cb.onWake?.("not supported"));
  }

  private reconnectNow(): void {
    if (!this.wanted || this.ended) return;
    this.retry = 0;
    this.ws?.close();
  }

  private connect(): void {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/voice?lang=${this.lang()}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    this.lastRx = Date.now();
    ws.onopen = () => {
      this.retry = 0;
      this.lastRx = Date.now();
      this.cb.onState(this.paused ? "paused" : "live");
      ws.send(JSON.stringify({ type: "mic", on: !this.paused }));
      window.clearInterval(this.pingTimer);
      this.pingTimer = window.setInterval(() => {
        if (ws.readyState !== WebSocket.OPEN) return;
        if (Date.now() - this.lastRx > 15000) { ws.close(); return; }
        ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
      }, 5000);
    };
    ws.onmessage = (m) => {
      this.lastRx = Date.now();
      if (m.data instanceof ArrayBuffer) return this.play(m.data);
      try {
        const ev = JSON.parse(m.data);
        if (ev.type === "flush") this.flush();
        if (ev.type === "pong" || ev.type === "voice_status") {
          if (ev.status === "reconnecting") this.cb.onState("reconnecting", ev.detail);
          else if (!this.paused && this.wanted) this.cb.onState("live");
        }
        if (ev.type === "error") this.cb.onState("reconnecting", ev.message);
        if (ev.type === "session_end") { this.ended = true; this.wanted = false; this.cb.onState("off", ev.message); }
      } catch { /* ignore */ }
    };
    ws.onclose = () => {
      window.clearInterval(this.pingTimer);
      this.flush();
      if (!this.wanted || this.ended) return;
      // fresh session; the backend never restores a pending action
      this.cb.onState("reconnecting");
      const delay = Math.min(1000 * 2 ** this.retry++, 10000);
      window.setTimeout(() => this.wanted && !this.ended && this.connect(), delay);
    };
  }

  setPaused(p: boolean): void {
    this.paused = p;
    this.node?.port.postMessage({ enabled: !p });
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "mic", on: !p }));
    this.cb.onState(p ? "paused" : "live");
  }

  /** Operator button. The browser does not reconnect after this. */
  stop(): void {
    this.ended = true;
    this.wanted = false;
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "end_session" }));
    this.teardown();
    this.cb.onState("off");
  }

  private teardown(): void {
    window.clearInterval(this.pingTimer);
    window.removeEventListener("online", this.online);
    document.removeEventListener("visibilitychange", this.visible);
    this.flush();
    this.ws?.close();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.wake?.release().catch(() => undefined);
    this.ctx?.close();
    this.ws = null; this.stream = null; this.ctx = null; this.node = null; this.wake = null;
  }

  private play(buf: ArrayBuffer): void {
    if (!this.ctx) return;
    const i16 = new Int16Array(buf);
    const ab = this.ctx.createBuffer(1, i16.length, 24000);
    const ch = ab.getChannelData(0);
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
    const s = this.ctx.createBufferSource();
    s.buffer = ab;
    s.connect(this.ctx.destination);
    const t = Math.max(this.ctx.currentTime + 0.03, this.playHead);
    s.start(t);
    this.playHead = t + ab.duration;
    this.playing.add(s);
    s.onended = () => this.playing.delete(s);
  }

  /** Barge-in / STOP: drop everything queued for playback immediately. */
  flush(): void {
    for (const s of this.playing) { try { s.stop(); } catch { /* already stopped */ } }
    this.playing.clear();
    this.playHead = 0;
  }
}
