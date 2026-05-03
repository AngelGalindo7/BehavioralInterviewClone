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
        background: "#1a1a1a",
        borderRadius: 12,
        overflow: "hidden",
        border: isReady ? "2px solid #22c55e" : "2px solid #374151",
      }}
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio ref={audioRef} autoPlay />
      {!isReady && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#9ca3af",
            fontSize: 14,
          }}
        >
          Connecting avatar…
        </div>
      )}
    </div>
  );
});

AvatarView.displayName = "AvatarView";
export default AvatarView;
