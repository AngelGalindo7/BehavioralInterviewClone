/**
 * WebSocket client for the /ws/interview endpoint.
 *
 * Outbound: JSON text frames (transcripts / skip).
 * Inbound:  binary frames whose first byte tells the avatar dispatcher whether
 *           the rest is a play-immediate chunk (0x01) or a normal chunk (0x00).
 *           See app/avatar/protocol.py for the protocol definition.
 *
 * Reconnects with exponential backoff + jitter; the reconnect timer is tracked
 * so close() can cancel a pending reconnect that would otherwise fire after
 * unmount. Outbound frames sent while the socket is CONNECTING are queued and
 * flushed on open instead of being silently dropped.
 */

export type AudioHandler = (pcm: Uint8Array, immediate: boolean) => void;

const MAX_BACKOFF_MS = 30_000;

// Audio frame diagnostic counters — reset per connection.
let _frameCount = 0;
let _lastFrameTs = 0;

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
      _frameCount = 0;
      _lastFrameTs = 0;
      this.onStatus("connected");
      console.log("[AUDIO-WS] connected, diagnostic counters reset");
      while (this.outboundQueue.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
        const payload = this.outboundQueue.shift()!;
        this.ws.send(payload);
      }
    };

    this.ws.onmessage = (event: MessageEvent) => {
      if (!(event.data instanceof ArrayBuffer) || event.data.byteLength < 2) {
        console.warn("[AUDIO-WS] received non-audio or too-small frame, byteLength:", event.data instanceof ArrayBuffer ? event.data.byteLength : typeof event.data);
        return;
      }
      const view = new Uint8Array(event.data);
      const immediate = view[0] === 0x01;
      const pcm = view.subarray(1);
      const pcmLen = pcm.byteLength;
      const isAligned = pcmLen % 2 === 0;
      const now = performance.now();
      const gapMs = _lastFrameTs > 0 ? now - _lastFrameTs : null;
      _lastFrameTs = now;

      if (!isAligned) {
        console.error(
          `[AUDIO-WS] MISALIGNED FRAME #${_frameCount}: pcmLen=${pcmLen} (odd) — broken PCM16 sample, likely cause of static. immediate=${immediate}`
        );
      } else {
        console.debug(
          `[AUDIO-WS] frame #${_frameCount}: pcmLen=${pcmLen} immediate=${immediate} gapMs=${gapMs !== null ? gapMs.toFixed(1) : "first"}`
        );
      }

      if (gapMs !== null && gapMs > 200) {
        console.warn(
          `[AUDIO-WS] large gap before frame #${_frameCount}: ${gapMs.toFixed(1)} ms — may cause Simli underrun → static`
        );
      }

      _frameCount++;
      this.onAudio(pcm, immediate);
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
