import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  deleteAnecdote,
  listAnecdotes,
  reindex,
  upsertAnecdote,
  type AnecdoteSummary,
} from "../lib/adminApi";
import { logout } from "../lib/auth";

export default function AdminPage() {
  const [items, setItems] = useState<AnecdoteSummary[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setItems(await listAnecdotes());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await upsertAnecdote(title, content);
      setMessage(`Saved ${result.source_file} — ${result.chunks_inserted} chunk(s).`);
      setTitle("");
      setContent("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (sourceFile: string) => {
    if (!confirm(`Delete ${sourceFile}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteAnecdote(sourceFile);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleReindex = async () => {
    setReindexing(true);
    setError(null);
    setMessage(null);
    try {
      const result = await reindex();
      setMessage(`Index rebuilt in ${result.elapsed_ms} ms.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setReindexing(false);
    }
  };

  const handleLogout = async () => {
    await logout();
    window.location.reload();
  };

  return (
    <div style={pageStyle}>
      <header style={headerStyle} className="fade-in">
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h1 className="gradient-text" style={{ fontSize: 26, fontWeight: 800, letterSpacing: "-0.02em" }}>
            Stories
          </h1>
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
            Manage the anecdotes that feed retrieval.
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <a href="/" className="btn btn-ghost" style={{ padding: "8px 14px", fontSize: 13 }}>
            ← Interview
          </a>
          <button onClick={handleLogout} className="btn btn-ghost" style={{ padding: "8px 14px", fontSize: 13 }}>
            Sign out
          </button>
        </div>
      </header>

      <form onSubmit={handleSubmit} className="surface fade-in" style={cardStyle}>
        <label style={labelStyle}>
          Title
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={busy}
            placeholder="e.g. Resolved a production outage"
            className="input"
            required
          />
        </label>
        <label style={labelStyle}>
          Story (markdown — Situation / Task / Action / Result)
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            disabled={busy}
            rows={14}
            className="input"
            style={{
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: 13,
              lineHeight: 1.55,
              resize: "vertical",
            }}
            required
          />
        </label>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="submit"
            disabled={busy || !title || !content}
            className="btn btn-primary"
            style={{ padding: "9px 18px", fontSize: 13 }}
          >
            {busy ? "Saving…" : "Save story"}
          </button>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Same title overwrites the existing story.
          </span>
        </div>
      </form>

      {message && (
        <div
          className="fade-in"
          style={msgStyle("#a7f3d0", "rgba(16, 185, 129, 0.10)", "rgba(16, 185, 129, 0.30)")}
        >
          {message}
        </div>
      )}
      {error && (
        <div
          className="fade-in"
          style={msgStyle("#fda4af", "rgba(244, 63, 94, 0.10)", "rgba(244, 63, 94, 0.30)")}
        >
          {error}
        </div>
      )}

      <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h2 style={sectionHeading}>
          Ingested stories
          <span className="pill" style={{ marginLeft: 8 }}>{items.length}</span>
        </h2>
        {items.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--text-muted)" }}>No stories yet.</p>
        ) : (
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              margin: 0,
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            {items.map((item) => (
              <li key={item.source_file} className="surface" style={rowStyle}>
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 14,
                      fontWeight: 500,
                      color: "var(--text)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.source_file}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    {item.chunks} chunk(s) · {new Date(item.created_at).toLocaleString()}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(item.source_file)}
                  disabled={busy}
                  className="btn btn-danger-ghost"
                  style={{ padding: "6px 12px", fontSize: 12 }}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="surface fade-in" style={cardStyle}>
        <h2 style={sectionHeading}>Rebuild index</h2>
        <p style={{ fontSize: 12.5, color: "var(--text-dim)", margin: 0, lineHeight: 1.5 }}>
          Run this after a batch of edits — not after every save. Briefly pauses queries; do not run during a live interview.
        </p>
        <button
          onClick={handleReindex}
          disabled={reindexing}
          className="btn btn-ghost"
          style={{ alignSelf: "flex-start", padding: "9px 16px", fontSize: 13 }}
        >
          {reindexing ? "Rebuilding…" : "Rebuild IVFFlat index"}
        </button>
      </section>
    </div>
  );
}

const pageStyle: React.CSSProperties = {
  maxWidth: 760,
  margin: "0 auto",
  padding: "32px 24px 56px",
  display: "flex",
  flexDirection: "column",
  gap: 22,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-end",
  justifyContent: "space-between",
  gap: 16,
  flexWrap: "wrap",
};

const cardStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
  padding: 20,
};

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  fontSize: 12.5,
  fontWeight: 500,
  color: "var(--text-dim)",
  letterSpacing: 0.2,
};

const sectionHeading: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 600,
  margin: 0,
  color: "var(--text)",
  display: "inline-flex",
  alignItems: "center",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: 14,
  padding: "12px 14px",
  borderRadius: 12,
};

const msgStyle = (color: string, bg: string, border: string): React.CSSProperties => ({
  padding: "10px 14px",
  borderRadius: 10,
  background: bg,
  color,
  fontSize: 13,
  border: `1px solid ${border}`,
});
