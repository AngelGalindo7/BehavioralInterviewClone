interface StatusBarProps {
  wsStatus: string;
  lastQuestion: string;
  isListening: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  connected: "#4ade80",
  reconnecting: "#f5a524",
  disconnected: "#e5484d",
};

export default function StatusBar({ wsStatus, lastQuestion, isListening }: StatusBarProps) {
  const dot = STATUS_COLOR[wsStatus] ?? "var(--text-muted)";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span className="pill" style={{ textTransform: "capitalize" }}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: dot,
              display: "inline-block",
            }}
          />
          {wsStatus}
        </span>
        {isListening && (
          <span
            className="pill fade-in"
            style={{
              color: "#fca5a5",
              borderColor: "rgba(229, 72, 77, 0.30)",
              background: "rgba(229, 72, 77, 0.06)",
            }}
          >
            <span
              className="pulse-dot"
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--danger)",
                display: "inline-block",
              }}
            />
            Listening
          </span>
        )}
      </div>
      {lastQuestion && (
        <div
          className="fade-in"
          style={{
            color: "var(--text-muted)",
            fontSize: 12,
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
