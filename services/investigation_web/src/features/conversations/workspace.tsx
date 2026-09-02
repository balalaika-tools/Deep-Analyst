"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { CloseIcon, MenuIcon } from "@/components/icons";
import { deleteThread, loadMessages, loadThreads } from "@/features/investigations/browser-api";
import { InvocationProblem, InvocationTransportError, invokeInvestigation } from "@/features/investigations/client";
import type { InvokeRequest, MessagePage, ThreadPage, ThreadSummary } from "@/features/investigations/contracts";
import { ProtocolError } from "@/features/investigations/protocol";
import { ThreadSidebar } from "@/features/threads/thread-sidebar";
import { mergeMessages, mergeThreads } from "./collections";
import { Composer } from "./composer";
import { MessageList } from "./message-list";
import { displayStreamEvent } from "./stream-pacing";
import { initialTurnState, isTurnActive, turnReducer } from "./turn-state";

interface ConversationWorkspaceProps {
  threadId: string | null;
  initialThreads: ThreadPage;
  initialMessages: MessagePage;
}

export function ConversationWorkspace(props: ConversationWorkspaceProps) {
  return <ConversationWorkspaceState key={props.threadId ?? "new-conversation"} {...props} />;
}

function ConversationWorkspaceState({
  threadId,
  initialThreads,
  initialMessages,
}: ConversationWorkspaceProps) {
  const router = useRouter();
  const [threads, setThreads] = useState(initialThreads.items);
  const [threadCursor, setThreadCursor] = useState(initialThreads.next_cursor);
  const [messages, setMessages] = useState(initialMessages.items);
  const [messageCursor, setMessageCursor] = useState(initialMessages.next_cursor);
  const [currentThreadId, setCurrentThreadId] = useState(threadId);
  const [turn, dispatch] = useReducer(turnReducer, initialTurnState);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ThreadSummary | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const dialogCancelRef = useRef<HTMLButtonElement | null>(null);

  const active = isTurnActive(turn);
  const canRetry = Boolean(turn.payload && turn.failure?.retryable && !active);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    if (deleteTarget) dialogCancelRef.current?.focus();
  }, [deleteTarget]);

  async function refreshHistory(targetThreadId: string, requestId: string): Promise<boolean> {
    const [threadPage, messagePage] = await Promise.all([
      loadThreads(),
      loadMessages(targetThreadId),
    ]);
    setThreads(threadPage.items);
    setThreadCursor(threadPage.next_cursor);
    setMessages(messagePage.items);
    setMessageCursor(messagePage.next_cursor);
    return messagePage.items.some((message) => message.request_id === requestId);
  }

  async function reconcile(
    targetThreadId: string,
    requestId: string,
    resetWhenPersisted: boolean,
  ): Promise<void> {
    try {
      const persisted = await refreshHistory(targetThreadId, requestId);
      if (persisted && resetWhenPersisted) dispatch({ type: "reset" });
      if (persisted) {
        router.replace(`/threads/${encodeURIComponent(targetThreadId)}`, { scroll: false });
      } else {
        router.refresh();
      }
    } catch {
      // The provisional state remains visible until a later manual navigation or retry.
    }
  }

  async function runAttempt(payload: InvokeRequest): Promise<void> {
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    dispatch({ type: "begin", payload });
    let displayDeltaIndex = 0;

    try {
      const terminal = await invokeInvestigation(payload, {
        signal: controller.signal,
        onEvent: (event) => displayStreamEvent(
          event,
          () => displayDeltaIndex++,
          (displayEvent) => dispatch({ type: "event", event: displayEvent }),
          controller.signal,
        ),
      });
      await reconcile(payload.thread_id, payload.request_id, terminal.event === "run.completed");
    } catch (error) {
      if (controller.signal.aborted) {
        dispatch({ type: "cancelled" });
        await reconcile(payload.thread_id, payload.request_id, false);
      } else if (error instanceof InvocationProblem) {
        dispatch({
          type: "transport-failed",
          failure: { message: error.problem.detail, retryable: error.problem.retryable },
        });
      } else if (error instanceof InvocationTransportError) {
        dispatch({
          type: "transport-failed",
          failure: { message: error.message, retryable: error.retryable },
        });
      } else if (error instanceof ProtocolError || error instanceof Error) {
        dispatch({
          type: "uncertain",
          message: "The connection ended unexpectedly. Saved history is being checked.",
        });
        await reconcile(payload.thread_id, payload.request_id, false);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }

  function submit(message: string): void {
    if (active) return;
    const targetThreadId = currentThreadId ?? crypto.randomUUID();
    if (!currentThreadId) {
      setCurrentThreadId(targetThreadId);
    }
    void runAttempt({
      request_id: crypto.randomUUID(),
      thread_id: targetThreadId,
      message,
    });
  }

  function cancel(): void {
    abortRef.current?.abort();
  }

  function retry(): void {
    if (turn.payload && !active) {
      void runAttempt({ ...turn.payload, request_id: crypto.randomUUID() });
    }
  }

  async function loadMoreThreads(): Promise<void> {
    if (!threadCursor || loadingThreads) return;
    setLoadingThreads(true);
    try {
      const page = await loadThreads(threadCursor);
      setThreads((current) => mergeThreads(current, page.items));
      setThreadCursor(page.next_cursor);
    } finally {
      setLoadingThreads(false);
    }
  }

  async function loadMoreMessages(): Promise<void> {
    if (!messageCursor || !currentThreadId || loadingMessages) return;
    setLoadingMessages(true);
    try {
      const page = await loadMessages(currentThreadId, messageCursor);
      setMessages((current) => mergeMessages(current, page.items));
      setMessageCursor(page.next_cursor);
    } finally {
      setLoadingMessages(false);
    }
  }

  function closeDrawer(): void {
    setDrawerOpen(false);
    requestAnimationFrame(() => menuButtonRef.current?.focus());
  }

  async function confirmDelete(): Promise<void> {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteThread(deleteTarget.thread_id);
      setThreads((current) => current.filter((thread) => thread.thread_id !== deleteTarget.thread_id));
      const deletedActive = deleteTarget.thread_id === currentThreadId;
      setDeleteTarget(null);
      if (deletedActive) router.push("/");
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "The conversation could not be deleted.");
    } finally {
      setDeleting(false);
    }
  }

  const sidebar = (
    <ThreadSidebar
      activeThreadId={currentThreadId}
      activeTurn={active}
      loadingMore={loadingThreads}
      nextCursor={threadCursor}
      onDelete={(thread) => {
        setDeleteError(null);
        setDeleteTarget(thread);
      }}
      onLoadMore={() => void loadMoreThreads()}
      onNavigate={closeDrawer}
      threads={threads}
    />
  );

  return (
    <main className="workspace">
      <aside className="desktop-sidebar">{sidebar}</aside>
      {drawerOpen ? (
        <div className="drawer-layer">
          <button aria-label="Close conversations" className="drawer-backdrop" onClick={closeDrawer} type="button" />
          <aside aria-label="Conversation navigation" className="mobile-drawer">
            <button aria-label="Close conversations" className="drawer-close" onClick={closeDrawer} type="button"><CloseIcon /></button>
            {sidebar}
          </aside>
        </div>
      ) : null}

      <section className="conversation-panel" aria-labelledby="conversation-title">
        <header className="conversation-header">
          <button
            aria-expanded={drawerOpen}
            aria-label="Open conversations"
            className="menu-button"
            onClick={() => setDrawerOpen(true)}
            ref={menuButtonRef}
            type="button"
          ><MenuIcon /></button>
          <div className="case-title">
            <span className="eyebrow">Deep Analyst</span>
            <h1 id="conversation-title">Conversation</h1>
          </div>
          <div className="case-status"><span className="online-dot" /> Agent ready</div>
        </header>

        <div className="conversation-scroll">
          <MessageList
            loadingMore={loadingMessages}
            messages={messages}
            nextCursor={messageCursor}
            onLoadMore={() => void loadMoreMessages()}
            turn={turn}
          />
        </div>

        <Composer busy={active} canRetry={canRetry} onCancel={cancel} onRetry={retry} onSubmit={submit} />
      </section>

      {deleteTarget ? (
        <div className="dialog-layer">
          <div
            aria-describedby="delete-description"
            aria-labelledby="delete-title"
            aria-modal="true"
            className="confirm-dialog"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !deleting) setDeleteTarget(null);
            }}
            role="dialog"
          >
            <span className="dialog-icon" aria-hidden="true">!</span>
            <h2 id="delete-title">Delete this conversation?</h2>
            <p id="delete-description">This saved conversation will be permanently removed.</p>
            {deleteError ? <p className="dialog-error" role="alert">{deleteError}</p> : null}
            <div className="dialog-actions">
              <button className="button button-secondary" disabled={deleting} onClick={() => setDeleteTarget(null)} ref={dialogCancelRef} type="button">Keep conversation</button>
              <button className="button button-danger" disabled={deleting} onClick={() => void confirmDelete()} type="button">{deleting ? "Deleting…" : "Delete"}</button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
