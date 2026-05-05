import { useCallback, useEffect, useRef, useState } from "react";
import AvatarView, { type AvatarState } from "./AvatarView";
import RecordButton from "./RecordButton";
import StatusBar from "./StatusBar";
import { destroyAvatar, initSimliAvatar, interruptAvatar, sendAudioToAvatar } from "../lib/simliAvatar";
import {
  isSpeechRecognitionSupported,
  startSpeechRecognition,
  type SpeechRecognition,
} from "../lib/speechRecognition";
import { InterviewWebSocket } from "../lib/wsClient";

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
  const recognitionRef = useRef<SpeechRecognition | null>(null);
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

        const tokenResp = await fetch("/simli/token", { method: "POST" });
        if (!tokenResp.ok) throw new Error(`POST /simli/token failed (${tokenResp.status})`);
        const tokenData = await tokenResp.json();
        const sessionToken: string = tokenData.session_token;
        const iceServers: RTCIceServer[] = tokenData.ice_servers ?? [];
        if (!sessionToken) throw new Error("Simli token response missing session_token");

        const refs = avatarRef.current;
        if (!refs?.video || !refs?.audio) {
          throw new Error("Avatar video/audio elements not mounted");
        }

        await initSimliAvatar({
          sessionToken,
          iceServers,
          videoEl: refs.video,
          audioEl: refs.audio,
        });
        if (cancelled) {
          await destroyAvatar();
          return;
        }
        setAvatarReady(true);

        const ws = new InterviewWebSocket(
          session_id,
          (pcm, immediate) => sendAudioToAvatar(pcm, immediate),
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
      void destroyAvatar();
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
    );
  }, [avatarReady]);

  const handleStopListening = useCallback(() => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setIsListening(false);
    setInterimText("");
  }, []);

  const handleSkip = useCallback(() => {
    interruptAvatar();
    wsRef.current?.sendSkip();
  }, []);

  const handleEndSession = useCallback(async () => {
    if (stopping) return;
    setStopping(true);

    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    try {
      recognition?.abort();
    } catch {
      // recognition may already be stopped
    }

    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();

    await destroyAvatar();

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
        <div className="surface fade-in" style={{ padding: 24, maxWidth: 440, textAlign: "center" }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 999,
              margin: "0 auto 12px",
              display: "grid",
              placeItems: "center",
              background: "var(--danger-soft)",
              color: "var(--danger)",
              fontSize: 18,
              fontWeight: 700,
            }}
          >
            !
          </div>
          <p style={{ color: "var(--danger)", fontSize: 14, lineHeight: 1.5 }}>{error}</p>
        </div>
      </div>
    );
  }

  if (phase === "landing") {
    return (
      <div style={pageCenter}>
        <div
          className="surface fade-in"
          style={{
            padding: "36px 32px",
            maxWidth: 480,
            width: "100%",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 18,
            textAlign: "center",
          }}
        >
          <span className="pill" style={{ color: "var(--text-dim)" }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: "var(--accent)",
                boxShadow: "0 0 8px var(--accent)",
              }}
            />
            Real-time AI interview clone
          </span>
          <h1 className="gradient-text" style={titleStyle}>BehavioralDummy</h1>
          <p style={leadStyle}>
            Click below to enter the interview view. Nothing is billed until you press Start
            from the next screen.
          </p>

          <button
            onClick={() => setPhase("preview")}
            className="btn btn-primary"
            style={{ padding: "13px 30px", fontSize: 15 }}
          >
            Enter session
          </button>

          <a href="/admin" style={footerLink}>
            Manage stories →
          </a>
        </div>
      </div>
    );
  }

  const avatarState: AvatarState =
    phase === "running" ? (avatarReady ? "ready" : "connecting") : "idle";
  const recordDisabled = phase !== "running" || !avatarReady || wsStatus !== "connected";

  return (
    <div style={pageStack}>
      <header
        className="fade-in"
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}
      >
        <h1 className="gradient-text" style={{ ...titleStyle, fontSize: 22 }}>
          BehavioralDummy
        </h1>
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

      <div className="fade-in" style={{ display: "flex", justifyContent: "center" }}>
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
        <div style={{ display: "flex", justifyContent: "center" }}>
          {!confirmingStart ? (
            <div
              className="fade-in"
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 8,
                maxWidth: 440,
                textAlign: "center",
              }}
            >
              <button
                onClick={() => setConfirmingStart(true)}
                className="btn btn-primary"
                style={{ padding: "13px 32px", fontSize: 15 }}
              >
                Start interview
              </button>
              <p style={{ color: "var(--text-muted)", fontSize: 12, lineHeight: 1.5, margin: 0 }}>
                This reserves a Simli avatar slot and opens the OpenAI + ElevenLabs pipeline.
                Real API credits will be used.
              </p>
              <button
                onClick={handleBackToLanding}
                className="btn btn-ghost"
                style={{ padding: "6px 14px", fontSize: 12, marginTop: 4 }}
              >
                ← Back
              </button>
            </div>
          ) : (
            <div
              className="fade-in"
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 12,
                padding: 18,
                border: "1px solid rgba(251, 191, 36, 0.35)",
                borderRadius: 12,
                background: "rgba(251, 191, 36, 0.06)",
                maxWidth: 440,
              }}
            >
              <p style={{ color: "var(--warn)", fontSize: 13.5, lineHeight: 1.5, margin: 0, textAlign: "center" }}>
                Are you sure? This will immediately start billing Simli, OpenAI, and ElevenLabs.
              </p>
              <div style={{ display: "flex", gap: 10 }}>
                <button onClick={() => setPhase("running")} className="btn btn-danger">
                  Yes, start
                </button>
                <button onClick={() => setConfirmingStart(false)} className="btn btn-ghost">
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
              style={{ padding: "8px 18px", fontSize: 13 }}
            >
              End session
            </button>
          ) : (
            <div
              className="fade-in"
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 12,
                padding: 16,
                border: "1px solid rgba(244, 63, 94, 0.35)",
                borderRadius: 12,
                background: "rgba(244, 63, 94, 0.06)",
                maxWidth: 440,
              }}
            >
              <p style={{ color: "#fda4af", fontSize: 13, margin: 0, textAlign: "center", lineHeight: 1.5 }}>
                End the session? This will release the Simli avatar slot and close the pipeline.
                Restarting will charge a new token.
              </p>
              <div style={{ display: "flex", gap: 10 }}>
                <button
                  onClick={() => void handleEndSession()}
                  disabled={stopping}
                  className="btn btn-danger"
                  style={{ padding: "8px 16px", fontSize: 13 }}
                >
                  {stopping ? "Ending…" : "Yes, end session"}
                </button>
                <button
                  onClick={() => setConfirmingStop(false)}
                  disabled={stopping}
                  className="btn btn-ghost"
                  style={{ padding: "8px 16px", fontSize: 13 }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <a href="/admin" style={{ ...footerLink, alignSelf: "center" }}>
        Manage stories →
      </a>
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
  maxWidth: 720,
  margin: "0 auto",
  padding: "32px 24px 48px",
  display: "flex",
  flexDirection: "column",
  gap: 24,
};

const titleStyle: React.CSSProperties = {
  fontSize: 30,
  fontWeight: 800,
  letterSpacing: "-0.025em",
  lineHeight: 1.1,
};

const leadStyle: React.CSSProperties = {
  color: "var(--text-dim)",
  maxWidth: 420,
  fontSize: 14,
  lineHeight: 1.55,
};

const footerLink: React.CSSProperties = {
  color: "var(--text-muted)",
  fontSize: 12,
  letterSpacing: 0.2,
};
