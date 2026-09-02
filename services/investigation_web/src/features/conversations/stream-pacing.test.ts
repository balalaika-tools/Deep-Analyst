import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnswerDeltaEvent, InvestigationEvent } from "@/features/investigations/contracts";
import { displayStreamEvent } from "./stream-pacing";

const answerDelta: AnswerDeltaEvent = {
  schema_version: 1,
  thread_id: "thread-1",
  turn_id: "turn-1",
  timestamp: "2026-09-03T00:00:00Z",
  event: "answer.delta",
  data: { index: 0, text: "A response long enough for pacing" },
};

afterEach(() => vi.useRealTimers());

describe("displayStreamEvent", () => {
  it("reveals answer deltas in small contiguous batches", async () => {
    vi.useFakeTimers();
    const events: InvestigationEvent[] = [];
    let index = 0;
    const displaying = displayStreamEvent(
      answerDelta,
      () => index++,
      (event) => events.push(event),
      new AbortController().signal,
    );

    expect(events).toHaveLength(1);
    await vi.runAllTimersAsync();
    await displaying;

    expect(events).toHaveLength(3);
    expect(events.map((event) => event.event === "answer.delta" ? event.data.index : -1)).toEqual([0, 1, 2]);
    expect(events.map((event) => event.event === "answer.delta" ? event.data.text : "").join(""))
      .toBe(answerDelta.data.text);
  });
});
