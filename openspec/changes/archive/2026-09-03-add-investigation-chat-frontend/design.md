## Context

See `proposal.md` for motivation and `specs/investigation-chat-ui/spec.md` for observable behavior.
The repository currently has no JavaScript workspace or frontend. The investigation agent exposes
paginated thread/history reads and a POST endpoint that responds either with versioned problem
details before streaming or with versioned SSE envelopes after acceptance. Because the invocation
uses POST, the browser's native `EventSource` API cannot consume it.

The backend ownership-removal work will make public endpoints unauthenticated while preserving the
first-turn `thread_id` to `case_id` binding. Thread deletion is a separately delivered backend
contract. Thread summaries do not contain a conversational title or message preview, so the UI
must not create an N+1 history-read pattern merely to label the sidebar.

## Goals / Non-Goals

**Goals:**

- Keep the UI small, understandable, responsive, and fully usable from the keyboard.
- Preserve the backend's idempotency and durability semantics across retries, cancellation, route
  changes, malformed streams, and uncertain disconnects.
- Keep protocol parsing and state transitions deterministic and independently testable.
- Avoid browser CORS configuration and prevent accidental response buffering.
- Make the frontend a cohesive deployable without restructuring the Python uv workspace.

**Non-Goals:**

- Authentication, case authorization, user accounts, or an in-product case picker.
- Rich-text editing, attachments, voice, agent configuration, thread rename, search, or branching.
- Rendering private graph state, raw tool activity, hidden reasoning, or raw model-token streams.
- Designing or implementing the backend delete endpoint or ownership-removal change.

## Decisions

### 1. Add a standalone Next.js 16 application under `services/investigation_web`

The frontend will use the App Router, React 19, strict TypeScript, and a service-local `package.json`
and lockfile configuration. It will not be added to the Python uv workspace. The application layer
owns routes and composition; cohesive `features/conversations` and `features/threads` modules own
their API contracts, state, components, and colocated tests. Truly generic presentation primitives
remain in a small `components` layer.

This preserves the repository's deployable-under-`services` convention while keeping frontend
dependencies isolated. A root JavaScript monorepo was rejected because there is only one JavaScript
package and no shared package that justifies workspace machinery.

### 2. Make case and thread identity explicit in App Router paths

The primary routes will be:

```text
/cases/[caseId]                         fresh conversation
/cases/[caseId]/threads/[threadId]      persisted or active conversation
```

The case route supplies the only case context for a fresh conversation. Selecting a summary uses
both its returned `case_id` and `thread_id`, preventing a thread from being opened under stale case
context. Creating a conversation produces a client UUID in memory; navigation to the thread route
occurs with the first submission, but the sidebar treats it as provisional until the invocation is
accepted or a subsequent history refresh finds it.

A query-string-only design was rejected because case/thread identity is primary navigation state,
not a transient filter. A case picker was rejected as additional product scope.

### 3. Put a thin streaming-preserving BFF at the Next.js boundary

Next.js Route Handlers will proxy the four browser operations to a server-only
`INVESTIGATION_AGENT_URL`:

```text
GET    /api/investigation/threads
GET    /api/investigation/threads/[threadId]/messages
POST   /api/investigation/invoke
DELETE /api/investigation/threads/[threadId]
```

The proxy sends no authorization or ownership metadata. Query strings, status codes, content type,
problem bodies, retry guidance, and cache-control headers are preserved. The invocation handler
passes the upstream `ReadableStream` directly into the downstream `Response`, propagates request
cancellation through `AbortSignal`, runs on the Node.js runtime, and opts out of caching. Deployment
configuration must also disable proxy buffering.

Direct browser-to-FastAPI calls were rejected because they require CORS configuration and expose
environment-specific backend routing. Server Actions were rejected for invocation because the
client must consume the response stream as a protocol, not wait for a mutation result.

### 4. Separate protocol parsing from conversation state

The SSE client will use `fetch` and a small incremental parser built on `TextDecoder` streaming.
The parser handles CRLF/LF separators, fields split across arbitrary byte chunks, multi-line `data`
fields, blank-line dispatch, and comment heartbeats. It produces protocol events but performs no UI
updates. Runtime type guards validate the envelope discriminator, `schema_version`, identifiers,
timestamp, and bounded event data before an event enters application state.

The parser rejects unknown versions/events, mismatched identities, deltas that do not start at zero
or skip/repeat an index, events before `run.started`, and events after a terminal event. EOF without
a terminal event is an uncertain delivery result, not success. This explicit parser was chosen over
`EventSource` because `EventSource` cannot POST, and over a general SSE dependency because the
required subset is small and deserves direct contract tests.

### 5. Model an active turn as a reducer-driven state machine

One feature-scoped reducer owns the active turn:

```text
idle
  -> submitting
  -> streaming_progress
  -> streaming_answer
  -> completed
  -> failed
  -> uncertain
  -> cancelled
```

Actions carry validated protocol events rather than arbitrary response objects. The reducer retains
the exact submission payload and request ID until the outcome is reconciled, making same-request
retry deliberate. A new user submission creates a new request ID. `AbortController` is scoped to
one attempt and is aborted during cancellation, route change, or component cleanup.

The submitted user message is shown immediately with a pending marker. Streamed assistant text is
provisional presentation of an already committed backend message and becomes final only on
`run.completed`. Completion triggers a bounded history/thread refresh to reconcile message IDs,
citations, status, and sidebar order. Pre-stream and streaming errors remain attached to the
attempt; they never synthesize assistant content.

Several independent `useState` values were rejected because invalid combinations such as an
enabled composer beside an active stream would be easy to create.

### 6. Use server-rendered initial data and client-managed interaction

Route pages fetch the initial thread page and, for persisted routes, the initial message page on the
server in parallel where possible. Serializable DTOs seed a narrow client workspace. Client fetches
then own load-more, deletion, refresh, and streaming. Reads use `no-store`, since checkpoint history
and statuses are operational state and must not be served stale by the Next.js cache.

Opaque cursors are passed through verbatim. Collections deduplicate by `thread_id` or `message_id`,
and messages sort by numeric sequence after pagination merges. The sidebar labels entries with
case, creation time, and status; it does not fetch every transcript to manufacture titles.

A global server-state library was rejected because the app has two small paginated resources and
one active streaming state machine; native fetch plus feature-scoped reducers is sufficient.

### 7. Keep deletion confirmed and conservative

Each persisted summary has an accessible menu/delete action. Confirmation names the thread context.
Only a successful backend response removes it locally. A failed response leaves the list intact.
Deleting the active thread navigates to `/cases/[caseId]`; deleting another thread preserves the
current route. The action is disabled for the currently streaming thread to avoid an ambiguous
race between checkpoint writes and deletion.

Optimistic removal was rejected because a failed destructive request would make durable history
temporarily disappear from the UI.

### 8. Use a restrained accessible visual system

The layout uses semantic regions, visible focus states, labelled icon buttons, at least 44px touch
targets on compact screens, adequate contrast, reduced-motion support, and a live status region for
coarse progress that does not repeatedly announce answer chunks. The composer is a labelled
multiline control with a clear send/cancel action. A mobile drawer contains the sidebar and returns
focus to its trigger when closed.

Styling will use locally defined CSS design tokens and CSS Modules/global CSS rather than adopting
a component framework for this small surface. This limits dependencies while allowing a coherent
light/dark-aware interface.

### 9. Test protocol invariants more heavily than presentation details

Vitest will cover runtime validation, arbitrary byte/chunk boundaries, UTF-8, comments, multiline
data, ordered deltas, duplicate terminals, premature EOF, reducer transitions, cursor merging, and
same-request retry. Component tests will cover keyboard submission, cancellation, confirmation,
busy states, and accessible names. A small Playwright suite against a production build and a
deterministic fixture upstream will cover opening history, successful progressive streaming,
retryable failure, deletion, navigation, and the mobile sidebar using role-based locators and
observable states rather than hard waits.

Large snapshots and exhaustive cosmetic assertions were rejected because they add maintenance cost
without improving confidence in the streaming contract.

## Risks / Trade-offs

- **[A proxy or hosting platform buffers SSE]** -> Stream the upstream body unchanged, set no-store
  and no-buffer headers, document the deployment requirement, and include a progressive-arrival
  integration test.
- **[The backend delete contract lands with different response semantics]** -> Centralize deletion
  in one typed API function and accept any successful 2xx response without depending on a body.
- **[The stream disconnects after the backend committed]** -> Treat EOF as uncertain, reload
  persisted history, and retain the original request payload for explicit idempotent retry.
- **[Thread summaries have weak human-readable labels]** -> Show case, date, and status now; add a
  backend-provided title/preview in a future contract instead of creating N+1 reads.
- **[Listing across all cases becomes large]** -> Preserve backend cursor pagination; a future
  backend case filter can be added without changing the workspace interaction model.
- **[No auth makes the deployment unsafe outside a trusted prototype environment]** -> Keep the
  client free of identity assumptions so authentication and case authorization can later be added
  at the FastAPI boundary without changing agent semantics.

## Migration Plan

1. Land the backend ownership-removal change and `DELETE /v1/threads/{thread_id}` contract.
2. Add the isolated frontend service, runtime configuration, and deterministic tests.
3. Run contract checks against the FastAPI service and verify progressive delivery through the
   actual local reverse-proxy path.
4. Add the frontend service to local composition only after health, history, invoke, and delete are
   available.
5. Roll back by removing the frontend deployment; the backend contract and persisted conversations
   remain unchanged.
