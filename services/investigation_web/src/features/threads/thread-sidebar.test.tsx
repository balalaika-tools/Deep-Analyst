import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ThreadSummary } from "@/features/investigations/contracts";
import { ThreadSidebar } from "./thread-sidebar";

const thread: ThreadSummary = {
  thread_id: "thread-1",
  turn_id: "turn-1",
  status: "running",
  created_at: "2026-09-02T12:00:00Z",
};

function renderSidebar(overrides: { active?: boolean; cursor?: string | null } = {}) {
  const onLoadMore = vi.fn();
  render(
    <ThreadSidebar
      activeThreadId={overrides.active ? "thread-1" : null}
      activeTurn={Boolean(overrides.active)}
      loadingMore={false}
      nextCursor={overrides.cursor ?? null}
      onDelete={vi.fn()}
      onLoadMore={onLoadMore}
      threads={[thread]}
    />,
  );
  return onLoadMore;
}

describe("ThreadSidebar", () => {
  it("links conversations using only thread identity", () => {
    renderSidebar();
    expect(screen.getByRole("link", { name: /^investigation 1/i })).toHaveAttribute(
      "href",
      "/threads/thread-1",
    );
    expect(screen.getByRole("link", { name: "New conversation" })).toHaveAttribute("href", "/");
  });

  it("numbers investigations in their displayed order", () => {
    render(
      <ThreadSidebar
        activeThreadId={null}
        activeTurn={false}
        loadingMore={false}
        nextCursor={null}
        onDelete={vi.fn()}
        onLoadMore={vi.fn()}
        threads={[
          thread,
          { ...thread, thread_id: "thread-2", turn_id: "turn-2" },
        ]}
      />,
    );

    expect(screen.getByText("Investigation 1")).toBeVisible();
    expect(screen.getByText("Investigation 2")).toBeVisible();
  });

  it("disables deletion for the actively streaming thread", () => {
    renderSidebar({ active: true });
    expect(screen.getByRole("button", { name: "Delete Investigation 1" })).toBeDisabled();
  });

  it("loads another cursor page only on request", async () => {
    const user = userEvent.setup();
    const onLoadMore = renderSidebar({ cursor: "opaque" });
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });
});
