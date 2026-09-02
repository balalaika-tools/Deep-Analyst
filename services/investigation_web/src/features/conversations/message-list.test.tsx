import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MessageItem } from "@/features/investigations/contracts";
import { MessageList } from "./message-list";
import { initialTurnState } from "./turn-state";

const interrupted: MessageItem = {
  message_id: "message-1",
  sequence: 1,
  turn_id: "turn-1",
  request_id: "request-1",
  role: "user",
  content: "Investigate account 77",
  citations: [],
  turn_status: "interrupted",
  created_at: "2026-09-02T12:00:00Z",
};

describe("MessageList", () => {
  it("shows an interrupted user turn without fabricating an assistant answer", () => {
    render(
      <MessageList
        loadingMore={false}
        messages={[interrupted]}
        nextCursor={null}
        onLoadMore={vi.fn()}
        turn={initialTurnState}
      />,
    );
    expect(screen.getByText("Investigate account 77")).toBeVisible();
    expect(screen.getByText("interrupted")).toBeVisible();
    expect(screen.queryByLabelText("assistant message")).not.toBeInTheDocument();
  });

  it("renders accessible citation metadata", () => {
    const assistant: MessageItem = {
      ...interrupted,
      message_id: "message-2",
      sequence: 2,
      role: "assistant",
      content: "Verified answer",
      turn_status: "completed",
      citations: [
        {
          evidence_id: "evidence-1",
          content_hash: "a".repeat(64),
          source_ref: { record_id: "record-7", locator: { kind: "field", field: "amount" } },
        },
      ],
    };
    render(
      <MessageList
        loadingMore={false}
        messages={[assistant]}
        nextCursor={null}
        onLoadMore={vi.fn()}
        turn={initialTurnState}
      />,
    );
    expect(screen.getByText("1 source")).toBeVisible();
    expect(screen.getByText("Record record-7")).toBeInTheDocument();
  });

  it("shows immediate activity and replaces its label with streamed progress", () => {
    const activeTurn = {
      ...initialTurnState,
      phase: "submitting" as const,
      payload: {
        request_id: "request-new",
        thread_id: "thread-1",
        case_id: "case-1",
        message: "Investigate transfers",
      },
    };
    const view = render(
      <MessageList
        loadingMore={false}
        messages={[]}
        nextCursor={null}
        onLoadMore={vi.fn()}
        turn={activeTurn}
      />,
    );

    expect(screen.getByRole("status", { name: "Investigation progress" })).toBeVisible();
    expect(screen.getByText("Starting investigation")).toBeVisible();
    expect(document.querySelector(".activity-spinner")).toBeInTheDocument();

    view.rerender(
      <MessageList
        loadingMore={false}
        messages={[]}
        nextCursor={null}
        onLoadMore={vi.fn()}
        turn={{ ...activeTurn, phase: "streaming_progress", progress: "querying_records" }}
      />,
    );
    expect(screen.getByText("Querying records")).toBeVisible();
    expect(screen.queryByText("Starting investigation")).not.toBeInTheDocument();
  });
});
