## Why

The investigation agent has a stable streaming and history API but no user-facing client. Analysts
need a focused interface that makes long-running investigation progress understandable, preserves
multi-turn context, and lets them return to or remove earlier conversations.

## What Changes

- Add a Next.js frontend for one case-scoped investigation workspace, with the active `case_id`
  supplied by the route rather than an authentication flow.
- Add a responsive conversation sidebar for listing, opening, creating, progressively loading,
  and deleting threads.
- Add a chat window that renders persisted history, submits idempotent turns, consumes the
  versioned POST SSE contract, exposes coarse progress, and incrementally renders committed answer
  deltas.
- Add explicit recovery behavior for pre-stream problem responses, retryable terminal failures,
  disconnects, malformed streams, thread conflicts, empty states, and history pagination.
- Treat `DELETE /v1/threads/{thread_id}` as a backend-provided prerequisite; its backend design and
  implementation remain outside this change.
- Do not add authentication, authorization, account ownership, case selection, agent controls, or
  administrative UI.

## Capabilities

### New Capabilities

- `investigation-chat-ui`: A case-scoped analyst chat experience covering thread navigation,
  deletion, paginated history, idempotent turn submission, and robust consumption of the
  investigation agent's SSE protocol.

### Modified Capabilities

None.

## Impact

- Adds a separately deployable Next.js application and its TypeScript, styling, test, and runtime
  configuration.
- Consumes `POST /v1/agent/invoke`, `GET /v1/threads`,
  `GET /v1/threads/{thread_id}/messages`, and the backend-owned
  `DELETE /v1/threads/{thread_id}` without authorization headers.
- Depends on the investigation-agent ownership-removal update described by the user and on the
  delete endpoint being available before the delete UI can be accepted end to end.
- Adds browser-facing configuration for the investigation-agent base URL and requires deployment
  routing that preserves unbuffered SSE delivery.
