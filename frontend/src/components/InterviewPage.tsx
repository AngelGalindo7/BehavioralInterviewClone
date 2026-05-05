import { useCallback, useEffect, useRef, useState } from "react";
import AvatarView from "./AvatarView";
import RecordButton from "./RecordButton";
import StatusBar from "./StatusBar";
import { destroyAvatar, initSimliAvatar, interruptAvatar, sendAudioToAvatar } from "../lib/simliAvatar";
import {
  isSpeechRecognitionSupported,
  startSpeechRecognition,
  type SpeechRecognition,
} from "../lib/speechRecognition";
import { InterviewWebSocket } from "../lib/wsClient";

export default function InterviewPage() {
  const [started, setStarted] = useState(false);
  const [confirming, setConfirming] = useState(false);
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
    if (!started) return;

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
  }, [started]);

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
    setConfirming(false);
    setStarted(false);
    setStopping(false);
  }, [sessionId, stopping]);

  if (error) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <p style={{ color: "#ef4444", maxWidth: 400, textAlign: "center" }}>{error}</p>
      </div>
    );
  }

  if (!started) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 20,
          padding: 24,
          textAlign: "center",
        }}
      >
        <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.5px" }}>
          BehavioralDummy
        </h1>
        <p style={{ color: "#9ca3af", maxWidth: 420, fontSize: 14, lineHeight: 1.5 }}>
          Starting the session reserves a Simli avatar slot and opens the OpenAI + ElevenLabs
          pipeline. Each session costs real API credits — only start when you're ready to interview.
        </p>

        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            style={{
              padding: "12px 28px",
              fontSize: 15,
              fontWeight: 600,
              color: "#fff",
              background: "#22c55e",
              border: "none",
              borderRadius: 8,
              cursor: "pointer",
            }}
          >
            Start session
          </button>
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 12,
              padding: 20,
              border: "1px solid #374151",
              borderRadius: 10,
              background: "#111827",
              maxWidth: 420,
            }}
          >
            <p style={{ color: "#fbbf24", fontSize: 14, margin: 0 }}>
              Are you sure? This will immediately start billing Simli, OpenAI, and ElevenLabs.
            </p>
            <div style={{ display: "flex", gap: 10 }}>
              <button
                onClick={() => setStarted(true)}
                style={{
                  padding: "10px 20px",
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#fff",
                  background: "#dc2626",
                  border: "none",
                  borderRadius: 6,
                  cursor: "pointer",
                }}
              >
                Yes, start
              </button>
              <button
                onClick={() => setConfirming(false)}
                style={{
                  padding: "10px 20px",
                  fontSize: 14,
                  fontWeight: 600,
                  color: "#e5e7eb",
                  background: "transparent",
                  border: "1px solid #4b5563",
                  borderRadius: 6,
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        <a href="/admin" style={{ color: "#6b7280", fontSize: 12 }}>
          Manage stories →
        </a>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 24,
        padding: 24,
      }}
    >
      <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.5px" }}>
        BehavioralDummy
      </h1>

      <AvatarView ref={avatarRef} isReady={avatarReady} />

      <RecordButton
        isListening={isListening}
        disabled={!avatarReady || wsStatus !== "connected"}
        onStartListening={handleStartListening}
        onStopListening={handleStopListening}
        onSkip={handleSkip}
      />

      <StatusBar
        wsStatus={wsStatus}
        lastQuestion={interimText || lastQuestion}
        isListening={isListening}
      />

      {!sessionId && <p style={{ color: "#6b7280", fontSize: 13 }}>Initialising session…</p>}

      {!confirmingStop ? (
        <button
          onClick={() => setConfirmingStop(true)}
          disabled={stopping}
          style={{
            padding: "8px 18px",
            fontSize: 13,
            fontWeight: 600,
            color: "#fca5a5",
            background: "transparent",
            border: "1px solid #7f1d1d",
            borderRadius: 6,
            cursor: stopping ? "not-allowed" : "pointer",
            opacity: stopping ? 0.6 : 1,
          }}
        >
          End session
        </button>
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 10,
            padding: 16,
            border: "1px solid #7f1d1d",
            borderRadius: 10,
            background: "#1f1011",
            maxWidth: 420,
          }}
        >
          <p style={{ color: "#fca5a5", fontSize: 13, margin: 0, textAlign: "center" }}>
            End the session? This will release the Simli avatar slot and close the pipeline.
            Restarting will charge a new token.
          </p>
          <div style={{ display: "flex", gap: 10 }}>
            <button
              onClick={() => void handleEndSession()}
              disabled={stopping}
              style={{
                padding: "8px 16px",
                fontSize: 13,
                fontWeight: 600,
                color: "#fff",
                background: "#dc2626",
                border: "none",
                borderRadius: 6,
                cursor: stopping ? "not-allowed" : "pointer",
                opacity: stopping ? 0.6 : 1,
              }}
            >
              {stopping ? "Ending…" : "Yes, end session"}
            </button>
            <button
              onClick={() => setConfirmingStop(false)}
              disabled={stopping}
              style={{
                padding: "8px 16px",
                fontSize: 13,
                fontWeight: 600,
                color: "#e5e7eb",
                background: "transparent",
                border: "1px solid #4b5563",
                borderRadius: 6,
                cursor: stopping ? "not-allowed" : "pointer",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <a href="/admin" style={{ color: "#6b7280", fontSize: 12 }}>
        Manage stories →
      </a>
    </div>
  );
}
