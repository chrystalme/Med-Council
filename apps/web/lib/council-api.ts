const base = () =>
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://localhost:8000";

export type CouncilErrorDetail = {
  code?: string;
  message?: string;
  [k: string]: unknown;
};

export type CouncilErrorBody = { detail?: string | CouncilErrorDetail };

/**
 * Error thrown by councilJson when the server returns a non-2xx status.
 * When the body is a structured {detail: {code, message, ...}}, those fields
 * are lifted so callers can dispatch per-code modals (e.g. "consultation_cap",
 * "attachment_size", "voice_premium").
 */
export class CouncilApiError extends Error {
  status: number;
  code?: string;
  detail?: CouncilErrorDetail | string;

  constructor(
    message: string,
    opts: {
      status: number;
      code?: string;
      detail?: CouncilErrorDetail | string;
    }
  ) {
    super(message);
    this.name = "CouncilApiError";
    this.status = opts.status;
    this.code = opts.code;
    this.detail = opts.detail;
  }
}

export async function councilFetch(
  path: string,
  init: RequestInit & { token?: string | null }
): Promise<Response> {
  const headers = new Headers(init.headers);
  // Only default Content-Type to JSON for string bodies. FormData / Blob must
  // let the browser set multipart boundaries automatically.
  if (
    !headers.has("Content-Type") &&
    typeof init.body === "string" &&
    init.body.length > 0
  ) {
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
    if (!res.ok) {
      throw new CouncilApiError(text.slice(0, 200) || `HTTP ${res.status}`, {
        status: res.status,
        detail: text.slice(0, 500),
      });
    }
    return data as T;
  }
  if (!res.ok) {
    const d = data as CouncilErrorBody;
    const detail = d?.detail;
    let code: string | undefined;
    let msg: string;
    if (typeof detail === "string") {
      msg = detail;
    } else if (detail && typeof detail === "object") {
      code = typeof detail.code === "string" ? detail.code : undefined;
      msg =
        typeof detail.message === "string"
          ? detail.message
          : `HTTP ${res.status}`;
    } else {
      msg = `HTTP ${res.status}`;
    }
    throw new CouncilApiError(msg, {
      status: res.status,
      code,
      detail: detail as CouncilErrorDetail | string | undefined,
    });
  }
  return data as T;
}
