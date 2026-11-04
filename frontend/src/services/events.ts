// /ws/events subscriber with automatic reconnection.

export type EventHandler = (ev: any) => void;
export type ConnHandler = (state: "connected" | "reconnecting" | "disconnected") => void;

export class EventStream {
  private ws: WebSocket | null = null;
  private retry = 0;
  private closed = false;
  private ping: number | undefined;

  constructor(private onEvent: EventHandler, private onConn: ConnHandler) {}

  start(): void {
    this.closed = false;
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.onConn("connected");
      this.ping = window.setInterval(() => ws.readyState === 1 && ws.send("ping"), 10000);
    };
    ws.onmessage = (m) => {
      try { this.onEvent(JSON.parse(m.data)); } catch { /* ignore */ }
    };
    ws.onclose = () => {
      window.clearInterval(this.ping);
      if (this.closed) return;
      this.onConn(this.retry === 0 ? "reconnecting" : "disconnected");
      const delay = Math.min(500 * 2 ** this.retry++, 5000);
      window.setTimeout(() => this.start(), delay);
    };
  }

  stop(): void {
    this.closed = true;
    this.ws?.close();
  }
}
