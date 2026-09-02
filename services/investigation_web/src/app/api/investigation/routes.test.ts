import { afterEach, describe, expect, it, vi } from "vitest";

import { POST as invoke } from "./invoke/route";
import { GET as threads } from "./threads/route";
import { DELETE as removeThread } from "./threads/[threadId]/route";
import { GET as messages } from "./threads/[threadId]/messages/route";

describe("investigation route handlers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.INVESTIGATION_AGENT_URL;
  });

  it("routes thread and message reads without auth metadata", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ items: [], next_cursor: null }),
    );
    vi.stubGlobal("fetch", fetcher);

    await threads(new Request("http://web/api/investigation/threads?cursor=opaque"));
    await messages(
      new Request("http://web/api/investigation/threads/thread-1/messages"),
      { params: Promise.resolve({ threadId: "thread-1" }) },
    );

    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "http://agent:8080/v1/threads?cursor=opaque",
      "http://agent:8080/v1/threads/thread-1/messages",
    ]);
    expect(JSON.stringify(fetcher.mock.calls)).not.toMatch(/authorization|owner/i);
  });

  it("routes deletion and accepts a bodyless success", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const response = await removeThread(
      new Request("http://web/api/investigation/threads/thread-1", { method: "DELETE" }),
      { params: Promise.resolve({ threadId: "thread-1" }) },
    );
    expect(response.status).toBe(204);
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe("DELETE");
  });

  it("routes POST invocation as an SSE response", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("event: run.started\n\n", { headers: { "Content-Type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetcher);
    const response = await invoke(
      new Request("http://web/api/investigation/invoke", { method: "POST", body: "{}" }),
    );
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(await response.text()).toBe("event: run.started\n\n");
  });
});
