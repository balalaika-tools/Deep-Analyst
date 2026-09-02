import { describe, expect, it, vi } from "vitest";

import { consumeInvestigationStream } from "./protocol";

const timestamp = "2026-09-02T12:00:00Z";
const base = { schema_version: 1, thread_id: "thread-1", turn_id: "turn-1", timestamp };

function event(name: string, data: object): string {
  return `event: ${name}\ndata: ${JSON.stringify({ ...base, event: name, data })}\n\n`;
}

function stream(text: string): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
}

describe("consumeInvestigationStream", () => {
  it("accepts a started, ordered, terminal stream", async () => {
    const onEvent = vi.fn();
    const terminal = await consumeInvestigationStream(
      stream(
        event("run.started", { status: "running" }) +
          event("answer.delta", { index: 0, text: "A" }) +
          event("answer.delta", { index: 1, text: "Δ" }) +
          event("run.completed", { message_id: "message-1", citations: [], status: "completed" }),
      ),
      { threadId: "thread-1", onEvent },
    );
    expect(terminal.event).toBe("run.completed");
    expect(onEvent).toHaveBeenCalledTimes(4);
  });

  it.each([
    ["skipped delta", event("run.started", { status: "running" }) + event("answer.delta", { index: 1, text: "x" })],
    ["missing start", event("run.failed", { code: "internal", message: "Failed", retryable: false })],
    ["premature EOF", event("run.started", { status: "running" })],
    [
      "duplicate terminal",
      event("run.started", { status: "running" }) +
        event("run.failed", { code: "internal", message: "Failed", retryable: false }) +
        event("run.failed", { code: "internal", message: "Failed", retryable: false }),
    ],
  ])("rejects %s", async (_name, value) => {
    await expect(
      consumeInvestigationStream(stream(value), { threadId: "thread-1", onEvent: vi.fn() }),
    ).rejects.toThrow();
  });

  it("rejects a thread identity mismatch", async () => {
    await expect(
      consumeInvestigationStream(stream(event("run.started", { status: "running" })), {
        threadId: "thread-2",
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow(/another thread/);
  });
});
