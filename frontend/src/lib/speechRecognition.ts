/**
 * Thin wrapper around the browser WebSpeech API (Chrome-only).
 *
 * interimResults=true lets us surface live partials in the UI; only the FINAL
 * recognition result is sent to the backend (we don't want to dispatch a turn
 * on every interim hypothesis). Surfacing partials trims ~300–500 ms of
 * perceived latency vs. waiting silently for end-of-utterance.
 *
 * NOTE: SpeechRecognition is not available in Firefox or Safari.
 */

type ResultCallback = (text: string) => void;
type InterimCallback = (text: string) => void;
type ErrorCallback = (error: string) => void;

export function isSpeechRecognitionSupported(): boolean {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

export function startSpeechRecognition(
  onFinal: ResultCallback,
  onError?: ErrorCallback,
  onInterim?: InterimCallback,
): SpeechRecognition {
  const SpeechRecognitionImpl =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  const recognition = new SpeechRecognitionImpl();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = "en-US";
  recognition.maxAlternatives = 1;

  recognition.onresult = (event: SpeechRecognitionEvent) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const transcript = result[0]?.transcript?.trim() ?? "";
      if (!transcript) continue;
      if (result.isFinal) {
        onFinal(transcript);
      } else {
        onInterim?.(transcript);
      }
    }
  };

  recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
    onError?.(event.error);
  };

  recognition.start();
  return recognition;
}

declare global {
  interface Window {
    SpeechRecognition: typeof SpeechRecognition;
    webkitSpeechRecognition: typeof SpeechRecognition;
  }
}
