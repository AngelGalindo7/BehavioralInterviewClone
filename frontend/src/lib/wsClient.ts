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

import {
  currentTurnId,
  mark as markTiming,
  setTimingSink,
  type TimingSummary,
} from "./timing";
import { wsUrl } from "./api";

export type AudioHandler = (pcm: Uint8Array, immediate: boolean) => void;

const MAX_BACKOFF_MS = 30_000;

// Audio frame diagnostic counters — reset per connection.
let _frameCount = 0;
let _lastFrameTs = 0;

export interface InterviewWebSocketOptions {
  // Provider name routes the WS to the correct backend pipeline branch
  // (audio_pcm vs text). Omit for the server-side default (Simli).
  provider?: string;
  // Required when provider is a text-mode provider (HeyGen) — the backend
  // needs this id to route streaming.task/stop calls to the right upstream
  // session. Ignored by audio_pcm providers.
  avatarSessionId?: string;
  // Called when the backend signals a per-turn pipeline failure via a JSON
  // text frame. Distinct from onStatus (connection-level state).
  onTurnError?: (message: string) => void;
}

export class InterviewWebSocket {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private onAudio: AudioHandler;
  private onStatus: (status: string) => void;
  private onTurnError: ((message: string) => void) | undefined;
  private provider: string | undefined;
  private avatarSessionId: string | undefined;
  private attempt = 0;
  private closed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private outboundQueue: string[] = [];

  constructor(
    sessionId: string,
    onAudio: AudioHandler,
    onStatus: (status: string) => void,
    options?: InterviewWebSocketOptions,
  ) {
    this.sessionId = sessionId;
    this.onAudio = onAudio;
    this.onStatus = onStatus;
    this.onTurnError = options?.onTurnError;
    this.provider = options?.provider;
    this.avatarSessionId = options?.avatarSessionId;
    // Route the per-turn timing summary out over this socket. The sink is a
    // module-level singleton so close() must clear it; otherwise a freshly
    // opened socket from a later session inherits a stale sink reference.
    setTimingSink((summary) => this._sendTimingSummary(summary));
  }

  connect(): void {
    if (this.closed) return;
    const params = new URLSearchParams({ session_id: this.sessionId });
    if (this.provider) params.set("provider", this.provider);
    if (this.avatarSessionId) params.set("avatar_session_id", this.avatarSessionId);
    const url = `${wsUrl("/ws/interview")}?${params.toString()}`;
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
      if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data) as { type?: string; message?: string };
          if (msg.type === "error") {
            this.onTurnError?.(msg.message ?? "Something went wrong.");
          }
        } catch {
          console.warn("[AUDIO-WS] unhandled text frame:", event.data);
        }
        return;
      }
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
      // Mark the FIRST PCM frame of this turn. The timing module ignores
      // re-marks within a turn, so subsequent chunks for the same turn are
      // free — no need to gate on _frameCount here.
      if (immediate) markTiming("first_pcm");
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
    // Tag the transcript with the current turn_id so the backend can
    // correlate its per-stage timings and the eventual client_timing
    // summary onto the same turn in Loki.
    const turn_id = currentTurnId();
    markTiming("ws_send");
    this._send({ type: "transcript", text, turn_id });
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
    setTimingSink(null);
    this.ws?.close();
  }

  private _sendTimingSummary(summary: TimingSummary): void {
    this._send({
      type: "client_timing",
      turn_id: summary.turn_id,
      events: {
        audio_capture_start_ms: summary.audio_capture_start_ms,
        webspeech_final_ms: summary.webspeech_final_ms,
        ws_send_ms: summary.ws_send_ms,
        first_pcm_ms: summary.first_pcm_ms,
        first_frame_rendered_ms: summary.first_frame_rendered_ms,
      },
    });
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
