# Investigation Web

Next.js analyst workspace for the investigation-agent API.

## Local development

```bash
cp .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000/cases/<case_id>`. The server-only
`INVESTIGATION_AGENT_URL` must point at the FastAPI service and is never sent to the browser.

The frontend assumes an unauthenticated backend contract with:

- `GET /v1/threads`
- `GET /v1/threads/{thread_id}/messages`
- `POST /v1/agent/invoke`
- `DELETE /v1/threads/{thread_id}`

See the delivery section below for streaming and container setup.

## Routes and interaction

- `/cases/<case_id>` opens a fresh conversation scoped to the case in the path.
- `/cases/<case_id>/threads/<thread_id>` opens durable checkpoint-backed history.
- Thread summaries are paginated as returned by the backend; the UI does not fetch every
  transcript to manufacture titles.
- Retrying a retryable turn creates a new `request_id` while preserving the thread, case, and
  message. This starts a real new attempt instead of replaying the idempotent failed result.
- Deletion is confirmed and is never optimistic. Any successful 2xx delete response is accepted;
  no response body is required.

There is deliberately no authentication layer in this prototype. The frontend sends only
`request_id`, `thread_id`, `case_id`, and `message` for invocation. Authentication and case
authorization can later be introduced at the backend boundary without changing agent semantics.

## Streaming contract

Invocation uses `fetch()` over POST and incrementally parses `text/event-stream`; browser
`EventSource` is not used because it cannot POST. The Next.js Route Handler forwards the upstream
body as a `ReadableStream` and propagates cancellation through the request signal.

Every deployment hop must preserve streaming and disable buffering. The supported events are
`run.started`, `progress`, `answer.delta`, `run.completed`, and `run.failed`; SSE comments are
heartbeats. The client rejects unknown schema versions, identity changes, out-of-order deltas,
duplicate terminal events, and premature EOF. After an uncertain disconnect it reloads durable
history and never creates a replacement request automatically.

For Nginx or another reverse proxy, buffering must remain disabled for
`/api/investigation/invoke`. Do not add response compression or caching at that path unless it has
been verified to flush individual SSE events.

## Verification

```bash
npm run lint
npm run typecheck
npm run test:run
INVESTIGATION_AGENT_URL=http://127.0.0.1:8080 npm run build
npm run test:e2e
```

The Playwright suite starts an isolated deterministic fixture upstream on port `8181` and the
production Next.js server on port `3100`. It covers progressive delivery, retry, cancellation,
malformed termination, history, cross-case navigation, deletion, and the mobile drawer.

To verify the real FastAPI service, start it first and then run:

```bash
INVESTIGATION_AGENT_URL=http://127.0.0.1:8080 npm run dev
```

Open `/cases/case_trg_001`, submit a question, and confirm that progress appears before the final
answer and that a refresh restores the committed transcript.

## Container image

Build from the repository root so the Dockerfile can copy the service package deterministically:

```bash
docker build -f services/investigation_web/Dockerfile -t deep-analyst-investigation-web:local .
docker run --rm -p 3100:3000 \
  -e INVESTIGATION_AGENT_URL=http://host.docker.internal:8080 \
  deep-analyst-investigation-web:local
```

The runtime value must be reachable from inside the container. In Compose it should be
`http://investigation-agent:8080`, not the host loopback address.

The root Compose file already supplies that internal URL and waits for the backend readiness check:

```bash
docker compose up --build --wait investigation-web
```

The UI is then available at `http://localhost:3002/cases/<case_id>` by default. Override the host
port with `INVESTIGATION_WEB_PORT` in the root `.env`; the container continues to listen on port
`3000`.

## Troubleshooting

- If startup reports a missing `INVESTIGATION_AGENT_URL`, set an absolute HTTP(S) URL in
  `.env.local` or the container environment.
- If history loads but invocation never progresses, inspect every proxy between the browser and
  FastAPI for response buffering.
- If the UI reports an uncertain result, refresh history before retrying. The backend may have
  committed after the browser disconnected.
- A `409 thread_case_conflict` means the route case does not match the immutable case bound to the
  thread. Reopen the thread from its sidebar summary.
- Thread deletion requires the separately delivered backend
  `DELETE /v1/threads/{thread_id}` endpoint.
