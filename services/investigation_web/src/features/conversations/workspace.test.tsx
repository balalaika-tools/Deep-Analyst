import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  InvestigationEvent,
  InvokeRequest,
  ThreadSummary,
} from "@/features/investigations/contracts";
import { ConversationWorkspace } from "./workspace";

const { push, deleteThread, invokeInvestigation, loadMessages, loadThreads } = vi.hoisted(() => ({
  push: vi.fn(),
  deleteThread: vi.fn(),
  invokeInvestigation: vi.fn(),
  loadMessages: vi.fn(),
  loadThreads: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/features/investigations/browser-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/investigations/browser-api")>();
  return {
    ...original,
    deleteThread,
    loadThreads,
    loadMessages,
  };
});
vi.mock("@/features/investigations/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/investigations/client")>();
  return { ...original, invokeInvestigation };
});

const thread: ThreadSummary = {
  thread_id: "thread-1",
  turn_id: "turn-1",
  status: "completed",
  created_at: "2026-09-02T12:00:00Z",
};

function renderWorkspace() {
  return render(
    <ConversationWorkspace
      initialMessages={{ items: [], next_cursor: null }}
      initialThreads={{ items: [thread], next_cursor: null }}
      threadId="thread-1"
    />,
  );
}

describe("ConversationWorkspace deletion", () => {
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    push.mockReset();
    deleteThread.mockReset();
    invokeInvestigation.mockReset();
    loadThreads.mockReset();
    loadThreads.mockResolvedValue({ items: [], next_cursor: null });
    loadMessages.mockReset();
    loadMessages.mockResolvedValue({ items: [], next_cursor: null });
  });

  it("removes an active thread only after confirmed successful deletion", async () => {
    const user = userEvent.setup();
    deleteThread.mockResolvedValue(undefined);
    renderWorkspace();

    await user.click(screen.getAllByRole("button", { name: "Delete Investigation 1" })[0]!);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(deleteThread).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteThread).toHaveBeenCalledWith("thread-1"));
    expect(screen.queryByText("Sep 2")).not.toBeInTheDocument();
    expect(push).toHaveBeenCalledWith("/");
  });

  it("keeps the thread and shows a safe error when deletion fails", async () => {
    const user = userEvent.setup();
    deleteThread.mockRejectedValue(new Error("The conversation could not be deleted."));
    renderWorkspace();
    await user.click(screen.getAllByRole("button", { name: "Delete Investigation 1" })[0]!);
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be deleted");
    expect(screen.getAllByRole("link", { name: /conversation/i }).length).toBeGreaterThan(0);
  });

  it("closes confirmation without sending a request", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getAllByRole("button", { name: "Delete Investigation 1" })[0]!);
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
      message: requests[0]?.message,
    });
    expect(requests[1]?.request_id).not.toBe(requests[0]?.request_id);
  });

  it("keeps the completed streamed response mounted while history reconciles", async () => {
    const user = userEvent.setup();
    loadMessages.mockImplementation(async () => ({
      items: [
        {
          message_id: "user-message",
          sequence: 1,
          turn_id: "turn-complete",
          request_id: "request-complete",
          role: "user",
          content: "Investigate",
          citations: [],
          turn_status: "completed",
          created_at: "2026-09-02T12:00:00Z",
        },
        {
          message_id: "assistant-message",
          sequence: 2,
          turn_id: "turn-complete",
          request_id: "request-complete",
          role: "assistant",
          content: "Persisted answer",
          citations: [],
          turn_status: "completed",
          created_at: "2026-09-02T12:00:01Z",
        },
      ],
      next_cursor: null,
    }));
    invokeInvestigation.mockImplementation(
      async (
        request: InvokeRequest,
        options: { onEvent: (event: InvestigationEvent) => void | Promise<void> },
      ) => {
        const base = {
          schema_version: 1 as const,
          thread_id: request.thread_id,
          turn_id: "turn-complete",
          timestamp: "2026-09-02T12:00:00Z",
        };
        await options.onEvent({ ...base, event: "run.started", data: { status: "running" } });
        await options.onEvent({
          ...base,
          event: "answer.delta",
          data: { index: 0, text: "Streamed answer" },
        });
        const completed = {
          ...base,
          event: "run.completed" as const,
          data: { message_id: "assistant-message", citations: [], status: "completed" as const },
        };
        await options.onEvent(completed);
        return completed;
      },
    );
    vi.spyOn(crypto, "randomUUID").mockReturnValueOnce("thread-new").mockReturnValueOnce("request-complete");
    const historyReplace = vi.spyOn(window.history, "replaceState");
    render(
      <ConversationWorkspace
        initialMessages={{ items: [], next_cursor: null }}
        initialThreads={{ items: [], next_cursor: null }}
        threadId={null}
      />,
    );

    await user.type(screen.getByLabelText("Message the investigation agent"), "Investigate");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Streamed answer")).toBeVisible();
    await waitFor(() => expect(loadMessages).toHaveBeenCalledWith("thread-new"));
    expect(screen.queryByText("Persisted answer")).not.toBeInTheDocument();
    expect(screen.getByText("Streamed answer")).toBeVisible();
    expect(historyReplace).toHaveBeenCalledWith(null, "", "/threads/thread-new");
  });
});
