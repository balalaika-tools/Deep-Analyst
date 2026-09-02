import { describe, expect, it, vi } from "vitest";

import { invokeInvestigation, InvocationProblem, InvocationTransportError } from "./client";

const request = {
  request_id: "request-1",
  thread_id: "thread-1",
  case_id: "case-1",
  message: "Investigate",
};

describe("invokeInvestigation", () => {
  it("does not decode a non-streaming problem as SSE", async () => {
    const problem = {
      schema_version: 1 as const,
      type: "urn:investigation-agent:problem:thread_busy",
      title: "Conflict",
      status: 409,
      code: "thread_busy",
      detail: "Another request is already running for this thread.",
      retryable: true,
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json(problem, {
        status: 409,
        headers: { "Content-Type": "application/problem+json" },
      }),
    );
    const promise = invokeInvestigation(request, {
      signal: new AbortController().signal,
      onEvent: vi.fn(),
      fetcher,
    });
    await expect(promise).rejects.toBeInstanceOf(InvocationProblem);
    await promise.catch((error: unknown) => {
      expect((error as InvocationProblem).problem).toEqual(problem);
    });
  });

  it("rejects an unsupported successful content type", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("ok", { headers: { "Content-Type": "text/plain" } }),
    );
    await expect(
      invokeInvestigation(request, {
        signal: new AbortController().signal,
        onEvent: vi.fn(),
        fetcher,
      }),
    ).rejects.toBeInstanceOf(InvocationTransportError);
  });
});
