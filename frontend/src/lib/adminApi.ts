export async function getStories(): Promise<string> {
  const res = await fetch("/admin/stories", { credentials: "same-origin" });
  if (!res.ok) throw new Error(`Load failed (${res.status})`);
  const data = await res.json();
  return data.content as string;
}

export async function saveStories(content: string): Promise<void> {
  const res = await fetch("/admin/stories", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Save failed (${res.status}): ${detail}`);
  }
}

export type AnecdoteSummary = {
  source_file: string;
  chunks: number;
  created_at: string;
};

export async function listAnecdotes(): Promise<AnecdoteSummary[]> {
  const res = await fetch("/admin/anecdotes", { credentials: "same-origin" });
  if (!res.ok) throw new Error(`List failed (${res.status})`);
  return res.json();
}

export async function upsertAnecdote(title: string, content: string): Promise<{
  source_file: string;
  chunks_inserted: number;
}> {
  const res = await fetch("/admin/anecdotes", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ title, content }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Save failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export async function deleteAnecdote(sourceFile: string): Promise<void> {
  const res = await fetch(`/admin/anecdotes/${encodeURIComponent(sourceFile)}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error(`Delete failed (${res.status})`);
}

export async function reindex(): Promise<{ status: string; elapsed_ms: number }> {
  const res = await fetch("/admin/reindex", {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error(`Reindex failed (${res.status})`);
  return res.json();
}
