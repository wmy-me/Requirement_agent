/** 后端对话流（POST + SSE）的客户端。
 *
 * 为什么不用 `EventSource`：后端是 **POST** 且要求 `Authorization` 头，而
 * `EventSource` 既不能发 POST 也不能自定义头。所以这里手写
 * `fetch` + `ReadableStream`。也**不能**复用 `lib/http.ts` 的 `api()` ——
 * 它强制 `Accept: application/json` 且总会 `response.json()`，对 SSE 会直接卡死。
 *
 * 服务端帧格式见 `api/routes/agent_chat.py` 的 `_sse`：
 *     id: {seq}\n            （**只有** step/done/error 有）
 *     event: {name}\n
 *     data: {json}\n\n       （ensure_ascii=False，所以 data 恒为单行）
 * 全库只有 6 个事件名：session / step / narrative / artifacts / done / error。
 */
import { token } from "./http";

export type SseEventName = "session" | "step" | "narrative" | "artifacts" | "done" | "error";

export interface SseFrame {
  event: SseEventName | string;
  data: unknown;
  /** `id:` 字段，仅 step/done/error 会带。见下方「不做重放」的说明。 */
  id: string | null;
}

/** 读到响应之前就失败（401/403/409/422）。409 的 `detail` 是**对象**，
 * 携带 `active_run`（对话正忙），与 pause/cancel 的字符串 detail 不同。 */
export class StreamUnavailableError extends Error {
  status: number;
  detail: unknown;
  requestId?: string;
  activeRun?: { run_id: string; status: string; created_at: string };
  constructor(status: number, detail: unknown, requestId?: string) {
    super(typeof detail === "string" ? detail : `请求失败（${status}）`);
    this.status = status;
    this.detail = detail;
    this.requestId = requestId;
    if (detail && typeof detail === "object" && "active_run" in detail) {
      const run = (detail as { active_run?: unknown }).active_run;
      if (run && typeof run === "object") this.activeRun = run as StreamUnavailableError["activeRun"];
    }
  }
}

/** 连接中断 / 提前关闭。**这不是服务端错误** —— 服务端错误会以 `error` 帧到达。 */
export class StreamTransportError extends Error {}
/** 客户端主动断开读取。 */
export class StreamAbortedError extends Error {
  reason?: string;
  constructor(reason?: string) {
    super("流已断开");
    this.reason = reason;
  }
}

export interface ChatStreamHandle {
  /** 流正常结束（收到 `done` 帧）或异常结束时 settle。协议级失败以 `error`
   * **帧**到达，不会让这个 Promise reject。 */
  readonly completion: Promise<void>;
  /** **只取消读取**，不取消服务端任务。要真正停止请先调
   * `POST /api/v1/agent/runs/{run_id}/cancel`，等 `done{status:"cancelled"}` 到了
   * 再调这个 —— 否则 run 会一直挂到租约过期。 */
  abortRead(reason?: "user" | "unmount" | "navigation"): void;
}

/** 把一整块（两个空行之间的内容）解析成一个帧。未知字段与注释行按 SSE 规范忽略。 */
export function parseEventBlock(block: string): SseFrame {
  let event = "message";
  let id: string | null = null;
  const dataLines: string[] = [];
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue; // 注释 / 心跳
    const colon = rawLine.indexOf(":");
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    // 规范：冒号后若紧跟一个空格，去掉这一个空格。
    const value = colon === -1 ? "" : rawLine.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "id") id = value;
    else if (field === "data") dataLines.push(value);
    // retry / 其它字段：忽略
  }
  const raw = dataLines.join("\n");
  let data: unknown = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    // 帧格式错误不该让整个读循环炸掉 —— 变成一个 error 帧交给上层展示。
    return { event: "error", data: { message: "响应格式无法解析" }, id };
  }
  return { event, data, id };
}

export function openChatStream(options: {
  path: string;
  body: BodyInit;
  signal?: AbortSignal;
  onEvent: (frame: SseFrame) => void;
}): ChatStreamHandle {
  const controller = new AbortController();
  let abortedReason: string | undefined;
  if (options.signal) {
    if (options.signal.aborted) controller.abort();
    else options.signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  const completion = (async () => {
    const headers = new Headers({ Accept: "text/event-stream" });
    const credential = token();
    if (credential) headers.set("Authorization", `Bearer ${credential}`);
    // FormData 必须让浏览器自己带 boundary，手设 Content-Type 会破坏 multipart。
    if (typeof options.body === "string") headers.set("Content-Type", "application/json");

    let response: Response;
    try {
      response = await fetch(options.path, { method: "POST", headers, body: options.body, signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted) throw new StreamAbortedError(abortedReason);
      throw new StreamTransportError(error instanceof Error ? error.message : "网络异常");
    }
    if (!response.ok) {
      let detail: unknown = undefined;
      let requestId = response.headers.get("x-request-id") ?? undefined;
      try {
        const payload = (await response.json()) as { detail?: unknown; request_id?: unknown };
        detail = payload.detail;
        if (typeof payload.request_id === "string") requestId = payload.request_id;
      } catch { /* 非 JSON 错误体 */ }
      throw new StreamUnavailableError(response.status, detail, requestId);
    }
    if (!response.body) throw new StreamTransportError("响应没有可读的流");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        // 用正则切块以同时容忍 CRLF 和 LF。
        let match = buffer.match(/\r?\n\r?\n/);
        while (match && match.index !== undefined) {
          const block = buffer.slice(0, match.index);
          buffer = buffer.slice(match.index + match[0].length);
          if (block.trim()) options.onEvent(parseEventBlock(block));
          match = buffer.match(/\r?\n\r?\n/);
        }
      }
      // 流结束：尾块若不含空行就是**未完成事件**，按 SSE 规范丢弃，不派发。
    } catch (error) {
      if (controller.signal.aborted) throw new StreamAbortedError(abortedReason);
      throw new StreamTransportError(error instanceof Error ? error.message : "连接中断");
    } finally {
      try { reader.releaseLock(); } catch { /* 已释放 */ }
    }
  })();

  return {
    completion,
    abortRead: (reason) => {
      abortedReason = reason;
      controller.abort();
    },
  };
}
