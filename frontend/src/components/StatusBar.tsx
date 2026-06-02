interface StatusBarProps {
  wsStatus: string;
  lastQuestion: string;
  isListening: boolean;
}

const STATUS_STYLE: Record<string, { dot: string; glow: string }> = {
  connected:    { dot: "#46C285", glow: "rgba(70, 194, 133, 0.28)" },
  reconnecting: { dot: "#6D97EF", glow: "rgba(109, 151, 239, 0.28)" },
  disconnected: { dot: "#E0726A", glow: "rgba(224, 114, 106, 0.28)" },
};

export default function StatusBar({ wsStatus, lastQuestion, isListening }: StatusBarProps) {
  const s = STATUS_STYLE[wsStatus] ?? { dot: "var(--text-3)", glow: "transparent" };
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span className="pill" style={{ textTransform: "capitalize" }}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: s.dot,
              boxShadow: `0 0 0 3px ${s.glow}`,
              display: "inline-block",
              flexShrink: 0,
            }}
          />
          {wsStatus}
        </span>
        {isListening && (
          <span
            className="pill fade-in"
            style={{
              color: "#E0726A",
              borderColor: "rgba(224, 114, 106, 0.30)",
              background: "rgba(224, 114, 106, 0.06)",
            }}
          >
            <span
              className="pulse-dot"
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "#E0726A",
                boxShadow: "0 0 0 3px rgba(224, 114, 106, 0.28)",
                display: "inline-block",
                flexShrink: 0,
              }}
            />
            Listening
          </span>
        )}
      </div>
      {lastQuestion && (
        <div
          className="fade-in text-mono"
          style={{
            color: "var(--text-3)",
            maxWidth: 320,
            textAlign: "right",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={lastQuestion}
        >
          {lastQuestion}
        </div>
      )}
    </div>
  );
}
