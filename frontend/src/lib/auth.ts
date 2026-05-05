export async function checkAuth(): Promise<boolean> {
  const res = await fetch("/auth/check", { method: "GET", credentials: "same-origin" });
  if (!res.ok) return false;
  const data = (await res.json()) as { authenticated: boolean };
  return data.authenticated;
}

export async function login(passcode: string): Promise<void> {
  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ passcode }),
  });
  if (res.status === 401) throw new Error("Invalid passcode");
  if (!res.ok) throw new Error(`Login failed (${res.status})`);
}

export async function logout(): Promise<void> {
  await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
}
