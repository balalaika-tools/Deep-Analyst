import type {
  Citation,
  InvestigationEvent,
  InvokeRequest,
  ProgressPhase,
} from "@/features/investigations/contracts";

export type TurnPhase =
  | "idle"
  | "submitting"
  | "streaming_progress"
  | "streaming_answer"
  | "completed"
  | "failed"
  | "uncertain"
  | "cancelled";

export interface TurnFailure {
  message: string;
  retryable: boolean;
}

export interface TurnState {
  phase: TurnPhase;
  payload: InvokeRequest | null;
  turnId: string | null;
  progress: ProgressPhase | null;
  answer: string;
  nextDeltaIndex: number;
  messageId: string | null;
  citations: Citation[];
  failure: TurnFailure | null;
}

export const initialTurnState: TurnState = {
  phase: "idle",
  payload: null,
  turnId: null,
  progress: null,
  answer: "",
  nextDeltaIndex: 0,
  messageId: null,
  citations: [],
  failure: null,
};

export type TurnAction =
  | { type: "begin"; payload: InvokeRequest }
  | { type: "event"; event: InvestigationEvent }
  | { type: "transport-failed"; failure: TurnFailure }
  | { type: "uncertain"; message: string }
  | { type: "cancelled" }
  | { type: "reset" };

const ACTIVE_PHASES = new Set<TurnPhase>(["submitting", "streaming_progress", "streaming_answer"]);

export function isTurnActive(state: TurnState): boolean {
  return ACTIVE_PHASES.has(state.phase);
}

function applyEvent(state: TurnState, event: InvestigationEvent): TurnState {
  if (!state.payload || event.thread_id !== state.payload.thread_id) {
    throw new Error("Event does not match the active request");
  }
  if (event.event !== "run.started" && (!state.turnId || event.turn_id !== state.turnId)) {
    throw new Error("Event does not match the active turn");
  }
  switch (event.event) {
    case "run.started":
      if (state.phase !== "submitting") throw new Error("Turn has already started");
      return { ...state, phase: "streaming_progress", turnId: event.turn_id };
    case "progress":
      return { ...state, phase: "streaming_progress", progress: event.data.phase };
    case "answer.delta":
      if (event.data.index !== state.nextDeltaIndex) throw new Error("Unexpected answer delta");
      return {
        ...state,
        phase: "streaming_answer",
        answer: state.answer + event.data.text,
        nextDeltaIndex: state.nextDeltaIndex + 1,
      };
    case "run.completed":
      return {
        ...state,
        phase: "completed",
        messageId: event.data.message_id,
        citations: event.data.citations,
        failure: null,
      };
    case "run.failed":
      return {
        ...state,
        phase: "failed",
        failure: { message: event.data.message, retryable: event.data.retryable },
      };
  }
}

export function turnReducer(state: TurnState, action: TurnAction): TurnState {
  switch (action.type) {
    case "begin":
      if (isTurnActive(state)) throw new Error("A turn is already active");
      return { ...initialTurnState, phase: "submitting", payload: action.payload };
    case "event":
      return applyEvent(state, action.event);
    case "transport-failed":
      if (!state.payload) throw new Error("No request is active");
      return { ...state, phase: "failed", failure: action.failure };
    case "uncertain":
      if (!state.payload) throw new Error("No request is active");
      return {
        ...state,
        phase: "uncertain",
        failure: { message: action.message, retryable: true },
      };
    case "cancelled":
      if (!state.payload) throw new Error("No request is active");
      return { ...state, phase: "cancelled", failure: null };
    case "reset":
      return initialTurnState;
  }
}
