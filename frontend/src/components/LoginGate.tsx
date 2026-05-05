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
        <div className="fade-in" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
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
          <div style={{ display: "flex", flexDirection: "column", gap: 4, textAlign: "center" }}>
            <h1 style={{ fontSize: 18, fontWeight: 600 }}>BehavioralDummy</h1>
            <p style={{ color: "var(--text-dim)", fontSize: 13 }}>Enter passcode to continue.</p>
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
                color: "#fca5a5",
                fontSize: 12,
                margin: 0,
                padding: "7px 10px",
                background: "var(--danger-soft)",
                border: "1px solid rgba(229, 72, 77, 0.25)",
                borderRadius: 6,
              }}
            >
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting || !passcode}
            className="btn btn-primary"
            style={{ width: "100%", padding: "9px 12px" }}
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
  gap: 12,
  width: 340,
  maxWidth: "100%",
  padding: 22,
};
