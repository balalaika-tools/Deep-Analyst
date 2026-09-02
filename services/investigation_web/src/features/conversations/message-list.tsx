"use client";

import { useEffect, useRef } from "react";

import { SparkIcon } from "@/components/icons";
import type { Citation, MessageItem, ProgressPhase } from "@/features/investigations/contracts";
import { isTurnActive, type TurnState } from "./turn-state";

const PROGRESS_LABELS: Record<ProgressPhase, string> = {
  checking_scope: "Checking the request",
  updating_context: "Updating case context",
  planning: "Planning the investigation",
  searching_evidence: "Searching evidence",
  querying_records: "Querying records",
  finding_connections: "Finding connections",
  verifying_answer: "Verifying the answer",
  committing_answer: "Saving the verified answer",
};

interface MessageListProps {
  messages: MessageItem[];
  turn: TurnState;
  nextCursor: string | null;
  loadingMore: boolean;
  onLoadMore: () => void;
}

function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <details className="citations">
      <summary>{citations.length} source{citations.length === 1 ? "" : "s"}</summary>
      <ol>
        {citations.map((citation) => (
          <li key={`${citation.evidence_id}:${citation.content_hash}`}>
            <strong>{citation.evidence_id}</strong>
            <span>Record {citation.source_ref.record_id}</span>
            <span>Field {citation.source_ref.locator.field}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function Message({ message }: { message: MessageItem }) {
  const assistant = message.role === "assistant";
  return (
    <article className={`message message-${message.role}`} aria-label={`${message.role} message`}>
      {assistant ? <span className="assistant-avatar"><SparkIcon /></span> : null}
      <div className="message-body">
        <div className="message-copy">{message.content}</div>
        <div className="message-footer">
          <time dateTime={message.created_at}>
            {new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" }).format(new Date(message.created_at))}
          </time>
          {message.turn_status !== "completed" ? <span>{message.turn_status}</span> : null}
        </div>
        {assistant ? <CitationList citations={message.citations} /> : null}
      </div>
    </article>
  );
}

function InvestigationActivity({ phase }: { phase: ProgressPhase | null }) {
  const label = phase ? PROGRESS_LABELS[phase] : "Starting investigation";
  return (
    <article
      aria-atomic="true"
      aria-label="Investigation progress"
      aria-live="polite"
      className="message message-assistant investigation-activity"
      role="status"
    >
      <span className="assistant-avatar"><SparkIcon /></span>
      <div className="activity-card">
        <span aria-hidden="true" className="activity-spinner" />
        <span className="activity-copy">
          <strong>Investigating</strong>
          <span>{label}</span>
        </span>
      </div>
    </article>
  );
}

export function MessageList({ messages, turn, nextCursor, loadingMore, onLoadMore }: MessageListProps) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const active = isTurnActive(turn);
  const activeRequestId = turn.payload?.request_id;
  const showPendingUser = turn.payload && turn.phase !== "idle" && !messages.some((item) => item.request_id === turn.payload?.request_id);
  const showAssistant = turn.answer.length > 0;
  const showActivity = active && !showAssistant;

  useEffect(() => {
    if (!active) return;
    const node = endRef.current;
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [active, activeRequestId, turn.phase, turn.progress]);

  return (
    <div className="transcript" aria-label="Investigation transcript">
      {nextCursor ? (
        <button className="load-older" disabled={loadingMore} onClick={onLoadMore} type="button">
          {loadingMore ? "Loading history…" : "Load older messages"}
        </button>
      ) : null}
      {messages.length === 0 && !showPendingUser ? (
        <section className="conversation-empty">
          <span className="empty-spark"><SparkIcon /></span>
          <span className="eyebrow">Case intelligence</span>
          <h2>Where should we begin?</h2>
          <p>Ask about entities, records, transactions, or connections. Answers are released only after evidence verification.</p>
        </section>
      ) : null}
      {messages.map((message) => <Message key={message.message_id} message={message} />)}
      {showPendingUser ? (
        <article className="message message-user is-pending" aria-label="Pending user message">
          <div className="message-body"><div className="message-copy">{turn.payload?.message}</div><div className="message-footer">Sending…</div></div>
        </article>
      ) : null}
      {showActivity ? <InvestigationActivity phase={turn.progress} /> : null}
      {showAssistant ? (
        <article className="message message-assistant is-streaming" aria-label="Streaming assistant message">
          <span className="assistant-avatar"><SparkIcon /></span>
          <div className="message-body">
            <div className="message-copy">{turn.answer}<span className="stream-caret" aria-hidden="true" /></div>
            {turn.phase === "completed" ? <CitationList citations={turn.citations} /> : null}
          </div>
        </article>
      ) : null}
      {turn.failure ? (
        <div className={`turn-notice${turn.phase === "uncertain" ? " is-warning" : ""}`} role="alert">
          <strong>{turn.phase === "uncertain" ? "Checking saved history" : "Investigation stopped"}</strong>
          <span>{turn.failure.message}</span>
        </div>
      ) : null}
      <div aria-hidden="true" ref={endRef} />
    </div>
  );
}
