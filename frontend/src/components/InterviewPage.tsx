import { useCallback, useEffect, useRef, useState } from "react";
import AvatarView, { type AvatarState } from "./AvatarView";
import RecordButton from "./RecordButton";
import StatusBar from "./StatusBar";
import { getAvatarProvider, type AvatarProviderName } from "../lib/activeAvatar";
import type { AvatarProvider } from "../lib/avatarProvider";
import {
  isSpeechRecognitionSupported,
  startSpeechRecognition,
  type RecognitionHandle,
} from "../lib/speechRecognition";
import { InterviewWebSocket } from "../lib/wsClient";
import { beginTurn, setVideoElement } from "../lib/timing";
import { apiUrl } from "../lib/api";

// Fire onFinal after this many ms of silence after the last interim result
// instead of waiting for Chrome's internal end-of-speech timer (~700–1200 ms).
const STT_SILENCE_FALLBACK_MS = 600;

// The UI is HeyGen-only. The backend still registers other providers, but the
// interview frontend no longer offers a choice — it always drives HeyGen.
const AVATAR_PROVIDER: AvatarProviderName = "heygen";

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
  const [turnError, setTurnError] = useState<string | null>(null);
  const turnErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const wsRef = useRef<InterviewWebSocket | null>(null);
  const recognitionRef = useRef<RecognitionHandle | null>(null);
  const avatarRef = useRef<{ video: HTMLVideoElement | null; audio: HTMLAudioElement | null }>(null);
  const avatarProviderRef = useRef<AvatarProvider | null>(null);
  const greetedRef = useRef(false);

  useEffect(() => {
    if (phase !== "running") return;

    if (!isSpeechRecognitionSupported()) {
      setError("WebSpeech API is not supported. Please use Google Chrome.");
      return;
    }

    let cancelled = false;

    (async () => {
      try {
        const sessionResp = await fetch(apiUrl("/session/"), { method: "POST", credentials: "include" });
        if (!sessionResp.ok) throw new Error(`POST /session failed (${sessionResp.status})`);
        const { session_id } = await sessionResp.json();
        if (cancelled) return;
        setSessionId(session_id);

        const tokenResp = await fetch(
          apiUrl(`/avatar/session?provider=${encodeURIComponent(AVATAR_PROVIDER)}`),
          { method: "POST", credentials: "include" },
        );
        if (!tokenResp.ok) throw new Error(`POST /avatar/session failed (${tokenResp.status})`);
        const tokenData = await tokenResp.json();
        const sessionToken: string = tokenData.session_token;
        const iceServers: RTCIceServer[] = tokenData.ice_servers ?? [];
        // HeyGen-only fields (undefined for Simli). url is the LiveKit URL;
        // avatarSessionId routes streaming.task/stop on the backend.
        const url: string | undefined = tokenData.url;
        const avatarSessionId: string | undefined = tokenData.session_id;
        if (!sessionToken) throw new Error("Avatar session response missing session_token");

        const refs = avatarRef.current;
        if (!refs?.video || !refs?.audio) {
          throw new Error("Avatar video/audio elements not mounted");
        }
        // Hand the avatar <video> to the timing module so its
        // requestVideoFrameCallback can stamp first_frame_rendered for the
        // waterfall. Cleared in the effect's teardown below.
        setVideoElement(refs.video);

        const provider = getAvatarProvider(AVATAR_PROVIDER);
        avatarProviderRef.current = provider;

        await provider.init({
          sessionToken,
          iceServers,
          videoEl: refs.video,
          audioEl: refs.audio,
          url,
        });
        if (cancelled) {
          await provider.destroy();
          avatarProviderRef.current = null;
          return;
        }
        setAvatarReady(true);

        const ws = new InterviewWebSocket(
          session_id,
          (pcm, immediate) => provider.sendAudio(pcm, immediate),
          (status) => setWsStatus(status),
          {
            provider: AVATAR_PROVIDER,
            avatarSessionId,
            onTurnError: (msg) => {
              if (turnErrorTimerRef.current) clearTimeout(turnErrorTimerRef.current);
              setTurnError(msg);
              turnErrorTimerRef.current = setTimeout(() => setTurnError(null), 5000);
            },
          },
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
      void avatarProviderRef.current?.destroy();
      avatarProviderRef.current = null;
      setVideoElement(null);
    };
  }, [phase]);

  // Deterministic opener. Fires once the avatar has connected AND the WS is
  // live — both are required so the greeting PCM has somewhere to play. The ref
  // guard (reset on End session) keeps a status flap from re-greeting; the
  // backend has its own once-only guard as a second line of defence.
  useEffect(() => {
    if (avatarReady && wsStatus === "connected" && !greetedRef.current) {
      greetedRef.current = true;
      wsRef.current?.sendGreeting();
    }
  }, [avatarReady, wsStatus]);

  // Tab-close cleanup. Without this, abandoned sessions linger as
  // ended_at IS NULL rows and burn up to ~60s of Simli idle billing per
  // session before Simli's own timeout kicks in. fetch keepalive (DELETE)
  // is the modern replacement for navigator.sendBeacon — sendBeacon is
  // POST-only, keepalive supports any verb and survives page unload.
  // Backend has its own WS-finally and reaper safety nets if this fails.
  useEffect(() => {
    if (!sessionId) return;
    const id = sessionId;
    const fire = () => {
      try {
        void fetch(apiUrl(`/session/${id}`), { method: "DELETE", keepalive: true, credentials: "include" });
      } catch {
        // Browser may reject keepalive in narrow conditions (>64KB body, etc.);
        // the backend reaper closes the orphan within session_reaper_interval.
      }
    };
    window.addEventListener("pagehide", fire);
    return () => window.removeEventListener("pagehide", fire);
  }, [sessionId]);

  const handleStartListening = useCallback(() => {
    if (!avatarReady) return;
    setInterimText("");
    setIsListening(true);
    // Open a fresh per-turn timing record. Subsequent marks from
    // speechRecognition.ts and wsClient.ts attach to this turn until
    // first_frame_rendered fires and ships the summary.
    beginTurn();
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
    avatarProviderRef.current?.interrupt();
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

    await avatarProviderRef.current?.destroy();
    avatarProviderRef.current = null;

    const idToEnd = sessionId;
    if (idToEnd) {
      void fetch(apiUrl(`/session/${idToEnd}`), { method: "DELETE", credentials: "include" }).catch((err) => {
        console.warn("DELETE /session failed:", err);
      });
    }

    greetedRef.current = false;
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
          <h1 style={{ fontSize: 21, fontWeight: 600, letterSpacing: "-0.01em" }}>Behavioral Clone</h1>
          <button
            onClick={() => setPhase("preview")}
            className="btn btn-primary"
            style={{ padding: "14px 32px" }}
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
    <>
      <div style={{ position: "fixed", top: 20, left: 24, zIndex: 10 }}>
        <h1 style={{ fontSize: 24, fontWeight: 600, letterSpacing: "-0.01em" }}>Behavioral Clone</h1>
      </div>
      <div style={pageStack}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
          {phase === "running" && (
            <div style={{ width: "100%", maxWidth: 460, marginBottom: 8 }}>
              <StatusBar
                wsStatus={wsStatus}
                lastQuestion={interimText || lastQuestion}
                isListening={isListening}
              />
            </div>
          )}
          <div className="fade-in" style={{ width: "100%", display: "flex", justifyContent: "center" }}>
            <AvatarView ref={avatarRef} state={avatarState} />
          </div>
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

      {phase === "running" && turnError && (
        <div style={{ display: "flex", justifyContent: "center" }}>
          <div
            className="fade-in"
            style={{
              color: "var(--danger)",
              fontSize: 12.5,
              textAlign: "center",
              padding: "6px 14px",
              background: "rgba(229, 72, 77, 0.07)",
              border: "1px solid rgba(229, 72, 77, 0.25)",
              borderRadius: 6,
            }}
          >
            {turnError}
          </div>
        </div>
      )}

      {phase === "preview" && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
          {!confirmingStart ? (
            <>
              <button
                onClick={() => setConfirmingStart(true)}
                className="btn btn-primary"
                style={{ padding: "14px 32px" }}
              >
                Start
              </button>
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
              style={{ padding: "14px 22px" }}
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
    </>
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
  padding: "72px 24px 40px",
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
