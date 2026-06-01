const BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "").replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

export function wsUrl(path: string): string {
  if (BASE) {
    const u = new URL(BASE);
    const protocol = u.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${u.host}${path}`;
  }
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${location.host}${path}`;
}
