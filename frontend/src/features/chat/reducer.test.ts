import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState } from "./reducer";

describe("chat reducer protocol edges", () => {
  it("creates an assistant message when artifacts arrive before narrative", () => {
    let state = chatReducer(initialChatState, { type: "SUBMIT", clientMessageId: "u", assistantId: "a", text: "整理", attachments: [] });
    state = chatReducer(state, { type: "ARTIFACTS", artifacts: { extracted: { requirement_title: "标题" } } });
    expect(state.messages.find((message) => message.localId === "a")?.artifacts).toEqual({ extracted: { requirement_title: "标题" } });
  });

  it("marks incomplete as warning and preserves explicit terminal status", () => {
    let state = chatReducer(initialChatState, { type: "SUBMIT", clientMessageId: "u", assistantId: "a", text: "问", attachments: [] });
    state = chatReducer(state, { type: "NARRATIVE_START" });
    state = chatReducer(state, { type: "DONE", runId: "r", status: "incomplete" });
    expect(state.messages.at(-1)).toMatchObject({ doneStatus: "incomplete", severity: "warn" });
  });

  it("keeps a truncated stream as an error fallback", () => {
    let state = chatReducer(initialChatState, { type: "SUBMIT", clientMessageId: "u", assistantId: "a", text: "问", attachments: [] });
    state = chatReducer(state, { type: "NARRATIVE_START" });
    state = chatReducer(state, { type: "STREAM_ENDED_WITHOUT_TERMINAL" });
    expect(state.phase).toBe("error");
  });

  it("never appends streamed narrative to the user message", () => {
    // 模拟 HTTP 非安全上下文中降级 ID 生成器错误地连续返回同一值；用户文本
    // 仍必须不受 narrative 影响。
    let state = chatReducer(initialChatState, { type: "SUBMIT", clientMessageId: "same", assistantId: "same", text: "原始问题", attachments: [] });
    state = chatReducer(state, { type: "NARRATIVE_START" });
    state = chatReducer(state, { type: "NARRATIVE_FLUSH", text: "模型回答" });
    state = chatReducer(state, { type: "DONE", runId: "r", status: "completed" });
    expect(state.messages.find((message) => message.role === "user")?.content).toBe("原始问题");
    expect(state.messages.find((message) => message.role === "assistant")?.content).toBe("模型回答");
    expect(state.phase).toBe("done");
  });
});
