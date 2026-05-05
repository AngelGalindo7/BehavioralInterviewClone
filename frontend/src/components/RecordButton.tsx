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
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <button
        onClick={isListening ? onStopListening : onStartListening}
        disabled={disabled}
        className={`btn ${isListening ? "btn-danger" : "btn-primary"}`}
        style={{ padding: "10px 22px", minWidth: 130 }}
      >
        {isListening ? (
          <>
            <span
              style={{
                display: "inline-block",
                width: 9,
                height: 9,
                borderRadius: 1,
                background: "currentColor",
              }}
            />
            Stop
          </>
        ) : (
          <>
            <MicIcon />
            Ask
          </>
        )}
      </button>

      <button
        onClick={onSkip}
        disabled={disabled || isListening}
        title="Interrupt current response"
        className="btn btn-ghost"
        style={{ padding: "10px 14px" }}
      >
        Skip
      </button>
    </div>
  );
}

function MicIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
    </svg>
  );
}
