import type { ApiError, UnknownRecord } from "../contracts/api";

const token = (): string | undefined => document.querySelector<HTMLMetaElement>('meta[name="ra-ui-token"]')?.content || undefined;
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const credential = token();
  if (credential) headers.set("Authorization", `Bearer ${credential}`);
  headers.set("Accept", "application/json");
  const response = await fetch(path, { ...init, headers, signal: init.signal });
  if (!response.ok) {
    const error = new Error(`请求失败（${response.status}）`) as ApiError;
    error.status = response.status; error.requestId = response.headers.get("x-request-id") ?? undefined;
    throw error;
  }
  return response.json() as Promise<T>;
}
export const items = <T>(response: UnknownRecord): T[] => Array.isArray(response.items) ? response.items as T[] : [];
