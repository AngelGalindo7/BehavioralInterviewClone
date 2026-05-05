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
    <div style={{ maxWidth: 720, margin: "0 auto", padding: 24, display: "flex", flexDirection: "column", gap: 24 }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Stories</h1>
        <div style={{ display: "flex", gap: 12 }}>
          <a href="/" style={{ fontSize: 13, color: "#374151" }}>← Interview</a>
          <button onClick={handleLogout} style={linkButton}>Sign out</button>
        </div>
      </header>

      <form onSubmit={handleSubmit} style={cardStyle}>
        <label style={labelStyle}>
          Title
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={busy}
            placeholder="e.g. Resolved a production outage"
            style={inputStyle}
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
            style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", fontSize: 13 }}
            required
          />
        </label>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="submit" disabled={busy || !title || !content} style={primaryButton}>
            {busy ? "Saving…" : "Save story"}
          </button>
          <span style={{ fontSize: 12, color: "#6b7280" }}>
            Same title overwrites the existing story.
          </span>
        </div>
      </form>

      {message && <div style={msgStyle("#065f46", "#d1fae5")}>{message}</div>}
      {error && <div style={msgStyle("#991b1b", "#fee2e2")}>{error}</div>}

      <section>
        <h2 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 8px" }}>
          Ingested stories ({items.length})
        </h2>
        {items.length === 0 ? (
          <p style={{ fontSize: 13, color: "#6b7280" }}>No stories yet.</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {items.map((item) => (
              <li key={item.source_file} style={rowStyle}>
                <div>
                  <div style={{ fontSize: 14 }}>{item.source_file}</div>
                  <div style={{ fontSize: 11, color: "#6b7280" }}>
                    {item.chunks} chunk(s) · {new Date(item.created_at).toLocaleString()}
                  </div>
                </div>
                <button onClick={() => handleDelete(item.source_file)} disabled={busy} style={dangerButton}>
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section style={cardStyle}>
        <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Rebuild index</h2>
        <p style={{ fontSize: 12, color: "#6b7280", margin: 0 }}>
          Run this after a batch of edits — not after every save. Briefly pauses queries; do not run during a live interview.
        </p>
        <button onClick={handleReindex} disabled={reindexing} style={secondaryButton}>
          {reindexing ? "Rebuilding…" : "Rebuild IVFFlat index"}
        </button>
      </section>
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  border: "1px solid #e5e7eb",
  borderRadius: 8,
};

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  fontSize: 13,
  color: "#374151",
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  border: "1px solid #d1d5db",
  borderRadius: 6,
  fontSize: 14,
  fontFamily: "inherit",
};

const primaryButton: React.CSSProperties = {
  padding: "8px 14px",
  border: "none",
  borderRadius: 6,
  background: "#111827",
  color: "white",
  fontSize: 13,
  cursor: "pointer",
};

const secondaryButton: React.CSSProperties = {
  padding: "8px 14px",
  border: "1px solid #d1d5db",
  borderRadius: 6,
  background: "white",
  color: "#111827",
  fontSize: 13,
  cursor: "pointer",
};

const dangerButton: React.CSSProperties = {
  padding: "6px 10px",
  border: "1px solid #fca5a5",
  borderRadius: 6,
  background: "white",
  color: "#b91c1c",
  fontSize: 12,
  cursor: "pointer",
};

const linkButton: React.CSSProperties = {
  border: "none",
  background: "transparent",
  color: "#374151",
  fontSize: 13,
  cursor: "pointer",
  padding: 0,
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "10px 12px",
  border: "1px solid #e5e7eb",
  borderRadius: 6,
};

const msgStyle = (color: string, bg: string): React.CSSProperties => ({
  padding: "8px 12px",
  borderRadius: 6,
  background: bg,
  color,
  fontSize: 13,
});
