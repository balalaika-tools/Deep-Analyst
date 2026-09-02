import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ThreadSummary } from "@/features/investigations/contracts";
import { ThreadSidebar } from "./thread-sidebar";

const thread: ThreadSummary = {
  thread_id: "thread-1",
  case_id: "case-2",
  turn_id: "turn-1",
  status: "running",
  created_at: "2026-09-02T12:00:00Z",
};

describe("ThreadSidebar", () => {
  it("links with the summary case and thread identity", () => {
    render(
      <ThreadSidebar
        activeCaseId="case-1"
        activeThreadId={null}
        activeTurn={false}
        loadingMore={false}
        nextCursor={null}
        onDelete={vi.fn()}
        onLoadMore={vi.fn()}
        threads={[thread]}
      />,
    );
    expect(screen.getByRole("link", { name: /case-2/i })).toHaveAttribute(
      "href",
      "/cases/case-2/threads/thread-1",
    );
    expect(screen.getByRole("link", { name: "New investigation" })).toHaveAttribute(
      "href",
      "/cases/case-1",
    );
  });

  it("disables deletion for the actively streaming thread", () => {
    render(
      <ThreadSidebar
        activeCaseId="case-2"
        activeThreadId="thread-1"
        activeTurn
        loadingMore={false}
        nextCursor={null}
        onDelete={vi.fn()}
        onLoadMore={vi.fn()}
        threads={[thread]}
      />,
    );
    expect(screen.getByRole("button", { name: "Delete conversation for case-2" })).toBeDisabled();
  });

  it("loads another cursor page only on request", async () => {
    const user = userEvent.setup();
    const onLoadMore = vi.fn();
    render(
      <ThreadSidebar
        activeCaseId="case-1"
        activeThreadId={null}
        activeTurn={false}
        loadingMore={false}
        nextCursor="opaque"
        onDelete={vi.fn()}
        onLoadMore={onLoadMore}
        threads={[thread]}
      />,
    );
    expect(onLoadMore).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });
});
