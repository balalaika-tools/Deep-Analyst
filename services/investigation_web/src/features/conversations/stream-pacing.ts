import type { AnswerDeltaEvent, InvestigationEvent } from "@/features/investigations/contracts";

const DISPLAY_CHUNK_SIZE = 12;
const DISPLAY_INTERVAL_MS = 22;

function waitForNextChunk(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const handleAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("The operation was aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, DISPLAY_INTERVAL_MS);
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

function shouldPaceStream(): boolean {
  return !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export async function displayStreamEvent(
  event: InvestigationEvent,
  nextIndex: () => number,
  dispatch: (event: InvestigationEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  if (event.event !== "answer.delta" || !shouldPaceStream()) {
    dispatch(event.event === "answer.delta"
      ? { ...event, data: { ...event.data, index: nextIndex() } }
      : event);
    return;
  }

  const chunks = event.data.text.match(new RegExp(`.{1,${DISPLAY_CHUNK_SIZE}}`, "gs")) ?? [];
  for (const [chunkIndex, text] of chunks.entries()) {
    if (signal.aborted) throw new DOMException("The operation was aborted", "AbortError");
    const pacedEvent: AnswerDeltaEvent = {
      ...event,
      data: { index: nextIndex(), text },
    };
    dispatch(pacedEvent);
    if (chunkIndex < chunks.length - 1) await waitForNextChunk(signal);
  }
}
