import type { ApiError, ListResponse, UploadResult, UploadSession } from "../contracts/api";

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

const RESUMABLE_THRESHOLD_BYTES = 5 * 1024 * 1024;
const RESUMABLE_PART_BYTES = 1024 * 1024;

const sha256 = async (value: ArrayBuffer): Promise<string> => {
  if (!globalThis.crypto?.subtle) throw new Error("当前浏览器不支持大文件分段上传所需的本地校验能力。");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", value);
  return Array.from(new Uint8Array(digest)).map((part) => part.toString(16).padStart(2, "0")).join("");
};

/** 大文件走已有断点续传 API；小文件继续使用原有 multipart，避免无谓增加请求数。
 * sessionStorage 只保存 upload_id，重新选择同一个文件可向服务端读取已接收片段；
 * 不保存文件正文，也不把运行时 token 写入浏览器存储。 */
export async function uploadDocumentWithProgress(file: File, onProgress: (completed: number, total: number, resumed: boolean) => void): Promise<UploadResult> {
  if (file.size < RESUMABLE_THRESHOLD_BYTES) return uploadDocument(file);
  const fileChecksum = await sha256(await file.arrayBuffer());
  const total = Math.ceil(file.size / RESUMABLE_PART_BYTES);
  const storageKey = `ra-upload:${file.name}:${file.size}:${fileChecksum}`;
  let session: UploadSession | null = null;
  const knownUploadId = sessionStorage.getItem(storageKey);
  if (knownUploadId) {
    try {
      const candidate = await api<UploadSession>(`/api/v1/documents/uploads/${encodeURIComponent(knownUploadId)}`);
      if (candidate.status === "open" && candidate.checksum === fileChecksum && candidate.total_chunks === total) session = candidate;
    } catch { sessionStorage.removeItem(storageKey); }
  }
  if (!session) {
    session = await api<UploadSession>("/api/v1/documents/uploads", { method: "POST", body: JSON.stringify({ file_name: file.name, content_type: file.type || "application/octet-stream", total_size: file.size, total_chunks: total, checksum: fileChecksum }), headers: { "Content-Type": "application/json" } });
    sessionStorage.setItem(storageKey, session.upload_id);
  }
  const completed = new Set(session.received_chunks);
  onProgress(completed.size, total, completed.size > 0);
  for (let index = 0; index < total; index += 1) {
    if (completed.has(index)) continue;
    const part = file.slice(index * RESUMABLE_PART_BYTES, Math.min(file.size, (index + 1) * RESUMABLE_PART_BYTES));
    const checksum = await sha256(await part.arrayBuffer());
    await api<UploadSession>(`/api/v1/documents/uploads/${encodeURIComponent(session.upload_id)}/chunks/${index}?checksum=${checksum}`, { method: "PUT", body: part });
    completed.add(index); onProgress(completed.size, total, completed.size > 0);
  }
  const done = await api<{ document: UploadResult["document"]; status: UploadResult["status"]; result: UploadResult["result"] }>(`/api/v1/documents/uploads/${encodeURIComponent(session.upload_id)}/complete`, { method: "POST" });
  sessionStorage.removeItem(storageKey);
  return { document: done.document, status: done.status, result: done.result };
}
