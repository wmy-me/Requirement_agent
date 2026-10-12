/** 把 SSE 流接到 reducer 上。
 *
 * 两个刻意的设计：
 *  - **不在 `useEffect` 里发起请求**。main.tsx 用了 `<React.StrictMode>`，开发期
 *    effect 会跑两遍；流一律由用户动作（提交/恢复）发起，effect 只负责卸载时断读。
 *  - **narrative 不逐 token dispatch**。`reader.read()` 的回调不在 React 事件里，
 *    跨读取不批处理，逐 token dispatch 会把输入框打回石器时代。这里缓冲后用
 *    requestAnimationFrame 刷一次。
 */
import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type { AnalysisMode, ChatMode, SourceType, WebPolicy } from "../../contracts/api";
import { cancelRun, getChatHistory, getResumable, pauseRun, resumeRun, startChatTurn, type ChatTurn } from "../../lib/chatApi";
import { StreamAbortedError, StreamTransportError, StreamUnavailableError, type SseFrame } from "../../lib/stream";
import { chatReducer, initialChatState, type ChatMessage } from "./reducer";

// `0.0.0.0` 上的 HTTP 页面不是 secure context，部分浏览器没有
// crypto.randomUUID；两个同步调用若只靠时间戳会撞号，进而让用户和助手气泡共用
// reducer 的 localId。递增序号让降级路径也保持同一轮内唯一。
let fallbackIdSequence = 0;
const uid = (): string => (
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `id-${Date.now()}-${++fallbackIdSequence}`
);

export interface ChatOptions { mode: ChatMode; webPolicy: WebPolicy; analysisMode: AnalysisMode; sourceType: SourceType; }

export function useChatStream() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const handleRef = useRef<{ abortRead: (reason?: "user" | "unmount" | "navigation") => void } | null>(null);
  const bufferRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const sawDoneRef = useRef(false);
  const runIdRef = useRef<string | null>(null);

  const flushNarrative = useCallback(() => {
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      const text = bufferRef.current;
      bufferRef.current = "";
      if (text) dispatch({ type: "NARRATIVE_FLUSH", text });
    });
  }, []);

  const onFrame = useCallback((frame: SseFrame) => {
    const data = (frame.data ?? {}) as Record<string, unknown>;
    switch (frame.event) {
      case "session":
        if (typeof data.session_id === "string") {
          if (typeof data.run_id === "string" && data.run_id) runIdRef.current = data.run_id;
          dispatch({ type: "SESSION", sessionId: data.session_id, runId: typeof data.run_id === "string" ? data.run_id : undefined, intent: typeof data.intent === "string" ? data.intent : undefined });
        }
        break;
      case "step":
        dispatch({ type: "STEP", step: String(data.step ?? ""), label: String(data.label ?? "处理中…"), reason: typeof data.reason === "string" ? data.reason : undefined, tool: typeof data.tool === "string" ? data.tool : undefined });
        break;
      case "narrative":
        if (data.start === true) dispatch({ type: "NARRATIVE_START" });
        else if (typeof data.t === "string" && data.t) { bufferRef.current += data.t; flushNarrative(); }
        break;
      case "artifacts":
        if (data.artifacts && typeof data.artifacts === "object") dispatch({ type: "ARTIFACTS", artifacts: data.artifacts as Record<string, unknown> });
        break;
      case "done":
        sawDoneRef.current = true;
        if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
        const pending = bufferRef.current; bufferRef.current = "";
        if (pending) dispatch({ type: "NARRATIVE_FLUSH", text: pending });
        dispatch({ type: "DONE", runId: typeof data.run_id === "string" ? data.run_id : "", status: (typeof data.status === "string" ? data.status : undefined) as never, intent: typeof data.intent === "string" ? data.intent : undefined });
        break;
      case "error":
        dispatch({ type: "ERROR_FRAME", message: String(data.message ?? "本轮执行出错。") });
        break;
      default:
        break;
    }
  }, [flushNarrative]);

  /** 跑一条流，并统一处理「读到一半断掉」与「没有终态帧就结束」。 */
  const runStream = useCallback((open: (onEvent: (f: SseFrame) => void) => { completion: Promise<void>; abortRead: (r?: "user" | "unmount" | "navigation") => void }) => {
    sawDoneRef.current = false;
    const handle = open(onFrame);
    handleRef.current = handle;
    handle.completion
      .then(() => { if (!sawDoneRef.current) dispatch({ type: "STREAM_ENDED_WITHOUT_TERMINAL" }); })
      .catch((error) => {
        if (error instanceof StreamAbortedError) return; // 用户/卸载主动断的，不是错误
        if (error instanceof StreamUnavailableError && error.status === 409) {
          dispatch({ type: "BUSY", message: error.activeRun ? "该对话已有一个进行中的任务。" : "请求未被接受。", activeRun: error.activeRun });
          return;
        }
        const message = error instanceof StreamTransportError ? "连接中断，本轮未能完成。" : error instanceof Error ? error.message : "请求失败。";
        dispatch({ type: "TRANSPORT_FAILED", message });
      })
      .finally(() => { handleRef.current = null; });
  }, [onFrame]);

  const submit = useCallback((text: string, files: File[], options: ChatOptions) => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;
    const clientMessageId = uid();
    const assistantId = uid();
    dispatch({ type: "SUBMIT", clientMessageId, assistantId, text: trimmed, attachments: files.map((f) => ({ name: f.name, size: f.size })) });
    const turn: ChatTurn = { message: trimmed, sessionId: state.sessionId, clientMessageId, mode: options.mode, webPolicy: options.webPolicy, analysisMode: options.analysisMode, sourceType: options.sourceType, files };
    runStream((onEvent) => startChatTurn(turn, onEvent));
  }, [runStream, state.sessionId]);

  /** 真正的停止：先打 cancel 接口，再断读。顺序反了会把 run 挂到租约过期。 */
  const stop = useCallback(async () => {
    const runId = runIdRef.current;
    dispatch({ type: "CANCEL_REQUESTED" });
    if (!runId) { handleRef.current?.abortRead("user"); return; }
    try { await cancelRun(runId); }
    catch { /* 取消失败也要断读，不能让界面卡在「正在停止」 */ }
  }, []);

  const pause = useCallback(async () => {
    const runId = runIdRef.current;
    dispatch({ type: "PAUSE_REQUESTED" });
    if (!runId) return;
    try { await pauseRun(runId); } catch { /* 展示由流终态决定 */ }
  }, []);

  const resume = useCallback((runId: string) => {
    runStream((onEvent) => resumeRun(runId, onEvent));
  }, [runStream]);

  const newConversation = useCallback(() => {
    handleRef.current?.abortRead("navigation");
    dispatch({ type: "NEW_CONVERSATION" });
  }, []);

  const loadHistory = useCallback(async (sessionId: string) => {
    const data = await getChatHistory(sessionId);
    const messages: ChatMessage[] = (data.history ?? []).map((item, index) => ({
      localId: `h-${sessionId}-${index}`,
      role: item.role === "user" ? "user" : "assistant",
      content: item.content ?? "",
      clientMessageId: item.client_message_id ?? undefined,
      steps: [],
      artifacts: (item.artifacts as Record<string, unknown> | null) ?? null,
      severity: item.role === "user" ? undefined : "ok",
    }));
    dispatch({ type: "HYDRATE", sessionId, messages });
  }, []);

  const loadResumable = useCallback(async (sessionId: string) => {
    try {
      const data = await getResumable(sessionId);
      dispatch({ type: "RESUMABLE", resumable: data.run });
    } catch { dispatch({ type: "RESUMABLE", resumable: null }); }
  }, []);

  // 卸载只断读，**不取消服务端 run** —— 用户只是离开了页面。
  useEffect(() => () => {
    handleRef.current?.abortRead("unmount");
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
  }, []);

  const streaming = useMemo(() => state.phase === "sending" || state.phase === "streaming" || state.phase === "pausing", [state.phase]);

  return { state, submit, stop, pause, resume, newConversation, loadHistory, loadResumable, streaming, dispatch };
}
