interface StatusBarProps {
  wsStatus: string;
  lastQuestion: string;
  isListening: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  connected: "#10b981",
  reconnecting: "#fbbf24",
  disconnected: "#f43f5e",
};

export default function StatusBar({ wsStatus, lastQuestion, isListening }: StatusBarProps) {
  const dot = STATUS_COLOR[wsStatus] ?? "#6b7280";
  const isConnected = wsStatus === "connected";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="pill" style={{ textTransform: "capitalize" }}>
          <span
            className={isConnected ? "pulse-dot" : ""}
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: dot,
              boxShadow: `0 0 8px ${dot}`,
              display: "inline-block",
            }}
          />
          {wsStatus}
        </span>
        {isListening && (
          <span
            className="pill fade-in"
            style={{
              color: "#fecaca",
              borderColor: "rgba(244, 63, 94, 0.40)",
              background: "rgba(244, 63, 94, 0.10)",
            }}
          >
            <span
              className="pulse-dot"
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "#f43f5e",
                boxShadow: "0 0 8px #f43f5e",
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
            fontStyle: "italic",
            color: "var(--text-muted)",
            fontSize: 12,
            maxWidth: 360,
            textAlign: "right",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={lastQuestion}
        >
          “{lastQuestion}”
        </div>
      )}
    </div>
  );
}
