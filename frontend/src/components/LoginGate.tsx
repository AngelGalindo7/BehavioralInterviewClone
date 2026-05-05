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
        <div className="fade-in" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
          <div className="spinner" />
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>Checking session…</p>
        </div>
      </div>
    );
  }

  if (state === "needs-login") {
    return (
      <div style={shellStyle}>
        <form onSubmit={handleSubmit} className="surface fade-in" style={formStyle}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, textAlign: "center" }}>
            <h1 className="gradient-text" style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.02em" }}>
              BehavioralDummy
            </h1>
            <p style={{ color: "var(--text-dim)", fontSize: 13, lineHeight: 1.5 }}>
              Enter passcode to continue.
            </p>
          </div>
          <input
            type="password"
            autoFocus
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            disabled={submitting}
            className="input"
            placeholder="Passcode"
          />
          {error && (
            <p
              style={{
                color: "var(--danger)",
                fontSize: 12,
                margin: 0,
                padding: "8px 10px",
                background: "var(--danger-soft)",
                border: "1px solid rgba(244, 63, 94, 0.30)",
                borderRadius: 8,
              }}
            >
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting || !passcode}
            className="btn btn-primary"
            style={{ width: "100%", padding: "11px 14px" }}
          >
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
  gap: 14,
  width: 360,
  maxWidth: "100%",
  padding: 28,
};
