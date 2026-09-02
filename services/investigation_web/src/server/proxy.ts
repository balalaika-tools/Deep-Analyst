import { getAgentBaseUrl } from "./environment";

const FORWARDED_RESPONSE_HEADERS = [
  "cache-control",
  "content-type",
  "retry-after",
  "x-accel-buffering",
] as const;

function safeFailure(): Response {
  return Response.json(
    {
      schema_version: 1,
      type: "urn:investigation-web:problem:upstream_unavailable",
      title: "Bad Gateway",
      status: 502,
      code: "upstream_unavailable",
      detail: "The investigation service is unavailable.",
      retryable: true,
    },
    {
      status: 502,
      headers: { "Cache-Control": "no-store" },
    },
  );
}

function responseHeaders(upstream: Response): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("Cache-Control", "no-cache, no-store");
  if (upstream.headers.get("content-type")?.startsWith("text/event-stream")) {
    headers.set("X-Accel-Buffering", "no");
    headers.set("Connection", "keep-alive");
  }
  return headers;
}

export async function proxyAgentRequest(
  request: Request,
  upstreamPath: string,
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  const target = new URL(upstreamPath, getAgentBaseUrl());
  target.search = new URL(request.url).search;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  try {
    const upstream = await fetcher(target, {
      method: request.method,
      cache: "no-store",
      signal: request.signal,
      headers: hasBody
        ? { "Content-Type": request.headers.get("content-type") ?? "application/json" }
        : undefined,
      body: hasBody ? await request.arrayBuffer() : undefined,
    });
    return new Response(upstream.status === 204 ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders(upstream),
    });
  } catch (error) {
    if (request.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      throw error;
    }
    return safeFailure();
  }
}
