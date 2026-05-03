import React from "react";

interface StatusBarProps {
  wsStatus: string;
  lastQuestion: string;
  isListening: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  connected: "#22c55e",
  reconnecting: "#f59e0b",
  disconnected: "#ef4444",
};

export default function StatusBar({ wsStatus, lastQuestion, isListening }: StatusBarProps) {
  const dot = STATUS_COLOR[wsStatus] ?? "#6b7280";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "12px 16px",
        background: "#1a1a1a",
        borderRadius: 8,
        fontSize: 13,
        color: "#9ca3af",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: dot,
            display: "inline-block",
          }}
        />
        <span>{wsStatus}</span>
        {isListening && (
          <span style={{ marginLeft: "auto", color: "#f87171", fontWeight: 600 }}>
            🔴 Listening…
          </span>
        )}
      </div>
      {lastQuestion && (
        <div style={{ fontStyle: "italic", color: "#6b7280" }}>
          Last: "{lastQuestion}"
        </div>
      )}
    </div>
  );
}
