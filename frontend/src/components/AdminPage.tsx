import { useEffect, useRef, useState } from "react";
import { getStories, saveStories } from "../lib/adminApi";
import { logout } from "../lib/auth";

export default function AdminPage() {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getStories()
      .then(setContent)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await saveStories(content);
      setMessage("Stories saved and reloaded.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setContent((ev.target?.result as string) ?? "");
      setMessage(null);
      setError(null);
    };
    reader.readAsText(file, "utf-8");
    e.target.value = "";
  };

  const handleLogout = async () => {
    await logout();
    window.location.reload();
  };

  const wordCount = content.trim() ? content.trim().split(/\s+/).length : 0;

  return (
    <div style={pageStyle}>
      <header style={headerStyle}>
        <div>
          <h1 style={{ fontSize: 18, fontWeight: 600 }}>Stories</h1>
          <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 2 }}>
            Edit and save the full story corpus. Changes take effect on the next interview turn.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <a href="/" className="btn btn-ghost" style={{ padding: "6px 12px", fontSize: 12.5 }}>
            Interview
          </a>
          <button onClick={handleLogout} className="btn btn-ghost" style={{ padding: "6px 12px", fontSize: 12.5 }}>
            Sign out
          </button>
        </div>
      </header>

      <div className="surface" style={editorCard}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {loading ? "Loading…" : `${wordCount.toLocaleString()} words`}
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              ref={fileInputRef}
              type="file"
              accept=".md,.txt"
              style={{ display: "none" }}
              onChange={handleFileUpload}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || loading}
              className="btn btn-ghost"
              style={{ fontSize: 12.5, padding: "6px 12px" }}
            >
              Import file
            </button>
            <button
              onClick={handleSave}
              disabled={busy || loading}
              className="btn btn-primary"
              style={{ fontSize: 12.5, padding: "6px 14px" }}
            >
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>

        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          disabled={loading}
          className="input"
          style={{
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 12.5,
            lineHeight: 1.6,
            resize: "vertical",
            minHeight: 520,
          }}
          placeholder="Paste your stories here in markdown, or use Import file to upload a .md file."
          spellCheck={false}
        />
      </div>

      {message && (
        <div className="surface fade-in" style={{ padding: "10px 12px", fontSize: 13 }}>
          {message}
        </div>
      )}
      {error && (
        <div className="fade-in" style={errStyle}>
          {error}
        </div>
      )}
    </div>
  );
}

const pageStyle: React.CSSProperties = {
  maxWidth: 860,
  margin: "0 auto",
  padding: "28px 24px 48px",
  display: "flex",
  flexDirection: "column",
  gap: 20,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-end",
  justifyContent: "space-between",
  gap: 16,
  flexWrap: "wrap",
};

const editorCard: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
};

const errStyle: React.CSSProperties = {
  padding: "10px 12px",
  borderRadius: 10,
  background: "var(--danger-soft)",
  color: "#fca5a5",
  fontSize: 13,
  border: "1px solid rgba(229, 72, 77, 0.25)",
};
