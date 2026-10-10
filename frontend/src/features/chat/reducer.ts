/** 对话页状态机。
 *
 * 这个 reducer 存在的理由是后端协议的几个**反直觉**之处，散在组件里迟早写错：
 *  1. `done` 事件**经常不带 status**（成功路径只发 `{run_id}`，见 agent_chat.py:1188）。
 *     所以「没有 status」既可能是成功也可能是失败 —— 用 `sawErrorFrame` 区分。
 *  2. `narrative` 的 `{start:true}` 只是分段哨兵，而 `artifacts` 在分析管线上
 *     **先于** `start` 到达（agent_chat.py:1147-1177），所以气泡不能靠 start 创建。
 *  3. `incomplete`（预算/超时耗尽）**不是成功**，但它是个「完整可回放」的回答 ——
 *     不能渲染成绿色成功，也不能当成普通失败。
 *  4. 流可能**没有终态帧**就结束（实测：provider 抛 httpx 异常时服务端只捕获
 *     RuntimeError/ValueError，流直接断）。必须把这种截断显式标成失败。
 */
import type { ChatArtifacts } from "../../contracts/api";

export type DoneStatus = "completed" | "failed" | "incomplete" | "cancelled" | "paused" | "waiting_clarification" | null;

export type Phase = "idle" | "sending" | "streaming" | "pausing" | "paused" | "clarifying" | "done" | "error" | "busy";

export interface ToolStep { step: string; label: string; reason?: string; tool?: string; }
export interface Attachment { name: string; size: number; }

export interface ChatMessage {
  localId: string;
  role: "user" | "assistant";
  content: string;
  /** 用户轮才有；重发时必须**原样复用**，否则后端会重算而不是回放。 */
  clientMessageId?: string;
  attachments?: Attachment[];
  steps: ToolStep[];
  artifacts: ChatArtifacts | null;
  intent?: string;
  doneStatus?: DoneStatus;
  /** 原生终态：`done.status` 与 `artifacts.status` 取更严重的那个。 */
  severity?: "ok" | "warn" | "bad" | "neutral";
}

export interface ChatState {
  sessionId: string | null;
  phase: Phase;
  runId: string | null;
  messages: ChatMessage[];
  /** SUBMIT 时预生成，首个 step/artifacts/narrative-start 到达时才落地成一条消息 ——
   * 免得「对话正忙」这类立刻失败的轮次留下一个空气泡。 */
  pendingAssistantId: string | null;
  openAssistantId: string | null;
  sawErrorFrame: boolean;
  errorMessage: string | null;
  busy: { message: string; activeRun?: { run_id: string; status: string; created_at: string } } | null;
  resumable: { run_id: string; status: string; stage: string | null; steps_done: number | null; updated_at: string | null } | null;
}

export const initialChatState: ChatState = {
  sessionId: null, phase: "idle", runId: null, messages: [], pendingAssistantId: null,
  openAssistantId: null, sawErrorFrame: false, errorMessage: null, busy: null, resumable: null,
};

export type ChatAction =
  | { type: "NEW_CONVERSATION" }
  | { type: "HYDRATE"; sessionId: string; messages: ChatMessage[] }
  | { type: "SUBMIT"; clientMessageId: string; assistantId: string; text: string; attachments: Attachment[] }
  | { type: "SESSION"; sessionId: string; runId?: string; intent?: string }
  | { type: "STEP"; step: string; label: string; reason?: string; tool?: string }
  | { type: "ARTIFACTS"; artifacts: ChatArtifacts }
  | { type: "NARRATIVE_START" }
  | { type: "NARRATIVE_FLUSH"; text: string }
  | { type: "DONE"; runId: string; status?: DoneStatus; intent?: string }
  | { type: "ERROR_FRAME"; message: string }
  | { type: "STREAM_ENDED_WITHOUT_TERMINAL" }
  | { type: "TRANSPORT_FAILED"; message: string }
  | { type: "BUSY"; message: string; activeRun?: { run_id: string; status: string; created_at: string } }
  | { type: "PAUSE_REQUESTED" }
  | { type: "CANCEL_REQUESTED" } | { type: "CANCEL_ACK" } | { type: "PAUSE_ACK" }
  | { type: "RESUMABLE"; resumable: ChatState["resumable"] }
  | { type: "CLEAR_NOTICE" };

/** 从 `done.status` 与 `artifacts.status` 推出一个统一的严重度。
 * `failed`/`incomplete` 永远不能变成绿色。 */
function severityOf(status: DoneStatus, artifacts: ChatArtifacts | null, sawErrorFrame: boolean): ChatMessage["severity"] {
  const artifactStatus = artifacts && typeof artifacts.status === "string" ? artifacts.status : null;
  const values = [status, artifactStatus].filter(Boolean) as string[];
  if (values.includes("failed")) return "bad";
  if (sawErrorFrame) return "bad";
  if (values.includes("incomplete")) return "warn";
  if (values.includes("cancelled") || values.includes("paused")) return "neutral";
  return "ok";
}

function withOpen(state: ChatState, mutate: (message: ChatMessage) => ChatMessage): ChatState {
  const id = state.openAssistantId;
  if (!id) return state;
  return { ...state, messages: state.messages.map((m) => (m.localId === id ? mutate(m) : m)) };
}

/** 首个 step/artifacts/narrative-start 到达时把占位落地成真实消息。 */
function ensureOpen(state: ChatState): ChatState {
  if (state.openAssistantId) return state;
  const id = state.pendingAssistantId;
  if (!id) return state;
  const message: ChatMessage = { localId: id, role: "assistant", content: "", steps: [], artifacts: null };
  return { ...state, openAssistantId: id, pendingAssistantId: null, messages: [...state.messages, message] };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "NEW_CONVERSATION":
      return { ...initialChatState };

    case "HYDRATE":
      return {
        ...initialChatState,
        sessionId: action.sessionId,
        messages: action.messages,
        phase: "idle",
      };

    case "SUBMIT":
      return {
        ...state,
        phase: "sending",
        runId: null,
        pendingAssistantId: action.assistantId,
        openAssistantId: null,
        sawErrorFrame: false,
        errorMessage: null,
        busy: null,
        resumable: null,
        messages: [
          ...state.messages,
          { localId: action.clientMessageId, role: "user", content: action.text, clientMessageId: action.clientMessageId, attachments: action.attachments, steps: [], artifacts: null },
        ],
      };

    case "SESSION":
      return { ...state, sessionId: action.sessionId, runId: action.runId ?? state.runId, phase: state.phase === "sending" ? "streaming" : state.phase };

    case "STEP": {
      const next = ensureOpen(state);
      const phase = next.phase === "sending" ? "streaming" : next.phase;
      const withPhase = { ...next, phase };
      return withOpen(withPhase, (m) => {
        // 连续相同的 label 不重复追加（后端会为每个工具再发一条 step）。
        const last = m.steps[m.steps.length - 1];
        if (last && last.label === action.label && last.tool === action.tool) return m;
        return { ...m, steps: [...m.steps, { step: action.step, label: action.label, reason: action.reason, tool: action.tool }] };
      });
    }

    case "ARTIFACTS": {
      const next = ensureOpen(state);
      const phase = next.phase === "sending" ? "streaming" : next.phase;
      const intent = typeof action.artifacts.intent === "string" ? action.artifacts.intent : undefined;
      return withOpen({ ...next, phase }, (m) => ({ ...m, artifacts: action.artifacts, intent: intent ?? m.intent }));
    }

    case "NARRATIVE_START": {
      const next = ensureOpen(state);
      // 重复的 start（resume / 回放会重发整段）不得新建气泡，也不清空已有正文。
      const phase = next.phase === "sending" ? "streaming" : next.phase;
      return { ...next, phase };
    }

    case "NARRATIVE_FLUSH":
      return withOpen(state, (m) => ({ ...m, content: m.content + action.text }));

    case "DONE": {
      const status = action.status ?? null;
      let phase: Phase = "done";
      let errorMessage = state.errorMessage;
      if (status === "paused") phase = "paused";
      else if (status === "waiting_clarification") phase = "clarifying";
      else if (status === "failed") phase = "error";
      else if (status === null && state.sawErrorFrame) phase = "error";
      else if (status === "cancelled") phase = "done";
      // 没有 status 且没见过 error 帧 = 成功路径的收尾。
      if (status === "failed" && !errorMessage) errorMessage = "本轮执行失败。";
      const severity = severityOf(status, null, state.sawErrorFrame);
      const finalized = withOpen(state, (m) => ({
        ...m,
        doneStatus: status,
        intent: action.intent ?? m.intent,
        severity: severityOf(status, m.artifacts, state.sawErrorFrame),
      }));
      return {
        ...finalized,
        phase,
        runId: action.runId || finalized.runId,
        openAssistantId: null,
        pendingAssistantId: null,
        errorMessage,
        messages: finalized.messages.map((m) => (m.severity ? m : { ...m, severity })),
      };
    }

    case "ERROR_FRAME":
      return { ...state, sawErrorFrame: true, errorMessage: action.message };

    case "STREAM_ENDED_WITHOUT_TERMINAL": {
      // 服务端在异常路径上可能**不发终态帧**就关闭连接（实测：provider 401 时如此）。
      // 这不是「完成」，必须显式失败，否则用户看到答案半截却毫无提示。
      const next = ensureOpen(state);
      return {
        ...withOpen(next, (m) => ({ ...m, severity: "bad" })),
        phase: "error",
        openAssistantId: null,
        pendingAssistantId: null,
        errorMessage: next.errorMessage ?? "连接在返回结果前中断。",
      };
    }

    case "TRANSPORT_FAILED": {
      const next = ensureOpen(state);
      return {
        ...withOpen(next, (m) => ({ ...m, severity: "bad" })),
        phase: "error",
        openAssistantId: null,
        pendingAssistantId: null,
        errorMessage: action.message,
      };
    }

    case "BUSY":
      return { ...state, phase: "busy", pendingAssistantId: null, openAssistantId: null, busy: { message: action.message, activeRun: action.activeRun } };

    case "PAUSE_REQUESTED": return { ...state, phase: "pausing" };
    case "CANCEL_REQUESTED": return { ...state, phase: "pausing" };
    case "CANCEL_ACK": return { ...state, phase: "done", openAssistantId: null, pendingAssistantId: null };
    case "PAUSE_ACK": return { ...state, phase: "paused", openAssistantId: null, pendingAssistantId: null };

    case "RESUMABLE":
      return { ...state, resumable: action.resumable };

    case "CLEAR_NOTICE":
      return { ...state, errorMessage: null, busy: null, phase: state.phase === "error" || state.phase === "busy" ? "idle" : state.phase };

    default:
      return state;
  }
}
