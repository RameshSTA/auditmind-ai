/**
 * Server-side API client — the BFF's single door to the FastAPI backend.
 *
 * This runs ONLY on the server (imported by Route Handlers under src/app/api/*). It is the one and
 * only place that:
 *   1. knows the API's base URL (AUDITMIND_API_BASE_URL),
 *   2. mints/attaches the bearer token, and
 *   3. translates the API's RFC 7807 problem+json into a shape the UI can render calmly.
 *
 * No browser code imports this module — "auth is invisible to the frontend" made structural: a
 * React component literally cannot reach the token or the API host.
 */
import { mintDevToken, type DevPersona } from "@/server/dev-auth";

const API_BASE_URL = process.env.AUDITMIND_API_BASE_URL ?? "http://localhost:8000";

/** The RFC 7807 body the API returns on every AuditMindError (see shared/errors.py). */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  trace_id?: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: ProblemDetail | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ApiRequestOptions {
  method?: string;
  /** JSON body; omit for GET. */
  body?: unknown;
  /** For multipart uploads (document upload). Takes precedence over `body`. */
  formData?: FormData;
  searchParams?: Record<string, string | number | undefined>;
}

/**
 * Issues an authenticated request to the API as the given persona and returns parsed JSON.
 * Throws `ApiError` (carrying the problem+json + status) on any non-2xx, so callers can map a
 * 403/404/422 to the right UI treatment rather than swallowing it.
 */
export async function apiFetch<T>(
  persona: DevPersona,
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const token = await mintDevToken(persona);
  const url = new URL(path, API_BASE_URL);
  if (options.searchParams) {
    for (const [key, value] of Object.entries(options.searchParams)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }

  const headers: Record<string, string> = { authorization: `Bearer ${token}` };
  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData; // fetch sets the multipart boundary Content-Type itself
  } else if (options.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body,
    cache: "no-store",
  });

  if (!response.ok) {
    let problem: ProblemDetail | null = null;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      problem = null;
    }
    throw new ApiError(
      response.status,
      problem,
      problem?.detail ?? `API request failed with status ${response.status}`,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Issues an authenticated GET and returns the raw, unparsed `Response` — the binary-body twin of
 * `apiFetch`, for the one route (report PDF export) whose body isn't JSON. The Route Handler that
 * calls this streams `.body`/headers straight through rather than buffering into a JS string.
 */
export async function apiFetchBinary(persona: DevPersona, path: string): Promise<Response> {
  const token = await mintDevToken(persona);
  const url = new URL(path, API_BASE_URL);
  const response = await fetch(url, {
    headers: { authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!response.ok) {
    let problem: ProblemDetail | null = null;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      problem = null;
    }
    throw new ApiError(
      response.status,
      problem,
      problem?.detail ?? `API request failed with status ${response.status}`,
    );
  }
  return response;
}

/**
 * Unauthenticated request to the API — the only two callers are ``/v1/auth/register`` and
 * ``/v1/auth/login`` (`api/auth/*` Route Handlers), which are by definition the calls made
 * *before* an identity/token exists. Every other route on the API requires a bearer token; this
 * helper deliberately cannot attach one, so it can't accidentally be reused for an authenticated
 * call that should go through `apiFetch`.
 */
export async function publicApiFetch<T>(path: string, body: unknown): Promise<T> {
  const url = new URL(path, API_BASE_URL);
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    let problem: ProblemDetail | null = null;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      problem = null;
    }
    throw new ApiError(
      response.status,
      problem,
      problem?.detail ?? `API request failed with status ${response.status}`,
    );
  }
  return (await response.json()) as T;
}
