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
    <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
      <button
        onClick={isListening ? onStopListening : onStartListening}
        disabled={disabled}
        className={`btn ${isListening ? "btn-danger pulse-ring" : "btn-primary"}`}
        style={{
          padding: "14px 30px",
          fontSize: 15,
          minWidth: 168,
          letterSpacing: 0.2,
        }}
      >
        {isListening ? (
          <>
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                borderRadius: 2,
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
        style={{ padding: "14px 18px", fontSize: 13 }}
      >
        Skip
        <SkipIcon />
      </button>
    </div>
  );
}

function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
    </svg>
  );
}

function SkipIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polygon points="5 4 15 12 5 20 5 4" />
      <line x1="19" y1="5" x2="19" y2="19" />
    </svg>
  );
}
