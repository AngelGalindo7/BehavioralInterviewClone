/**
 * Thin wrapper around the browser WebSpeech API (Chrome-only).
 *
 * interimResults=true lets us surface live partials in the UI; only the FINAL
 * recognition result is sent to the backend (we don't want to dispatch a turn
 * on every interim hypothesis). Surfacing partials trims ~300–500 ms of
 * perceived latency vs. waiting silently for end-of-utterance.
 *
 * Chrome's internal end-of-speech detector waits ~700–1200 ms of silence before
 * raising isFinal=true. There is no API to tune this. To cut that wait we add:
 *   - silenceFallbackMs: if no new interim arrives within this many ms, fire
 *     onFinal with the latest interim and abort the recogniser.
 *   - commitNow(): manual flush of the latest interim, used by the Stop button
 *     so the user can dispatch a turn at the moment they finish speaking.
 *
 * NOTE: SpeechRecognition is not available in Firefox or Safari.
 */

type ResultCallback = (text: string) => void;
type InterimCallback = (text: string) => void;
type ErrorCallback = (error: string) => void;

interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}

interface SpeechRecognitionResult {
  readonly length: number;
  readonly isFinal: boolean;
  [index: number]: SpeechRecognitionAlternative;
}

interface SpeechRecognitionResultList {
  readonly length: number;
  [index: number]: SpeechRecognitionResult;
}

interface SpeechRecognitionEvent extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEvent extends Event {
  readonly error: string;
  readonly message: string;
}

interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognition;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

export interface RecognitionHandle {
  recognition: SpeechRecognition;
  /** Flush the latest interim transcript as if it were final, then stop. */
  commitNow(): void;
  /** Stop without firing onFinal. Mirrors the native .stop(). */
  stop(): void;
}

export type { SpeechRecognition };

export function isSpeechRecognitionSupported(): boolean {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

interface StartOptions {
  silenceFallbackMs?: number;
}

export function startSpeechRecognition(
  onFinal: ResultCallback,
  onError?: ErrorCallback,
  onInterim?: InterimCallback,
  options: StartOptions = {},
): RecognitionHandle {
  const SpeechRecognitionImpl =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognitionImpl) {
    throw new Error("SpeechRecognition API is not available in this browser");
  }

  const recognition = new SpeechRecognitionImpl();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = "en-US";
  recognition.maxAlternatives = 1;

  let latestInterim = "";
  let committed = false;
  let silenceTimer: ReturnType<typeof setTimeout> | null = null;
  const silenceMs = options.silenceFallbackMs;

  const clearSilenceTimer = () => {
    if (silenceTimer !== null) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
  };

  const commit = (text: string) => {
    if (committed) return;
    committed = true;
    clearSilenceTimer();
    onFinal(text);
    try {
      recognition.abort();
    } catch {
      // recogniser may already be stopped
    }
  };

  const armSilenceTimer = () => {
    if (!silenceMs) return;
    clearSilenceTimer();
    silenceTimer = setTimeout(() => {
      const candidate = latestInterim.trim();
      if (candidate) commit(candidate);
    }, silenceMs);
  };

  recognition.onresult = (event: SpeechRecognitionEvent) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const transcript = result[0]?.transcript?.trim() ?? "";
      if (!transcript) continue;
      if (result.isFinal) {
        commit(transcript);
      } else {
        latestInterim = transcript;
        onInterim?.(transcript);
        armSilenceTimer();
      }
    }
  };

  recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
    clearSilenceTimer();
    onError?.(event.error);
  };

  recognition.start();

  return {
    recognition,
    commitNow() {
      const candidate = latestInterim.trim();
      if (candidate) {
        commit(candidate);
      } else {
        clearSilenceTimer();
        try {
          recognition.stop();
        } catch {
          // already stopped
        }
      }
    },
    stop() {
      clearSilenceTimer();
      try {
        recognition.stop();
      } catch {
        // already stopped
      }
    },
  };
}
