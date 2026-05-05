import React, { forwardRef } from "react";

export type AvatarState = "idle" | "connecting" | "ready";

interface AvatarViewProps {
  state: AvatarState;
}

const AvatarView = forwardRef<
  { video: HTMLVideoElement | null; audio: HTMLAudioElement | null },
  AvatarViewProps
>(({ state }, ref) => {
  const videoRef = React.useRef<HTMLVideoElement>(null);
  const audioRef = React.useRef<HTMLAudioElement>(null);

  React.useImperativeHandle(ref, () => ({
    get video() {
      return videoRef.current;
    },
    get audio() {
      return audioRef.current;
    },
  }));

  const isReady = state === "ready";

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        maxWidth: 460,
        aspectRatio: "1 / 1",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 12,
        overflow: "hidden",
      }}
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          opacity: isReady ? 1 : 0,
          transition: "opacity 0.4s ease",
        }}
      />
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio ref={audioRef} autoPlay />

      {state === "idle" && (
        <div style={overlayStyle}>
          <AvatarSilhouette />
          <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
            Avatar will appear once you start
          </span>
        </div>
      )}

      {state === "connecting" && (
        <div style={overlayStyle}>
          <div className="spinner" />
          <span style={{ color: "var(--text-dim)", fontSize: 13 }}>Connecting…</span>
        </div>
      )}
    </div>
  );
});

const overlayStyle: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 12,
  padding: 24,
  textAlign: "center",
};

function AvatarSilhouette() {
  return (
    <svg width="44" height="44" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"
         style={{ color: "var(--text-muted)", opacity: 0.5 }} aria-hidden>
      <circle cx="12" cy="9" r="3.5" />
      <path d="M4.5 20c1-3.5 4-5.5 7.5-5.5s6.5 2 7.5 5.5" />
    </svg>
  );
}

AvatarView.displayName = "AvatarView";
export default AvatarView;
