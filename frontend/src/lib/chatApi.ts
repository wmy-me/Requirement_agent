/** 对话相关的 HTTP 调用。JSON 的走 `api()`；流式的走 `openChatStream`。 */
import type { AnalysisMode, ChatHistory, ChatMode, ResumableResponse, RunSummary, SourceType, WebPolicy } from "../contracts/api";
import { api } from "./http";
import { openChatStream, type ChatStreamHandle, type SseFrame } from "./stream";

export interface ChatTurn {
  message: string;
  sessionId: string | null;
  clientMessageId: string;
  mode: ChatMode;
  webPolicy: WebPolicy;
  analysisMode: AnalysisMode;
  sourceType: SourceType;
  files: File[];
}

/** 有附件就必须走 multipart 端点 —— 它**没有** mode/web_policy/source_type 参数，
 * 服务端把 source_type 硬编码成 "web"（agent_chat.py:1359）。这些值在这里被丢弃
 * 是**故意的**：UI 必须先把对应控件禁用，而不是让用户以为设置了却无效。 */
export function startChatTurn(turn: ChatTurn, onEvent: (frame: SseFrame) => void, signal?: AbortSignal): ChatStreamHandle {
  if (turn.files.length > 0) {
    const form = new FormData();
    form.append("message", turn.message);
    form.append("session_id", turn.sessionId ?? "");
    form.append("client_message_id", turn.clientMessageId);
    form.append("analysis_mode", turn.analysisMode);
    turn.files.forEach((file) => form.append("files", file, file.name));
    return openChatStream({ path: "/api/v1/agent/chat/stream-with-files", body: form, onEvent, signal });
  }
  const body = JSON.stringify({
    message: turn.message,
    session_id: turn.sessionId,
    client_message_id: turn.clientMessageId,
    analysis_mode: turn.analysisMode,
    mode: turn.mode,
    web_policy: turn.webPolicy,
    source_type: turn.sourceType,
  });
  return openChatStream({ path: "/api/v1/agent/chat/stream", body, onEvent, signal });
}

/** 停止是**写操作**，必须打真正的接口。只 abort 本地读取不会停服务端 run。 */
export const cancelRun = (runId: string) => api<{ run_id: string; status: string }>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
export const pauseRun = (runId: string) => api<{ run_id: string; status: string }>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/pause`, { method: "POST" });

/** 恢复只对 `paused` 且带 checkpoint 的 run 有效；返回的是**又一条 SSE 流**。 */
export const resumeRun = (runId: string, onEvent: (frame: SseFrame) => void, signal?: AbortSignal): ChatStreamHandle =>
  openChatStream({ path: `/api/v1/agent/runs/${encodeURIComponent(runId)}/resume`, body: JSON.stringify({}), onEvent, signal });

export const getChatHistory = (sessionId: string) => api<ChatHistory>(`/api/v1/agent/chat/${encodeURIComponent(sessionId)}`);
export const getResumable = (sessionId: string) => api<ResumableResponse>(`/api/v1/agent/chat/${encodeURIComponent(sessionId)}/resumable`);
export const getRun = (runId: string) => api<RunSummary>(`/api/v1/agent/runs/${encodeURIComponent(runId)}`);
