export type ApiJsonOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | Record<string, unknown> | unknown[];
};

function normalizeBody(options: ApiJsonOptions): RequestInit {
  const opts: RequestInit = { ...options };
  const body = options.body;
  if (body !== undefined && typeof body !== "string" && !(body instanceof FormData) && !(body instanceof URLSearchParams) && !(body instanceof Blob)) {
    opts.body = JSON.stringify(body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  } else if (body !== undefined) {
    opts.body = body as BodyInit;
  }
  return opts;
}

export async function apiJson<T>(url: string, options: ApiJsonOptions = {}): Promise<T> {
  const response = await fetch(url, normalizeBody(options));
  if (!response.ok) {
    let msg = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      msg = payload.message || payload.detail || msg;
    } catch {
      // Fall through with HTTP status text.
    }
    throw new Error(msg);
  }
  if (response.status === 204) {
    return null as T;
  }
  return (await response.json()) as T;
}
