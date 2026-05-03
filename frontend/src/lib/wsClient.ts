/**
 * WebSocket client for the /ws/interview endpoint.
 *
 * Outbound: JSON text frames (transcripts / skip).
 * Inbound:  binary frames whose first byte tells the avatar dispatcher whether
 *           the rest is a play-immediate chunk (0x01) or a normal chunk (0x00).
 *           See app/avatar/simli_client.py for the protocol definition.
 *
 * Reconnects with exponential backoff + jitter; the reconnect timer is tracked
 * so close() can cancel a pending reconnect that would otherwise fire after
 * unmount. Outbound frames sent while the socket is CONNECTING are queued and
 * flushed on open instead of being silently dropped.
 */

export type AudioHandler = (pcm: Uint8Array, immediate: boolean) => void;

const MAX_BACKOFF_MS = 30_000;

export class InterviewWebSocket {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private onAudio: AudioHandler;
  private onStatus: (status: string) => void;
  private attempt = 0;
  private closed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private outboundQueue: string[] = [];

  constructor(
    sessionId: string,
    onAudio: AudioHandler,
    onStatus: (status: string) => void
  ) {
    this.sessionId = sessionId;
    this.onAudio = onAudio;
    this.onStatus = onStatus;
  }

  connect(): void {
    if (this.closed) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${location.host}/ws/interview?session_id=${this.sessionId}`;
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      this.attempt = 0;
      this.onStatus("connected");
      while (this.outboundQueue.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
        const payload = this.outboundQueue.shift()!;
        this.ws.send(payload);
      }
    };

    this.ws.onmessage = (event: MessageEvent) => {
      if (!(event.data instanceof ArrayBuffer) || event.data.byteLength < 2) return;
      const view = new Uint8Array(event.data);
      const immediate = view[0] === 0x01;
      this.onAudio(view.subarray(1), immediate);
    };

    this.ws.onclose = () => {
      if (this.closed) return;
      this.onStatus("reconnecting");
      const backoff = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.attempt);
      const jitter = Math.random() * Math.min(backoff, 1000);
      this.attempt++;
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, backoff + jitter);
    };

    this.ws.onerror = () => {
      // onclose fires after onerror — backoff handled there
    };
  }

  sendTranscript(text: string): void {
    this._send({ type: "transcript", text });
  }

  sendSkip(): void {
    this._send({ type: "skip" });
  }

  close(): void {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.outboundQueue = [];
    this.ws?.close();
  }

  private _send(payload: object): void {
    const serialised = JSON.stringify(payload);
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(serialised);
      return;
    }
    if (this.closed) return;
    this.outboundQueue.push(serialised);
  }
}
