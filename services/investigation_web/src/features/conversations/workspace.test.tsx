import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  InvestigationEvent,
  InvokeRequest,
  ThreadSummary,
} from "@/features/investigations/contracts";
import { ConversationWorkspace } from "./workspace";

const { push, refresh, deleteThread, invokeInvestigation } = vi.hoisted(() => ({
  push: vi.fn(),
  refresh: vi.fn(),
  deleteThread: vi.fn(),
  invokeInvestigation: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh }) }));
vi.mock("@/features/investigations/browser-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/investigations/browser-api")>();
  return {
    ...original,
    deleteThread,
    loadThreads: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    loadMessages: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
  };
});
vi.mock("@/features/investigations/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/investigations/client")>();
  return { ...original, invokeInvestigation };
});

const thread: ThreadSummary = {
  thread_id: "thread-1",
  case_id: "case-1",
  turn_id: "turn-1",
  status: "completed",
  created_at: "2026-09-02T12:00:00Z",
};

function renderWorkspace() {
  return render(
    <ConversationWorkspace
      caseId="case-1"
      initialMessages={{ items: [], next_cursor: null }}
      initialThreads={{ items: [thread], next_cursor: null }}
      threadId="thread-1"
    />,
  );
}

describe("ConversationWorkspace deletion", () => {
  beforeEach(() => {
    push.mockReset();
    refresh.mockReset();
    deleteThread.mockReset();
    invokeInvestigation.mockReset();
  });

  it("removes an active thread only after confirmed successful deletion", async () => {
    const user = userEvent.setup();
    deleteThread.mockResolvedValue(undefined);
    renderWorkspace();

    await user.click(screen.getAllByRole("button", { name: "Delete conversation for case-1" })[0]!);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(deleteThread).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteThread).toHaveBeenCalledWith("thread-1"));
    expect(screen.queryByText("Sep 2")).not.toBeInTheDocument();
    expect(push).toHaveBeenCalledWith("/cases/case-1");
  });

  it("keeps the thread and shows a safe error when deletion fails", async () => {
    const user = userEvent.setup();
    deleteThread.mockRejectedValue(new Error("The conversation could not be deleted."));
    renderWorkspace();
    await user.click(screen.getAllByRole("button", { name: "Delete conversation for case-1" })[0]!);
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be deleted");
    expect(screen.getAllByRole("link", { name: /case-1/i }).length).toBeGreaterThan(0);
  });

  it("closes confirmation without sending a request", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getAllByRole("button", { name: "Delete conversation for case-1" })[0]!);
    await user.click(screen.getByRole("button", { name: "Keep conversation" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteThread).not.toHaveBeenCalled();
  });

  it("aborts an active request once and restores the composer", async () => {
    const user = userEvent.setup();
    let aborts = 0;
    invokeInvestigation.mockImplementation(
      (_request: unknown, options: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () => {
            aborts += 1;
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
    );
    const view = renderWorkspace();
    await user.type(screen.getByLabelText("Message the investigation agent"), "Investigate");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await user.click(screen.getByRole("button", { name: "Cancel investigation" }));
    await waitFor(() => expect(screen.getByLabelText("Message the investigation agent")).toBeEnabled());
    view.unmount();
    expect(aborts).toBe(1);
  });

  it("retries a retryable failure with a fresh request id", async () => {
    const user = userEvent.setup();
    const requests: InvokeRequest[] = [];
    invokeInvestigation.mockImplementation(
      async (
        request: InvokeRequest,
        options: { onEvent: (event: InvestigationEvent) => void },
      ) => {
        requests.push(request);
        const base = {
          schema_version: 1 as const,
          thread_id: request.thread_id,
          turn_id: `turn-${request.request_id}`,
          timestamp: "2026-09-02T12:00:00Z",
        };
        options.onEvent({ ...base, event: "run.started", data: { status: "running" } });
        const failed = {
          ...base,
          event: "run.failed" as const,
          data: { code: "transient_exhausted", message: "Temporary", retryable: true },
        };
        options.onEvent(failed);
        return failed;
      },
    );
    renderWorkspace();

    await user.type(screen.getByLabelText("Message the investigation agent"), "Investigate");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await user.click(await screen.findByRole("button", { name: "Retry investigation" }));

    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1]).toMatchObject({
      thread_id: requests[0]?.thread_id,
      case_id: requests[0]?.case_id,
      message: requests[0]?.message,
    });
    expect(requests[1]?.request_id).not.toBe(requests[0]?.request_id);
  });
});
