import { describe, expect, it } from "vitest";

import type { MessageItem, ThreadSummary } from "@/features/investigations/contracts";
import { mergeMessages, mergeThreads } from "./collections";

const thread = (id: string): ThreadSummary => ({
  thread_id: id,
  case_id: "case-1",
  turn_id: "turn-1",
  status: "completed",
  created_at: "2026-09-02T12:00:00Z",
});
const message = (id: string, sequence: number): MessageItem => ({
  message_id: id,
  sequence,
  turn_id: "turn-1",
  request_id: "request-1",
  role: "user",
  content: id,
  citations: [],
  turn_status: "completed",
  created_at: "2026-09-02T12:00:00Z",
});

describe("paginated collection merging", () => {
  it("preserves thread order and removes overlaps", () => {
    expect(mergeThreads([thread("a"), thread("b")], [thread("b"), thread("c")])).toEqual([
      thread("a"),
      thread("b"),
      thread("c"),
    ]);
  });

  it("deduplicates and sorts messages by sequence", () => {
    expect(mergeMessages([message("b", 2)], [message("a", 1), message("b", 2)])).toEqual([
      message("a", 1),
      message("b", 2),
    ]);
  });
});
