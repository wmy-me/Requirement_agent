import { describe, expect, it } from "vitest";
import { openChatStream, parseEventBlock } from "./stream";

describe("SSE parser", () => {
  it("joins multi-line data and accepts CRLF", () => {
    expect(parseEventBlock("id: 9\r\nevent: narrative\r\ndata: {\"t\":\r\ndata: \"你好\"}\r\n")).toEqual({
      id: "9", event: "narrative", data: { t: "你好" },
    });
  });

  it("turns malformed JSON into a visible error frame", () => {
    expect(parseEventBlock("event: done\ndata: {broken\n")).toEqual({
      id: null, event: "error", data: { message: "响应格式无法解析" },
    });
  });

  it("buffers split frames, dispatches complete CRLF events, and drops a truncated tail", async () => {
    const encoder = new TextEncoder();
    const chunks = [
      "event: artifacts\r\ndata: {\"artifacts\":{\"x\":1}}\r",
      "\n\r\nevent: done\r\ndata: {\"run_id\":\"r\",\"status\":\"completed\"}\r\n\r\n",
      "event: narrative\ndata: {\"t\":\"must be dropped\"}",
    ];
    const body = new ReadableStream<Uint8Array>({
      start(controller) { chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk))); controller.close(); },
    });
    const fetchStub = async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    const originalFetch = globalThis.fetch;
    const originalDocument = globalThis.document;
    Object.defineProperty(globalThis, "fetch", { value: fetchStub, configurable: true });
    Object.defineProperty(globalThis, "document", { value: { querySelector: () => null }, configurable: true });
    const events: string[] = [];
    try {
      const stream = openChatStream({ path: "/stream", body: "{}", onEvent: (frame) => events.push(frame.event) });
      await stream.completion;
    } finally {
      Object.defineProperty(globalThis, "fetch", { value: originalFetch, configurable: true });
      Object.defineProperty(globalThis, "document", { value: originalDocument, configurable: true });
    }
    expect(events).toEqual(["artifacts", "done"]);
  });
});
