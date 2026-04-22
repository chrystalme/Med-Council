const base = () =>
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export type CouncilErrorBody = { detail?: string | Record<string, unknown> };

export async function councilFetch(
  path: string,
  init: RequestInit & { token?: string | null }
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (init.token) {
    headers.set("Authorization", `Bearer ${init.token}`);
  }
  return fetch(`${base()}${path}`, {
    ...init,
    headers,
    credentials: "omit",
  });
}

export async function councilJson<T>(
  path: string,
  init: RequestInit & { token?: string | null }
): Promise<T> {
  const res = await councilFetch(path, init);
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(text.slice(0, 200) || `HTTP ${res.status}`);
  }
  if (!res.ok) {
    const d = data as CouncilErrorBody;
    const detail = d?.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message?: string }).message)
          : `HTTP ${res.status}`;
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return data as T;
}
