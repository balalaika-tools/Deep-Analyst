import {
  parseInvestigationEvent,
  type InvestigationEvent,
  type RunCompletedEvent,
  type RunFailedEvent,
} from "./contracts";
import { decodeSseStream } from "./sse";

export class ProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProtocolError";
  }
}

export type TerminalEvent = RunCompletedEvent | RunFailedEvent;

export interface ProtocolExpectation {
  threadId: string;
  onEvent: (event: InvestigationEvent) => void | Promise<void>;
}

function parseFrame(eventName: string, data: string): InvestigationEvent {
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    throw new ProtocolError("SSE event data is not valid JSON");
  }
  const event = parseInvestigationEvent(payload);
  if (event.event !== eventName) {
    throw new ProtocolError("SSE event name does not match its envelope");
  }
  return event;
}

export async function consumeInvestigationStream(
  stream: ReadableStream<Uint8Array>,
  expectation: ProtocolExpectation,
): Promise<TerminalEvent> {
  let started = false;
  let turnId: string | null = null;
  let nextDeltaIndex = 0;
  let terminal: TerminalEvent | null = null;

  for await (const frame of decodeSseStream(stream)) {
    if (terminal) throw new ProtocolError("SSE stream emitted data after its terminal event");
    const event = parseFrame(frame.event, frame.data);
    if (event.thread_id !== expectation.threadId) {
      throw new ProtocolError("SSE event belongs to another thread");
    }
    if (!started) {
      if (event.event !== "run.started") {
        throw new ProtocolError("SSE stream did not start with run.started");
      }
      started = true;
      turnId = event.turn_id;
    } else {
      if (event.event === "run.started") throw new ProtocolError("Duplicate run.started event");
      if (event.turn_id !== turnId) throw new ProtocolError("SSE turn identity changed");
    }
    if (event.event === "answer.delta") {
      if (event.data.index !== nextDeltaIndex) {
        throw new ProtocolError("Answer deltas are not contiguous");
      }
      nextDeltaIndex += 1;
    }
    await expectation.onEvent(event);
    if (event.event === "run.completed" || event.event === "run.failed") terminal = event;
  }

  if (!started || !terminal) {
    throw new ProtocolError("SSE stream ended without a terminal event");
  }
  return terminal;
}
