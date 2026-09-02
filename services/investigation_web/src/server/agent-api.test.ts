import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AgentApiError,
  fetchAgentAvailability,
  fetchMessagePage,
  fetchThreadPage,
} from "./agent-api";

const timestamp = "2026-09-02T12:00:00Z";

describe("agent API reads", () => {
  afterEach(() => {
    delete process.env.INVESTIGATION_AGENT_URL;
  });

  it("passes an opaque cursor without auth headers", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ items: [], next_cursor: null }),
    );
    await fetchThreadPage("opaque+/=", fetcher);

    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(String(url)).toBe("http://agent:8080/v1/threads?cursor=opaque%2B%2F%3D");
    expect(init).toMatchObject({ cache: "no-store", headers: { Accept: "application/json" } });
    expect(JSON.stringify(init)).not.toMatch(/authorization|owner/i);
  });

  it("reads a valid message page", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({
        items: [
          {
            message_id: "message-1",
            sequence: 1,
            turn_id: "turn-1",
            request_id: "request-1",
            role: "user",
            content: "Question",
            citations: [],
            turn_status: "completed",
            created_at: timestamp,
          },
        ],
        next_cursor: null,
      }),
    );
    await expect(fetchMessagePage("thread-1", null, fetcher)).resolves.toMatchObject({
      items: [{ message_id: "message-1" }],
    });
  });

  it("preserves a safe upstream problem", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const problem = {
      schema_version: 1 as const,
      type: "urn:investigation-agent:problem:invalid_cursor",
      title: "Invalid Request",
      status: 422,
      code: "invalid_cursor",
      detail: "The pagination cursor is invalid.",
      retryable: false,
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(problem, { status: 422 }));

    await expect(fetchThreadPage(null, fetcher)).rejects.toEqual(
      expect.objectContaining<Partial<AgentApiError>>({ status: 422, problem }),
    );
  });

  it("rejects a successful but invalid payload", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ items: [], next_cursor: null, private: true }),
    );
    await expect(fetchThreadPage(null, fetcher)).rejects.toMatchObject({ status: 502 });
  });

  it("reports readiness without exposing the agent URL to the browser", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));

    await expect(fetchAgentAvailability(fetcher)).resolves.toBe("ready");
    expect(String(fetcher.mock.calls[0]?.[0])).toBe("http://agent:8080/ready");
  });

  it("maps connection failures to an unavailable status and safe page error", async () => {
    process.env.INVESTIGATION_AGENT_URL = "http://agent:8080";
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("private network detail"));

    await expect(fetchAgentAvailability(fetcher)).resolves.toBe("unavailable");
    await expect(fetchThreadPage(null, fetcher)).rejects.toMatchObject({
      message: "The investigation service is unavailable.",
      status: 503,
    });
  });
});
