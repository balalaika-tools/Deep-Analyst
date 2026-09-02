import http from "node:http";

const port = Number(process.env.FIXTURE_AGENT_PORT ?? 8181);
const timestamp = "2026-09-02T12:00:00Z";
const encoder = new TextEncoder();

const baseThreads = [
  { thread_id: "thread-1", case_id: "case-1", turn_id: "turn-1", status: "completed", created_at: timestamp },
  { thread_id: "thread-fail", case_id: "case-1", turn_id: "turn-fail", status: "failed", created_at: "2026-09-01T12:00:00Z" },
  { thread_id: "thread-2", case_id: "case-2", turn_id: "turn-2", status: "completed", created_at: "2026-08-31T12:00:00Z" },
];

const baseMessages = {
  "thread-1": [
    { message_id: "message-1", sequence: 1, turn_id: "turn-1", request_id: "request-old", role: "user", content: "Trace account 77", citations: [], turn_status: "completed", created_at: timestamp },
    { message_id: "message-2", sequence: 2, turn_id: "turn-1", request_id: "request-old", role: "assistant", content: "Account 77 connects to three reviewed records.", citations: [{ evidence_id: "evidence-1", content_hash: "a".repeat(64), source_ref: { record_id: "record-7", locator: { kind: "field", field: "account_id" } } }], turn_status: "completed", created_at: timestamp },
  ],
  "thread-fail": [
    { message_id: "message-fail", sequence: 1, turn_id: "turn-fail", request_id: "request-fail", role: "user", content: "Earlier failed investigation", citations: [], turn_status: "failed", created_at: timestamp },
  ],
  "thread-2": [
    { message_id: "message-3", sequence: 1, turn_id: "turn-2", request_id: "request-2", role: "user", content: "Review the second case", citations: [], turn_status: "completed", created_at: timestamp },
  ],
};

let threads;
let messages;
let attempts;

function reset() {
  threads = structuredClone(baseThreads);
  messages = structuredClone(baseMessages);
  attempts = new Map();
}

reset();

function json(response, status, body, contentType = "application/json") {
  response.writeHead(status, { "Content-Type": contentType, "Cache-Control": "no-store" });
  response.end(JSON.stringify(body));
}

function problem(response, status, code, detail, retryable) {
  json(response, status, {
    schema_version: 1,
    type: `urn:investigation-agent:problem:${code}`,
    title: status === 404 ? "Not Found" : "Service Unavailable",
    status,
    code,
    detail,
    retryable,
  }, "application/problem+json");
}

function envelope(event, threadId, turnId, data) {
  return `event: ${event}\ndata: ${JSON.stringify({ schema_version: 1, event, thread_id: threadId, turn_id: turnId, timestamp, data })}\n\n`;
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      try { resolve(JSON.parse(body)); } catch (error) { reject(error); }
    });
    request.on("error", reject);
  });
}

async function emit(response, chunks, delay = 45) {
  for (const chunk of chunks) {
    response.write(encoder.encode(chunk));
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
  response.end();
}

function addCompletedTurn(body, turnId, answer) {
  const existing = messages[body.thread_id] ?? [];
  const sequence = existing.length + 1;
  messages[body.thread_id] = [
    ...existing,
    { message_id: `${body.request_id}-user`, sequence, turn_id: turnId, request_id: body.request_id, role: "user", content: body.message, citations: [], turn_status: "completed", created_at: timestamp },
    { message_id: `${body.request_id}-assistant`, sequence: sequence + 1, turn_id: turnId, request_id: body.request_id, role: "assistant", content: answer, citations: [], turn_status: "completed", created_at: timestamp },
  ];
  const found = threads.find((thread) => thread.thread_id === body.thread_id);
  if (!found) threads.unshift({ thread_id: body.thread_id, case_id: body.case_id, turn_id: turnId, status: "completed", created_at: timestamp });
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host}`);
  if (url.pathname === "/health") return json(response, 200, { status: "ok" });
  if (url.pathname === "/ready") return json(response, 200, { status: "ready" });
  if (url.pathname === "/__reset" && request.method === "POST") {
    reset();
    return json(response, 200, { status: "reset" });
  }
  if (url.pathname === "/v1/threads" && request.method === "GET") {
    const secondPage = url.searchParams.get("cursor") === "page-2";
    return json(response, 200, {
      items: secondPage ? threads.slice(2) : threads.slice(0, 2),
      next_cursor: !secondPage && threads.length > 2 ? "page-2" : null,
    });
  }

  const messageMatch = url.pathname.match(/^\/v1\/threads\/([^/]+)\/messages$/);
  if (messageMatch && request.method === "GET") {
    const threadId = decodeURIComponent(messageMatch[1]);
    if (!messages[threadId]) return problem(response, 404, "resource_not_found", "The requested resource is not available.", false);
    return json(response, 200, { items: messages[threadId], next_cursor: null });
  }

  const threadMatch = url.pathname.match(/^\/v1\/threads\/([^/]+)$/);
  if (threadMatch && request.method === "DELETE") {
    const threadId = decodeURIComponent(threadMatch[1]);
    if (threadId === "thread-fail") return problem(response, 503, "persistence_failed", "The result could not be durably confirmed.", true);
    threads = threads.filter((thread) => thread.thread_id !== threadId);
    delete messages[threadId];
    response.writeHead(204, { "Cache-Control": "no-store" });
    return response.end();
  }

  if (url.pathname === "/v1/agent/invoke" && request.method === "POST") {
    const body = await readBody(request);
    const turnId = `turn-${body.request_id}`;
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-store",
      "X-Accel-Buffering": "no",
    });
    response.flushHeaders();
    const started = envelope("run.started", body.thread_id, turnId, { status: "running" });
    const progress = envelope("progress", body.thread_id, turnId, { phase: "searching_evidence", tool: "search_evidence", attempt: 1, count: null });

    if (body.message.toLowerCase().includes("malformed")) return emit(response, [started, progress]);
    if (body.message.toLowerCase().includes("slow")) return emit(response, [started, progress], 1_000);

    const attemptKey = `${body.thread_id}:${body.message}`;
    const count = (attempts.get(attemptKey) ?? 0) + 1;
    attempts.set(attemptKey, count);
    if (body.message.toLowerCase().includes("retry") && count === 1) {
      const failed = envelope("run.failed", body.thread_id, turnId, { code: "dependency_unavailable", message: "A required service is temporarily unavailable.", retryable: true });
      return emit(response, [started, progress, failed]);
    }

    const answer = "Verified connection found across the reviewed evidence.";
    addCompletedTurn(body, turnId, answer);
    return emit(response, [
      started,
      progress,
      envelope("answer.delta", body.thread_id, turnId, { index: 0, text: "Verified connection " }),
      envelope("answer.delta", body.thread_id, turnId, { index: 1, text: "found across the reviewed evidence." }),
      envelope("run.completed", body.thread_id, turnId, { message_id: `${body.request_id}-assistant`, citations: [], status: "completed" }),
    ]);
  }

  return problem(response, 404, "resource_not_found", "The requested resource is not available.", false);
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`fixture agent listening on ${port}\n`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
