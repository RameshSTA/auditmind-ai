/**
 * Browser-side fetch helper against this app's own /api/bff/* handlers (never the FastAPI API
 * directly — the browser doesn't know its address). Throws `BffError` carrying the backend's
 * problem+json so components can render the API's real message + trace_id.
 */
import type { ProblemDetail } from "@/server/api-client";

export class BffError extends Error {
  constructor(
    readonly status: number,
    readonly problem: ProblemDetail | null,
  ) {
    super(problem?.detail ?? `Request failed (${status})`);
    this.name = "BffError";
  }
}

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let problem: ProblemDetail | null = null;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      problem = null;
    }
    throw new BffError(response.status, problem);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function bffGet<T>(path: string): Promise<T> {
  return parse<T>(await fetch(path, { cache: "no-store" }));
}

export async function bffPost<T>(path: string, body?: unknown): Promise<T> {
  return parse<T>(
    await fetch(path, {
      method: "POST",
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export async function bffUpload<T>(path: string, formData: FormData): Promise<T> {
  return parse<T>(await fetch(path, { method: "POST", body: formData }));
}

export async function bffDelete<T>(path: string): Promise<T> {
  return parse<T>(await fetch(path, { method: "DELETE" }));
}
