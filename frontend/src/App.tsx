import React, { useCallback, useEffect, useRef, useState } from "react";
import AvatarView from "./components/AvatarView";
import RecordButton from "./components/RecordButton";
import StatusBar from "./components/StatusBar";
import { destroyAvatar, initSimliAvatar, interruptAvatar, sendAudioToAvatar } from "./lib/simliAvatar";
import { isSpeechRecognitionSupported, startSpeechRecognition } from "./lib/speechRecognition";
import { InterviewWebSocket } from "./lib/wsClient";

export default function App() {
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
  }, []);

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

  if (error) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <p style={{ color: "#ef4444", maxWidth: 400, textAlign: "center" }}>{error}</p>
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
    </div>
  );
}
