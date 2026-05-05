import React, { forwardRef } from "react";

interface AvatarViewProps {
  isReady: boolean;
}

const AvatarView = forwardRef<
  { video: HTMLVideoElement | null; audio: HTMLAudioElement | null },
  AvatarViewProps
>(({ isReady }, ref) => {
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

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        maxWidth: 480,
        aspectRatio: "1 / 1",
        borderRadius: 22,
        padding: 2,
        background: isReady
          ? "conic-gradient(from 220deg, rgba(16,185,129,0.85), rgba(96,165,250,0.55), rgba(244,63,94,0.45), rgba(16,185,129,0.85))"
          : "linear-gradient(140deg, rgba(255,255,255,0.10), rgba(255,255,255,0.03))",
        boxShadow: isReady
          ? "0 0 0 1px rgba(16,185,129,0.30), 0 24px 60px rgba(16,185,129,0.18)"
          : "0 20px 50px rgba(0,0,0,0.55)",
        transition: "background 0.4s ease, box-shadow 0.4s ease",
      }}
    >
      <div
        style={{
          position: "relative",
          width: "100%",
          height: "100%",
          background: "linear-gradient(180deg, #0d1018 0%, #0a0c12 100%)",
          borderRadius: 20,
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
            transition: "opacity 0.5s ease",
          }}
        />
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <audio ref={audioRef} autoPlay />

        {/* Subtle vignette over the video */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            background:
              "radial-gradient(120% 80% at 50% 30%, transparent 55%, rgba(0,0,0,0.45) 100%)",
          }}
        />

        {!isReady && (
          <div
            className="fade-in"
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 14,
              color: "var(--text-dim)",
              fontSize: 13,
              letterSpacing: 0.2,
            }}
          >
            <div className="spinner" />
            <span>Connecting avatar…</span>
          </div>
        )}

        {isReady && (
          <div
            style={{
              position: "absolute",
              top: 14,
              left: 14,
              display: "inline-flex",
              alignItems: "center",
              gap: 7,
              padding: "5px 10px",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 0.4,
              textTransform: "uppercase",
              color: "#a7f3d0",
              background: "rgba(6, 23, 18, 0.72)",
              border: "1px solid rgba(16, 185, 129, 0.40)",
              borderRadius: 999,
              backdropFilter: "blur(6px)",
            }}
          >
            <span
              className="pulse-dot"
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: "var(--accent)",
                boxShadow: "0 0 8px var(--accent)",
              }}
            />
            Live
          </div>
        )}
      </div>
    </div>
  );
});

AvatarView.displayName = "AvatarView";
export default AvatarView;
