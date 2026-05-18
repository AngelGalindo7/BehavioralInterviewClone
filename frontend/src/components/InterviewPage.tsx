import { useCallback, useEffect, useRef, useState } from "react";
import AvatarView, { type AvatarState } from "./AvatarView";
import RecordButton from "./RecordButton";
import StatusBar from "./StatusBar";
import { avatarProvider } from "../lib/activeAvatar";
import {
  isSpeechRecognitionSupported,
  startSpeechRecognition,
  type RecognitionHandle,
} from "../lib/speechRecognition";
import { InterviewWebSocket } from "../lib/wsClient";

// Fire onFinal after this many ms of silence after the last interim result
// instead of waiting for Chrome's internal end-of-speech timer (~700–1200 ms).
const STT_SILENCE_FALLBACK_MS = 600;

type Phase = "landing" | "preview" | "running";

export default function InterviewPage() {
  const [phase, setPhase] = useState<Phase>("landing");
  const [confirmingStart, setConfirmingStart] = useState(false);
  const [confirmingStop, setConfirmingStop] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [wsStatus, setWsStatus] = useState("disconnected");
  const [isListening, setIsListening] = useState(false);
  const [avatarReady, setAvatarReady] = useState(false);
  const [lastQuestion, setLastQuestion] = useState("");
  const [interimText, setInterimText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<InterviewWebSocket | null>(null);
  const recognitionRef = useRef<RecognitionHandle | null>(null);
  const avatarRef = useRef<{ video: HTMLVideoElement | null; audio: HTMLAudioElement | null }>(null);

  useEffect(() => {
    if (phase !== "running") return;

    if (!isSpeechRecognitionSupported()) {
      setError("WebSpeech API is not supported. Please use Google Chrome.");
      return;
    }

    let cancelled = false;

    (async () => {
      try {
        const sessionResp = await fetch("/session/", { method: "POST" });
        if (!sessionResp.ok) throw new Error(`POST /session failed (${sessionResp.status})`);
        const { session_id } = await sessionResp.json();
        if (cancelled) return;
        setSessionId(session_id);

        const tokenResp = await fetch("/avatar/session", { method: "POST" });
        if (!tokenResp.ok) throw new Error(`POST /avatar/session failed (${tokenResp.status})`);
        const tokenData = await tokenResp.json();
        const sessionToken: string = tokenData.session_token;
        const iceServers: RTCIceServer[] = tokenData.ice_servers ?? [];
        if (!sessionToken) throw new Error("Simli token response missing session_token");

        const refs = avatarRef.current;
        if (!refs?.video || !refs?.audio) {
          throw new Error("Avatar video/audio elements not mounted");
        }

        await avatarProvider.init({
          sessionToken,
          iceServers,
          videoEl: refs.video,
          audioEl: refs.audio,
        });
        if (cancelled) {
          await avatarProvider.destroy();
          return;
        }
        setAvatarReady(true);

        const ws = new InterviewWebSocket(
          session_id,
          (pcm, immediate) => avatarProvider.sendAudio(pcm, immediate),
          (status) => setWsStatus(status),
        );
        wsRef.current = ws;
        ws.connect();
      } catch (err) {
        if (!cancelled) setError(`Setup failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    })();

    return () => {
      cancelled = true;
      wsRef.current?.close();
      void avatarProvider.destroy();
    };
  }, [phase]);

  const handleStartListening = useCallback(() => {
    if (!avatarReady) return;
    setInterimText("");
    setIsListening(true);
    recognitionRef.current = startSpeechRecognition(
      (transcript) => {
        setIsListening(false);
        setLastQuestion(transcript);
        setInterimText("");
        wsRef.current?.sendTranscript(transcript);
      },
      (err) => {
        setIsListening(false);
        setInterimText("");
        console.error("Speech recognition error:", err);
      },
      (interim) => setInterimText(interim),
      { silenceFallbackMs: STT_SILENCE_FALLBACK_MS },
    );
  }, [avatarReady]);

  // Stop button = "Send now": flushes the latest interim transcript instead
  // of waiting for Chrome's isFinal. If no interim has arrived yet, falls back
  // to a plain stop() and we clear listening state ourselves.
  const handleStopListening = useCallback(() => {
    recognitionRef.current?.commitNow();
    recognitionRef.current = null;
    setIsListening(false);
    setInterimText("");
  }, []);

  const handleSkip = useCallback(() => {
    avatarProvider.interrupt();
    wsRef.current?.sendSkip();
  }, []);

  const handleEndSession = useCallback(async () => {
    if (stopping) return;
    setStopping(true);

    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    try {
      recognition?.stop();
    } catch {
      // recognition may already be stopped
    }

    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();

    await avatarProvider.destroy();

    const idToEnd = sessionId;
    if (idToEnd) {
      void fetch(`/session/${idToEnd}`, { method: "DELETE" }).catch((err) => {
        console.warn("DELETE /session failed:", err);
      });
    }

    setSessionId(null);
    setWsStatus("disconnected");
    setIsListening(false);
    setAvatarReady(false);
    setLastQuestion("");
    setInterimText("");
    setConfirmingStop(false);
    setConfirmingStart(false);
    setPhase("landing");
    setStopping(false);
  }, [sessionId, stopping]);

  const handleBackToLanding = useCallback(() => {
    setConfirmingStart(false);
    setPhase("landing");
  }, []);

  if (error) {
    return (
      <div style={pageCenter}>
        <div className="surface fade-in" style={{ padding: 20, maxWidth: 420, textAlign: "center" }}>
          <p style={{ color: "var(--danger)", fontSize: 13.5 }}>{error}</p>
        </div>
      </div>
    );
  }

  if (phase === "landing") {
    return (
      <div style={pageCenter}>
        <div className="fade-in" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18, maxWidth: 380, textAlign: "center" }}>
          <h1 style={{ fontSize: 22, fontWeight: 600 }}>BehavioralDummy</h1>
          <p style={{ color: "var(--text-dim)", fontSize: 14 }}>
            Click below to enter the interview view. Nothing is billed until you press Start.
          </p>
          <button
            onClick={() => setPhase("preview")}
            className="btn btn-primary"
            style={{ padding: "10px 22px" }}
          >
            Enter session
          </button>
          <a href="/admin" style={footerLink}>Manage stories →</a>
        </div>
      </div>
    );
  }

  const avatarState: AvatarState =
    phase === "running" ? (avatarReady ? "ready" : "connecting") : "idle";
  const recordDisabled = phase !== "running" || !avatarReady || wsStatus !== "connected";

  return (
    <div style={pageStack}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <h1 style={{ fontSize: 17, fontWeight: 600 }}>BehavioralDummy</h1>
        {phase === "running" && (
          <StatusBar
            wsStatus={wsStatus}
            lastQuestion={interimText || lastQuestion}
            isListening={isListening}
          />
        )}
      </header>

      <div className="fade-in" style={{ display: "flex", justifyContent: "center" }}>
        <AvatarView ref={avatarRef} state={avatarState} />
      </div>

      <div style={{ display: "flex", justifyContent: "center" }}>
        <RecordButton
          isListening={isListening}
          disabled={recordDisabled}
          onStartListening={handleStartListening}
          onStopListening={handleStopListening}
          onSkip={handleSkip}
        />
      </div>

      {phase === "running" && !sessionId && (
        <p style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center" }}>
          Initialising session…
        </p>
      )}

      {phase === "preview" && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
          {!confirmingStart ? (
            <>
              <button
                onClick={() => setConfirmingStart(true)}
                className="btn btn-primary"
                style={{ padding: "10px 24px" }}
              >
                Start
              </button>
              <p style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", maxWidth: 360 }}>
                Reserves a Simli avatar slot and opens the OpenAI + ElevenLabs pipeline. Real API
                credits will be used.
              </p>
              <button onClick={handleBackToLanding} className="btn btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }}>
                Back
              </button>
            </>
          ) : (
            <div className="surface fade-in" style={inlineConfirmStyle}>
              <p style={{ color: "var(--text)", fontSize: 13.5, margin: 0, textAlign: "center" }}>
                Start billing now?
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => setPhase("running")} className="btn btn-primary" style={{ padding: "7px 14px", fontSize: 13 }}>
                  Start
                </button>
                <button onClick={() => setConfirmingStart(false)} className="btn btn-ghost" style={{ padding: "7px 14px", fontSize: 13 }}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {phase === "running" && (
        <div style={{ display: "flex", justifyContent: "center" }}>
          {!confirmingStop ? (
            <button
              onClick={() => setConfirmingStop(true)}
              disabled={stopping}
              className="btn btn-danger-ghost"
              style={{ padding: "7px 14px", fontSize: 12.5 }}
            >
              End session
            </button>
          ) : (
            <div className="surface fade-in" style={inlineConfirmStyle}>
              <p style={{ color: "var(--text)", fontSize: 13.5, margin: 0, textAlign: "center" }}>
                End the session?
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={() => void handleEndSession()}
                  disabled={stopping}
                  className="btn btn-danger"
                  style={{ padding: "7px 14px", fontSize: 13 }}
                >
                  {stopping ? "Ending…" : "End"}
                </button>
                <button
                  onClick={() => setConfirmingStop(false)}
                  disabled={stopping}
                  className="btn btn-ghost"
                  style={{ padding: "7px 14px", fontSize: 13 }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <a href="/admin" style={{ ...footerLink, alignSelf: "center" }}>Manage stories →</a>
    </div>
  );
}

const pageCenter: React.CSSProperties = {
  minHeight: "100vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
};

const pageStack: React.CSSProperties = {
  minHeight: "100vh",
  maxWidth: 640,
  margin: "0 auto",
  padding: "28px 24px 40px",
  display: "flex",
  flexDirection: "column",
  gap: 22,
};

const inlineConfirmStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 10,
  padding: 14,
  maxWidth: 320,
};

const footerLink: React.CSSProperties = {
  color: "var(--text-muted)",
  fontSize: 12,
};
