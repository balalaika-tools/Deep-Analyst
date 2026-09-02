import { getAgentBaseUrl } from "./environment";
import {
  isMessagePage,
  isProblemDetails,
  isThreadPage,
  type MessagePage,
  type ProblemDetails,
  type ThreadPage,
} from "@/features/investigations/contracts";

export class AgentApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly problem: ProblemDetails | null = null,
  ) {
    super(message);
    this.name = "AgentApiError";
  }
}

type FetchImplementation = typeof fetch;

export type AgentAvailability = "ready" | "unavailable";

function endpoint(path: string, cursor?: string | null): URL {
  const url = new URL(path, getAgentBaseUrl());
  if (cursor) url.searchParams.set("cursor", cursor);
  return url;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new AgentApiError("The investigation service returned an invalid response.", 502);
  }
}

async function expectPage<T>(
  response: Response,
  guard: (value: unknown) => value is T,
): Promise<T> {
  const payload = await readJson(response);
  if (!response.ok) {
    const problem = isProblemDetails(payload) ? payload : null;
    throw new AgentApiError(
      problem?.detail ?? "The investigation service is unavailable.",
      response.status,
      problem,
    );
  }
  if (!guard(payload)) {
    throw new AgentApiError("The investigation service returned an invalid response.", 502);
  }
  return payload;
}

async function fetchPage<T>(
  url: URL,
  guard: (value: unknown) => value is T,
  fetcher: FetchImplementation,
): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new AgentApiError("The investigation service is unavailable.", 503);
  }
  return expectPage(response, guard);
}

export async function fetchAgentAvailability(
  fetcher: FetchImplementation = fetch,
): Promise<AgentAvailability> {
  try {
    const response = await fetcher(endpoint("/ready"), {
      cache: "no-store",
      signal: AbortSignal.timeout(2_000),
    });
    return response.ok ? "ready" : "unavailable";
  } catch {
    return "unavailable";
  }
}

export async function fetchThreadPage(
  cursor: string | null = null,
  fetcher: FetchImplementation = fetch,
): Promise<ThreadPage> {
  return fetchPage(endpoint("/v1/threads", cursor), isThreadPage, fetcher);
}

export async function fetchMessagePage(
  threadId: string,
  cursor: string | null = null,
  fetcher: FetchImplementation = fetch,
): Promise<MessagePage> {
  const path = `/v1/threads/${encodeURIComponent(threadId)}/messages`;
  return fetchPage(endpoint(path, cursor), isMessagePage, fetcher);
}
