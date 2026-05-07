import { useEffect, useRef, useState } from "react";
import {
  deleteAnecdote,
  getStories,
  listAnecdotes,
  reindex,
  saveStories,
  upsertAnecdote,
  type AnecdoteSummary,
} from "../lib/adminApi";
import { logout } from "../lib/auth";

const CORPUS_TITLE = "stories";

export default function AdminPage() {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [busyIngest, setBusyIngest] = useState(false);
  const [busyClear, setBusyClear] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [corpus, setCorpus] = useState<AnecdoteSummary | null | undefined>(undefined);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const flash = (msg: string) => { setMessage(msg); setError(null); };
  const flashErr = (err: unknown) => {
    setError(err instanceof Error ? err.message : String(err));
    setMessage(null);
  };

  useEffect(() => {
    Promise.all([
      getStories(),
      listAnecdotes(),
    ])
      .then(([text, anecdotes]) => {
        setContent(text);
        setCorpus(anecdotes.find((a) => a.source_file === `${CORPUS_TITLE}.md`) ?? null);
      })
      .catch(flashErr)
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await saveStories(content);
      flash("Stories saved.");
    } catch (err) {
      flashErr(err);
    } finally {
      setBusy(false);
    }
  };

  const handleIngest = async () => {
    if (!content.trim()) return;
    setBusyIngest(true);
    setError(null);
    setMessage(null);
    try {
      await saveStories(content);
      const result = await upsertAnecdote(CORPUS_TITLE, content.trim());
      await reindex();
      setCorpus({ source_file: result.source_file, chunks: result.chunks_inserted, created_at: new Date().toISOString() });
      flash(`Ingested ${result.chunks_inserted} chunks into corpus and rebuilt index.`);
    } catch (err) {
      flashErr(err);
    } finally {
      setBusyIngest(false);
    }
  };

  const handleClear = async () => {
    if (!confirmClear) { setConfirmClear(true); return; }
    setBusyClear(true);
    setError(null);
    setMessage(null);
    try {
      await deleteAnecdote(`${CORPUS_TITLE}.md`);
      setCorpus(null);
      setConfirmClear(false);
      flash("Corpus cleared.");
    } catch (err) {
      flashErr(err);
    } finally {
      setBusyClear(false);
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
            Edit your story corpus. Save updates the system prompt; Ingest pushes it into the RAG database.
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
              disabled={busy || busyIngest || loading}
              className="btn btn-ghost"
              style={{ fontSize: 12.5, padding: "6px 12px" }}
            >
              Import file
            </button>
            <button
              onClick={handleSave}
              disabled={busy || busyIngest || loading}
              className="btn btn-ghost"
              style={{ fontSize: 12.5, padding: "6px 14px" }}
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              onClick={handleIngest}
              disabled={busy || busyIngest || loading || !content.trim()}
              className="btn btn-primary"
              style={{ fontSize: 12.5, padding: "6px 14px" }}
            >
              {busyIngest ? "Ingesting…" : "Save & Ingest"}
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

      {/* Corpus status */}
      {corpus !== undefined && (
        <div className="surface fade-in" style={{ padding: "10px 14px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
            {corpus === null
              ? "No corpus in database."
              : `Corpus: ${corpus.chunks} chunk${corpus.chunks !== 1 ? "s" : ""} in database.`}
          </span>
          {corpus !== null && (
            confirmClear ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Remove all chunks?</span>
                <button
                  onClick={handleClear}
                  disabled={busyClear}
                  className="btn btn-danger"
                  style={{ fontSize: 12, padding: "4px 10px" }}
                >
                  {busyClear ? "Clearing…" : "Confirm"}
                </button>
                <button
                  onClick={() => setConfirmClear(false)}
                  className="btn btn-ghost"
                  style={{ fontSize: 12, padding: "4px 10px" }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmClear(true)}
                className="btn btn-ghost"
                style={{ fontSize: 12, padding: "4px 10px", color: "var(--danger)" }}
              >
                Clear corpus
              </button>
            )
          )}
        </div>
      )}

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
