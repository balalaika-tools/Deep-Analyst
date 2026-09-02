import { useState, type FormEvent, type KeyboardEvent } from "react";

import { SendIcon, StopIcon } from "@/components/icons";

interface ComposerProps {
  busy: boolean;
  canRetry: boolean;
  onCancel: () => void;
  onRetry: () => void;
  onSubmit: (message: string) => void;
}

export function Composer({ busy, canRetry, onCancel, onRetry, onSubmit }: ComposerProps) {
  const [message, setMessage] = useState("");

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const value = message.trim();
    if (!value || busy) return;
    onSubmit(value);
    setMessage("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className="composer-wrap">
      {canRetry ? (
        <button className="retry-button" onClick={onRetry} type="button">Retry investigation</button>
      ) : null}
      <form className="composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="investigation-message">Message the investigation agent</label>
        <textarea
          disabled={busy}
          id="investigation-message"
          maxLength={64_000}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask an evidence-backed question…"
          rows={1}
          value={message}
        />
        {busy ? (
          <button aria-label="Cancel investigation" className="composer-action is-stop" onClick={onCancel} type="button"><StopIcon /></button>
        ) : (
          <button aria-label="Send message" className="composer-action" disabled={!message.trim()} type="submit"><SendIcon /></button>
        )}
      </form>
      <p className="composer-hint">Enter to send · Shift + Enter for a new line</p>
    </div>
  );
}
