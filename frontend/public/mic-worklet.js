// Mic capture: resample to 24 kHz mono PCM16, post ~40 ms chunks + RMS level for the meter.
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 24000;
    this.pos = 0;
    this.prev = 0;
    this.out = new Int16Array(960);
    this.n = 0;
    this.sum = 0;
    this.count = 0;
    this.enabled = true;
    this.port.onmessage = (e) => { if (e.data && "enabled" in e.data) this.enabled = !!e.data.enabled; };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const a = i === 0 ? this.prev : ch[i - 1], b = ch[i];
      const s = Math.max(-1, Math.min(1, a + (b - a) * f));
      this.sum += s * s; this.count++;
      this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.out.length) {
        const level = Math.sqrt(this.sum / this.count);
        this.port.postMessage({ pcm: this.enabled ? this.out.buffer.slice(0) : null, level: this.enabled ? level : 0 });
        this.n = 0; this.sum = 0; this.count = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    this.prev = ch[ch.length - 1];
    return true;
  }
}
registerProcessor("mic-processor", MicProcessor);
