import { describe, expect, it } from "vitest";

import { isMessagePage, isProblemDetails, isThreadPage, parseInvestigationEvent } from "./contracts";

const timestamp = "2026-09-02T12:00:00Z";

describe("API contract guards", () => {
  it("accepts current thread and message pages", () => {
    expect(
      isThreadPage({
        items: [
          {
            thread_id: "thread-1",
            case_id: "case-1",
            turn_id: "turn-1",
            status: "completed",
            created_at: timestamp,
          },
        ],
        next_cursor: null,
      }),
    ).toBe(true);
    expect(
      isMessagePage({
        items: [
          {
            message_id: "message-1",
            sequence: 1,
            turn_id: "turn-1",
            request_id: "request-1",
            role: "user",
            content: "Investigate",
            citations: [],
            turn_status: "completed",
            created_at: timestamp,
          },
        ],
        next_cursor: "opaque",
      }),
    ).toBe(true);
  });

  it("rejects extra fields and malformed identifiers", () => {
    expect(isThreadPage({ items: [], next_cursor: null, owner: "private" })).toBe(false);
    expect(
      isThreadPage({
        items: [
          {
            thread_id: "bad id",
            case_id: "case-1",
            turn_id: "turn-1",
            status: "completed",
            created_at: timestamp,
          },
        ],
        next_cursor: null,
      }),
    ).toBe(false);
  });

  it("accepts safe problem details and rejects unknown versions", () => {
    const problem = {
      schema_version: 1,
      type: "urn:investigation-agent:problem:thread_busy",
      title: "Conflict",
      status: 409,
      code: "thread_busy",
      detail: "Another request is already running for this thread.",
      retryable: true,
    };
    expect(isProblemDetails(problem)).toBe(true);
    expect(isProblemDetails({ ...problem, schema_version: 2 })).toBe(false);
  });
});

describe("SSE envelope guard", () => {
  it("accepts the documented event variants", () => {
    const base = { schema_version: 1, thread_id: "thread-1", turn_id: "turn-1", timestamp };
    expect(parseInvestigationEvent({ ...base, event: "run.started", data: { status: "running" } }).event).toBe("run.started");
    expect(
      parseInvestigationEvent({
        ...base,
        event: "progress",
        data: { phase: "planning", tool: null, attempt: null, count: null },
      }).event,
    ).toBe("progress");
    expect(parseInvestigationEvent({ ...base, event: "answer.delta", data: { index: 0, text: "Hi" } }).event).toBe("answer.delta");
    expect(
      parseInvestigationEvent({
        ...base,
        event: "run.completed",
        data: { message_id: "message-1", citations: [], status: "completed" },
      }).event,
    ).toBe("run.completed");
    expect(
      parseInvestigationEvent({
        ...base,
        event: "run.failed",
        data: { code: "internal", message: "Unable to complete.", retryable: false },
      }).event,
    ).toBe("run.failed");
  });

  it.each([
    { schema_version: 2, event: "run.started", data: { status: "running" } },
    { schema_version: 1, event: "private.update", data: {} },
    { schema_version: 1, event: "answer.delta", data: { index: -1, text: "x" } },
  ])("rejects an invalid event", (partial) => {
    expect(() =>
      parseInvestigationEvent({
        thread_id: "thread-1",
        turn_id: "turn-1",
        timestamp,
        ...partial,
      }),
    ).toThrow();
  });
});
