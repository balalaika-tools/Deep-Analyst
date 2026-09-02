import {
  isProblemDetails,
  type InvestigationEvent,
  type InvokeRequest,
  type ProblemDetails,
} from "./contracts";
import { consumeInvestigationStream, type TerminalEvent } from "./protocol";

export class InvocationProblem extends Error {
  constructor(readonly problem: ProblemDetails) {
    super(problem.detail);
    this.name = "InvocationProblem";
  }
}

export class InvocationTransportError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message);
    this.name = "InvocationTransportError";
  }
}

async function safeProblem(response: Response): Promise<ProblemDetails | null> {
  try {
    const payload: unknown = await response.json();
    return isProblemDetails(payload) ? payload : null;
  } catch {
    return null;
  }
}

export async function invokeInvestigation(
  request: InvokeRequest,
  options: {
    signal: AbortSignal;
    onEvent: (event: InvestigationEvent) => void;
    fetcher?: typeof fetch;
  },
): Promise<TerminalEvent> {
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try {
    response = await fetcher("/api/investigation/invoke", {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify(request),
      cache: "no-store",
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      throw error;
    }
    throw new InvocationTransportError("The investigation service could not be reached.", true);
  }

  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (contentType.startsWith("application/problem+json") || !response.ok) {
    const problem = await safeProblem(response);
    if (problem) throw new InvocationProblem(problem);
    throw new InvocationTransportError("The investigation request was rejected.", response.status >= 500);
  }
  if (!contentType.startsWith("text/event-stream") || !response.body) {
    throw new InvocationTransportError("The investigation service returned an invalid stream.", true);
  }
  return consumeInvestigationStream(response.body, {
    threadId: request.thread_id,
    onEvent: options.onEvent,
  });
}
