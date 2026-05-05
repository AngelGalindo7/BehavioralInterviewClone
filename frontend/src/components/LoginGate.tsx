import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { checkAuth, login } from "../lib/auth";

type State = "checking" | "needs-login" | "authed";

export default function LoginGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>("checking");
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    checkAuth()
      .then((ok) => {
        if (!cancelled) setState(ok ? "authed" : "needs-login");
      })
      .catch(() => {
        if (!cancelled) setState("needs-login");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(passcode);
      setState("authed");
      setPasscode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (state === "checking") {
    return (
      <div style={shellStyle}>
        <p style={{ color: "#6b7280", fontSize: 13 }}>Checking session…</p>
      </div>
    );
  }

  if (state === "needs-login") {
    return (
      <div style={shellStyle}>
        <form onSubmit={handleSubmit} style={formStyle}>
          <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>BehavioralDummy</h1>
          <p style={{ color: "#6b7280", fontSize: 13, margin: 0 }}>
            Enter passcode to continue.
          </p>
          <input
            type="password"
            autoFocus
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            disabled={submitting}
            style={inputStyle}
            placeholder="Passcode"
          />
          {error && <p style={{ color: "#ef4444", fontSize: 12, margin: 0 }}>{error}</p>}
          <button type="submit" disabled={submitting || !passcode} style={buttonStyle}>
            {submitting ? "Checking…" : "Unlock"}
          </button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}

const shellStyle: React.CSSProperties = {
  minHeight: "100vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
};

const formStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  width: 320,
  padding: 24,
  border: "1px solid #e5e7eb",
  borderRadius: 8,
};

const inputStyle: React.CSSProperties = {
  padding: "10px 12px",
  border: "1px solid #d1d5db",
  borderRadius: 6,
  fontSize: 14,
};

const buttonStyle: React.CSSProperties = {
  padding: "10px 12px",
  border: "none",
  borderRadius: 6,
  background: "#111827",
  color: "white",
  fontSize: 14,
  cursor: "pointer",
};
