import { afterEach, describe, expect, it, vi } from "vitest";

import { proxyAgentRequest } from "./proxy";

describe("agent proxy", () => {
  afterEach(() => {
    delete process.env.INVESTIGATION_AGENT_URL;
  });

  it("preserves query, status, content type, retry guidance, and safe body", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const upstream = Response.json({ code: "thread_busy" }, { status: 409 });
    upstream.headers.set("Retry-After", "1");
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(upstream);
    const response = await proxyAgentRequest(
      new Request("http://web/api/investigation/threads?cursor=opaque"),
      "/v1/threads",
      fetcher,
    );

    expect(fetcher.mock.calls[0]?.[0].toString()).toBe("http://agent:8080/v1/threads?cursor=opaque");
    expect(response.status).toBe(409);
    expect(response.headers.get("retry-after")).toBe("1");
    await expect(response.json()).resolves.toEqual({ code: "thread_busy" });
  });

  it("accepts a bodyless successful delete", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    const response = await proxyAgentRequest(
      new Request("http://web/api/investigation/threads/thread-1", { method: "DELETE" }),
      "/v1/threads/thread-1",
      fetcher,
    );
    expect(response.status).toBe(204);
    await expect(response.text()).resolves.toBe("");
  });

  it("delivers the first SSE chunk before the upstream closes", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(value) {
        controller = value;
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
    );
    const response = await proxyAgentRequest(
      new Request("http://web/api/investigation/invoke", { method: "POST", body: "{}" }),
      "/v1/agent/invoke",
      fetcher,
    );
    const reader = response.body?.getReader();
    controller?.enqueue(new TextEncoder().encode("event: run.started\n\n"));

    await expect(reader?.read()).resolves.toMatchObject({ done: false });
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    controller?.close();
  });

  it("converts internal connection errors to a bounded problem", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("secret host detail"));
    const response = await proxyAgentRequest(
      new Request("http://web/api/investigation/threads"),
      "/v1/threads",
      fetcher,
    );
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("secret host detail");
  });
});
