/**
 * Per-turn latency timing for the blog-post waterfall.
 *
 * One "turn" runs from the moment the user clicks Start Listening to the
 * moment the avatar's first new video frame is rendered in response. Each
 * stage event along the way is marked with performance.now() and shipped
 * to the backend as a single client_timing summary so the per-stage
 * histograms live in Loki alongside the backend per-stage logs.
 *
 * "first avatar frame rendered" comes from requestVideoFrameCallback on the
 * avatar <video> element — the closest browser signal to "the user
 * perceived the avatar speaking." Falls back to requestAnimationFrame on
 * browsers without rVFC (Firefox); the simli-client v3 SDK does not emit
 * a server-visible first-frame ack so this is the only source we have.
 */

export type TimingEvent =
  | "audio_capture_start"
  | "webspeech_final"
  | "ws_send"
  | "first_pcm"
  | "first_frame_rendered";

export interface TimingSummary {
  turn_id: string;
  audio_capture_start_ms: number | null;
  webspeech_final_ms: number | null;
  ws_send_ms: number | null;
  first_pcm_ms: number | null;
  first_frame_rendered_ms: number | null;
}

type Sink = (summary: TimingSummary) => void;

interface TurnRecord {
  id: string;
  t0: number;
  events: Partial<Record<TimingEvent, number>>;
  shipped: boolean;
}

let _sink: Sink | null = null;
let _video: HTMLVideoElement | null = null;
let _current: TurnRecord | null = null;

export function setTimingSink(sink: Sink | null): void {
  _sink = sink;
}

export function setVideoElement(el: HTMLVideoElement | null): void {
  _video = el;
}

export function beginTurn(): string {
  if (_current && !_current.shipped) _ship(_current, "preempted");
  const id =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  _current = { id, t0: performance.now(), events: {}, shipped: false };
  console.log(`[TIMING] turn_begin id=${id}`);
  return id;
}

export function currentTurnId(): string | null {
  return _current?.id ?? null;
}

export function mark(event: TimingEvent): void {
  const t = performance.now();
  if (!_current) {
    console.debug(`[TIMING] ${event} (no active turn — ignored)`);
    return;
  }
  if (_current.events[event] !== undefined) return; // first occurrence wins
  const delta = t - _current.t0;
  _current.events[event] = delta;
  console.log(
    `[TIMING] ${event} delta=${delta.toFixed(1)}ms turn=${_current.id}`,
  );
  if (event === "first_pcm") _armFrameCallback();
}

interface VideoFrameCallbackMetadata {
  presentationTime: number;
}

type VideoFrameCallback = (
  now: number,
  metadata: VideoFrameCallbackMetadata,
) => void;

function _armFrameCallback(): void {
  const turn = _current;
  if (!turn) return;
  const el = _video as
    | (HTMLVideoElement & {
        requestVideoFrameCallback?: (cb: VideoFrameCallback) => number;
      })
    | null;
  if (el && typeof el.requestVideoFrameCallback === "function") {
    el.requestVideoFrameCallback(() => {
      if (_current === turn) {
        mark("first_frame_rendered");
        _ship(turn, "frame_callback");
      }
    });
    return;
  }
  const fallback = () => {
    if (_current === turn) {
      mark("first_frame_rendered");
      _ship(turn, "raf_fallback");
    }
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(fallback);
  } else {
    setTimeout(fallback, 16);
  }
}

function _ship(turn: TurnRecord, reason: string): void {
  if (turn.shipped) return;
  turn.shipped = true;
  if (!_sink) {
    console.debug(`[TIMING] no sink set — summary dropped (reason=${reason})`);
    return;
  }
  _sink({
    turn_id: turn.id,
    audio_capture_start_ms: turn.events.audio_capture_start ?? null,
    webspeech_final_ms: turn.events.webspeech_final ?? null,
    ws_send_ms: turn.events.ws_send ?? null,
    first_pcm_ms: turn.events.first_pcm ?? null,
    first_frame_rendered_ms: turn.events.first_frame_rendered ?? null,
  });
}
