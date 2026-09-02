import {
  isMessagePage,
  isProblemDetails,
  isThreadPage,
  type MessagePage,
  type ProblemDetails,
  type ThreadPage,
} from "./contracts";

export class BrowserApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly problem: ProblemDetails | null,
  ) {
    super(message);
    this.name = "BrowserApiError";
  }
}

async function json(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function getPage<T>(url: string, guard: (value: unknown) => value is T): Promise<T> {
  const response = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
  const payload = await json(response);
  if (!response.ok) {
    const problem = isProblemDetails(payload) ? payload : null;
    throw new BrowserApiError(problem?.detail ?? "The request could not be completed.", response.status, problem);
  }
  if (!guard(payload)) throw new BrowserApiError("The service returned an invalid response.", 502, null);
  return payload;
}

export function loadThreads(cursor: string | null = null): Promise<ThreadPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return getPage(`/api/investigation/threads${query}`, isThreadPage);
}

export function loadMessages(threadId: string, cursor: string | null = null): Promise<MessagePage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return getPage(
    `/api/investigation/threads/${encodeURIComponent(threadId)}/messages${query}`,
    isMessagePage,
  );
}

export async function deleteThread(threadId: string): Promise<void> {
  const response = await fetch(`/api/investigation/threads/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (response.ok) return;
  const payload = await json(response);
  const problem = isProblemDetails(payload) ? payload : null;
  throw new BrowserApiError(problem?.detail ?? "The conversation could not be deleted.", response.status, problem);
}
