import React from "react";

interface RecordButtonProps {
  isListening: boolean;
  disabled: boolean;
  onStartListening: () => void;
  onStopListening: () => void;
  onSkip: () => void;
}

export default function RecordButton({
  isListening,
  disabled,
  onStartListening,
  onStopListening,
  onSkip,
}: RecordButtonProps) {
  return (
    <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
      <button
        onClick={isListening ? onStopListening : onStartListening}
        disabled={disabled}
        style={{
          padding: "14px 28px",
          borderRadius: 8,
          border: "none",
          background: isListening ? "#ef4444" : "#22c55e",
          color: "#fff",
          fontSize: 16,
          fontWeight: 600,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          transition: "background 0.15s",
          minWidth: 140,
        }}
      >
        {isListening ? "⏹ Stop" : "🎙 Ask"}
      </button>

      <button
        onClick={onSkip}
        disabled={disabled || isListening}
        title="Interrupt current response"
        style={{
          padding: "14px 20px",
          borderRadius: 8,
          border: "1px solid #374151",
          background: "transparent",
          color: "#9ca3af",
          fontSize: 14,
          cursor: disabled || isListening ? "not-allowed" : "pointer",
          opacity: disabled || isListening ? 0.4 : 1,
        }}
      >
        Skip ⏭
      </button>
    </div>
  );
}
