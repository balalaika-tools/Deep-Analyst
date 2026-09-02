import Link from "next/link";

import { PlusIcon, TrashIcon } from "@/components/icons";
import type { ThreadSummary } from "@/features/investigations/contracts";

interface ThreadSidebarProps {
  threads: ThreadSummary[];
  activeThreadId: string | null;
  activeTurn: boolean;
  nextCursor: string | null;
  loadingMore: boolean;
  onLoadMore: () => void;
  onDelete: (thread: ThreadSummary) => void;
  onNavigate?: () => void;
}

const DATE_FORMAT = new Intl.DateTimeFormat("en", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function ThreadSidebar({
  threads,
  activeThreadId,
  activeTurn,
  nextCursor,
  loadingMore,
  onLoadMore,
  onDelete,
  onNavigate,
}: ThreadSidebarProps) {
  return (
    <div className="sidebar-content">
      <div className="brand-block">
        <span className="brand-mark">DA</span>
        <div>
          <strong>Deep Analyst</strong>
          <span>Investigation workspace</span>
        </div>
      </div>

      <Link className="new-thread-button" href="/" onClick={onNavigate}>
        <PlusIcon />
        New conversation
      </Link>

      <div className="sidebar-heading">
        <span>Conversations</span>
        <span className="thread-count">{threads.length}</span>
      </div>

      <nav aria-label="Previous conversations" className="thread-nav">
        {threads.length === 0 ? (
          <div className="sidebar-empty">
            <span className="empty-orbit" aria-hidden="true" />
            <p>No investigations yet.</p>
            <span>Your first question will create one.</span>
          </div>
        ) : (
          <ul className="thread-list">
            {threads.map((thread) => {
              const active = thread.thread_id === activeThreadId;
              return (
                <li className="thread-row" key={thread.thread_id}>
                  <Link
                    aria-current={active ? "page" : undefined}
                    aria-label={`Conversation ${thread.thread_id} ${DATE_FORMAT.format(new Date(thread.created_at))} ${thread.status}`}
                    className={`thread-link${active ? " is-active" : ""}`}
                    href={`/threads/${encodeURIComponent(thread.thread_id)}`}
                    onClick={onNavigate}
                  >
                    <span className="thread-case">Conversation</span>
                    <span className="thread-meta">
                      <time dateTime={thread.created_at}>{DATE_FORMAT.format(new Date(thread.created_at))}</time>
                      <span className={`status-dot status-${thread.status}`} aria-hidden="true" />
                      <span>{thread.status}</span>
                    </span>
                  </Link>
                  <button
                    aria-label={`Delete conversation ${thread.thread_id}`}
                    className="thread-delete"
                    disabled={active && activeTurn}
                    onClick={() => onDelete(thread)}
                    title={active && activeTurn ? "Cancel the running investigation before deleting" : "Delete conversation"}
                    type="button"
                  >
                    <TrashIcon />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      {nextCursor ? (
        <button className="load-more" disabled={loadingMore} onClick={onLoadMore} type="button">
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </div>
  );
}
