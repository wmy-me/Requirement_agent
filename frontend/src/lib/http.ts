import type { ApiError, ListResponse, UploadResult } from "../contracts/api";

/** Runtime credential injected by the server into the page (never bundled).
 * Exported because the SSE client must set the same header on a raw fetch. */
export const token = (): string | undefined => document.querySelector<HTMLMetaElement>('meta[name="ra-ui-token"]')?.content || undefined;

/** Raised for a 404 on a resource whose absence is a valid state (e.g. a
 * document with no version stream). Callers handle it instead of crashing. */
export class NotFoundError extends Error {}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const credential = token();
  if (credential) headers.set("Authorization", `Bearer ${credential}`);
  headers.set("Accept", "application/json");
  const response = await fetch(path, { ...init, headers, signal: init.signal });
  if (!response.ok) {
    const error = new Error(`请求失败（${response.status}）`) as ApiError;
    error.status = response.status;
    error.requestId = response.headers.get("x-request-id") ?? undefined;
    // Surface the server's own message (and request_id) instead of a generic one.
    try { const body = await response.clone().json() as { detail?: unknown; request_id?: unknown }; if (typeof body.detail === "string") error.detail = body.detail; if (typeof body.request_id === "string") error.requestId = body.request_id; } catch { /* non-JSON body */ }
    if (response.status === 404) throw Object.assign(new NotFoundError(error.message), { status: 404, requestId: error.requestId, detail: error.detail });
    throw error;
  }
  return response.json() as Promise<T>;
}

export const listItems = <T>(response: ListResponse<T> | undefined): T[] => response?.items ?? [];

export async function uploadDocument(file: File): Promise<UploadResult> {
  const body = new FormData(); body.append("file", file, file.name);
  return api<UploadResult>("/api/v1/documents/upload", { method: "POST", body });
}
