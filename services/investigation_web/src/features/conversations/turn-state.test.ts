import { describe, expect, it } from "vitest";

import type { InvestigationEvent, InvokeRequest } from "@/features/investigations/contracts";
import { initialTurnState, turnReducer } from "./turn-state";

const payload: InvokeRequest = {
  request_id: "request-1",
  thread_id: "thread-1",
  message: "Question",
};
const base = {
  schema_version: 1 as const,
  thread_id: "thread-1",
  turn_id: "turn-1",
  timestamp: "2026-09-02T12:00:00Z",
};

function event(value: Omit<InvestigationEvent, keyof typeof base>): InvestigationEvent {
  return { ...base, ...value } as InvestigationEvent;
}

describe("turnReducer", () => {
  it("moves through a successful streamed turn", () => {
    let state = turnReducer(initialTurnState, { type: "begin", payload });
    state = turnReducer(state, {
      type: "event",
      event: event({ event: "run.started", data: { status: "running" } }),
    });
    state = turnReducer(state, {
      type: "event",
      event: event({
        event: "progress",
        data: { phase: "planning", tool: null, attempt: null, count: null },
      }),
    });
    state = turnReducer(state, {
      type: "event",
      event: event({ event: "answer.delta", data: { index: 0, text: "Answer" } }),
    });
    state = turnReducer(state, {
      type: "event",
      event: event({
        event: "run.completed",
        data: { message_id: "message-1", citations: [], status: "completed" },
      }),
    });
    expect(state).toMatchObject({ phase: "completed", answer: "Answer", messageId: "message-1" });
  });

  it("retains the exact payload for retryable and uncertain outcomes", () => {
    const started = turnReducer(initialTurnState, { type: "begin", payload });
    const failed = turnReducer(started, {
      type: "transport-failed",
      failure: { message: "Temporary", retryable: true },
    });
    const uncertain = turnReducer(started, { type: "uncertain", message: "Check history" });
    expect(failed.payload).toBe(payload);
    expect(uncertain.payload).toBe(payload);
  });

  it("covers cancellation, reset, and invalid active submission", () => {
    const started = turnReducer(initialTurnState, { type: "begin", payload });
    expect(turnReducer(started, { type: "cancelled" }).phase).toBe("cancelled");
    expect(turnReducer(started, { type: "reset" })).toEqual(initialTurnState);
    expect(() => turnReducer(started, { type: "begin", payload })).toThrow(/already active/);
  });

  it("rejects a repeated delta index", () => {
    let state = turnReducer(initialTurnState, { type: "begin", payload });
    state = turnReducer(state, {
      type: "event",
      event: event({ event: "run.started", data: { status: "running" } }),
    });
    expect(() =>
      turnReducer(state, {
        type: "event",
        event: event({ event: "answer.delta", data: { index: 1, text: "bad" } }),
      }),
    ).toThrow(/Unexpected answer delta/);
  });
});
