// Browser voice channel: mic (AudioWorklet, PCM16 24 kHz) -> /ws/voice -> backend -> Grok Voice.
// The browser never talks to xAI and never holds an API key.

export type VoiceState = "off" | "starting" | "live" | "paused" | "reconnecting" | "error";

export interface VoiceCallbacks {
  onState: (s: VoiceState, detail?: string) => void;
  onLevel: (rms: number) => void;
}

export class VoiceClient {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;
  private ws: WebSocket | null = null;
  private playHead = 0;
  private playing = new Set<AudioBufferSourceNode>();
  private wanted = false;
  private paused = false;
  private retry = 0;

  constructor(private cb: VoiceCallbacks, private lang: () => string = () => "auto") {}

  /** Must be called from a user gesture (click): requests mic permission and starts streaming. */
  async start(): Promise<void> {
    this.wanted = true;
    this.cb.onState("starting");
    try {
      this.ctx = new AudioContext();
      await this.ctx.audioWorklet.addModule("/mic-worklet.js");
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "mic-processor");
      this.node.port.onmessage = (e) => {
        const { pcm, level } = e.data as { pcm: ArrayBuffer | null; level: number };
        this.cb.onLevel(level);
        if (pcm && this.ws?.readyState === WebSocket.OPEN && !this.paused) this.ws.send(pcm);
      };
      src.connect(this.node); // not routed to the speakers
      this.connect();
    } catch (err: any) {
      this.cb.onState("error", err?.message ?? String(err));
      this.teardown();
    }
  }

  private connect(): void {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/voice?lang=${this.lang()}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.cb.onState(this.paused ? "paused" : "live");
      ws.send(JSON.stringify({ type: "mic", on: !this.paused }));
    };
    ws.onmessage = (m) => {
      if (m.data instanceof ArrayBuffer) return this.play(m.data);
      try {
        const ev = JSON.parse(m.data);
        if (ev.type === "flush") this.flush();
        if (ev.type === "error") this.cb.onState("error", ev.message);
      } catch { /* ignore */ }
    };
    ws.onclose = () => {
      this.flush();
      if (!this.wanted) return;
      // fresh session; the backend never restores a pending action
      this.cb.onState("reconnecting");
      const delay = Math.min(1000 * 2 ** this.retry++, 8000);
      window.setTimeout(() => this.wanted && this.connect(), delay);
    };
  }

  setPaused(p: boolean): void {
    this.paused = p;
    this.node?.port.postMessage({ enabled: !p });
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "mic", on: !p }));
    this.cb.onState(p ? "paused" : "live");
  }

  stop(): void {
    this.wanted = false;
    this.teardown();
    this.cb.onState("off");
  }

  private teardown(): void {
    this.flush();
    this.ws?.close();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.ws = null; this.stream = null; this.ctx = null; this.node = null;
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
