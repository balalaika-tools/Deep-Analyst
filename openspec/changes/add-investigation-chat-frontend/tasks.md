## 1. Frontend Service Foundation

- [x] 1.1 Scaffold `services/investigation_web` as a standalone Next.js 16 App Router application
  with React 19, strict TypeScript, service-local package scripts, and pinned runtime requirements;
  verify a clean install and production build complete successfully.
- [x] 1.2 Add the feature-oriented application structure, import aliases, lint/type-check settings,
  Vitest setup, and Playwright configuration; verify lint, type-check, and an empty smoke test run
  from the service directory.
- [x] 1.3 Add validated server-only `INVESTIGATION_AGENT_URL` configuration plus documented local
  environment examples; verify startup fails with a clear configuration error when it is absent or
  invalid and never exposes the value in the client bundle.

## 2. Backend Contract Boundary

- [x] 2.1 Define strict TypeScript DTOs and runtime guards for thread pages, message pages,
  citations, problem details, and every SSE envelope variant; verify fixture tests accept current
  backend examples and reject unknown versions, fields, enum values, and malformed identifiers.
- [x] 2.2 Implement no-store server fetch functions for thread and message pagination that pass
  opaque cursors unchanged and send no auth or ownership data; verify focused tests cover success,
  problem responses, and invalid upstream payloads.
- [x] 2.3 Implement Next.js Route Handlers for thread and message reads plus thread deletion,
  preserving upstream 2xx/error status, safe body, relevant headers, and query parameters; verify
  route tests cover all methods and confirm deletion does not depend on a response body.
- [x] 2.4 Implement the invocation Route Handler as an unbuffered `ReadableStream` pass-through
  with request-signal propagation and no-store/no-buffer headers; verify an integration test
  observes the first upstream event before the upstream response closes.

## 3. Streaming Protocol

- [x] 3.1 Implement the incremental SSE decoder for LF/CRLF framing, split UTF-8 bytes, split
  fields, multiline data, blank-line dispatch, and comment heartbeats; verify Vitest cases cover
  every boundary and do not emit heartbeats as application events.
- [x] 3.2 Implement the invocation protocol validator for start ordering, matching thread/turn
  identity, contiguous zero-based answer indexes, one terminal event, and supported schema/event
  variants; verify tests reject skips, repeats, unknown events, mismatches, post-terminal events,
  and premature EOF.
- [x] 3.3 Implement the POST streaming client with distinct handling for SSE, versioned problem
  responses, aborts, network errors, and unsupported content types; verify tests prove a
  non-streaming problem is never passed to the SSE decoder.

## 4. Conversation State and Recovery

- [x] 4.1 Implement the reducer-driven active-turn state machine and retain the exact request
  payload through retry/reconciliation; verify transition tests cover success, safe progress,
  retryable and non-retryable failures, uncertain EOF, cancellation, reset, and invalid actions.
- [x] 4.2 Implement client-generated thread/request IDs, pending user messages, provisional
  assistant deltas, and same-request retry behavior; verify retries preserve every original payload
  field while deliberate new turns generate a new request ID.
- [x] 4.3 Implement abort cleanup for cancellation, route changes, and unmount plus history/thread
  reconciliation after completion or uncertain delivery; verify tests prove one active request is
  aborted exactly once and no automatic replacement turn is submitted.
- [x] 4.4 Implement cursor-page merge helpers that deduplicate thread/message IDs and preserve
  server thread order and ascending message sequence; verify deterministic unit tests cover
  overlapping pages and repeated fetch results.

## 5. Workspace and Transcript UI

- [x] 5.1 Implement `/cases/[caseId]` and `/cases/[caseId]/threads/[threadId]` Server Component
  routes with validated asynchronous params and parallel initial reads where applicable; verify
  fresh, existing, invalid, and missing-thread routes render their intended states.
- [x] 5.2 Build the responsive two-region workspace, desktop sidebar, mobile navigation drawer,
  empty state, and visible focus/focus-return behavior using local design tokens; verify keyboard
  component tests and mobile/desktop viewport checks cover all primary navigation.
- [x] 5.3 Build message rendering for user/assistant roles, pending and turn statuses, safe progress,
  streamed text, timestamps, failures, and accessible citation metadata; verify interrupted/failed
  turns never render fabricated assistant content.
- [x] 5.4 Build the labelled multiline composer with blank-input prevention, keyboard submit,
  send/cancel switching, duplicate-submit prevention, and retry controls; verify component tests
  cover keyboard and pointer interaction throughout the turn lifecycle.
- [x] 5.5 Add restrained responsive styling, light/dark-aware semantic colors, adequate contrast,
  44px compact touch targets, readable message width, and reduced-motion behavior; verify the
  production page has no horizontal overflow at supported mobile and desktop viewports.

## 6. Thread Navigation and Deletion

- [x] 6.1 Build the paginated thread sidebar using case, timestamp, and status labels without
  per-thread history requests; verify load-more appends unique summaries and no N+1 message reads
  occur.
- [x] 6.2 Implement new-conversation and thread-selection navigation so fresh conversations retain
  the current case while persisted summaries navigate with their returned case/thread IDs; verify
  route tests cover same-case and cross-case selection.
- [x] 6.3 Implement an accessible delete confirmation flow that removes a summary only after a
  successful backend response, preserves it on failure, redirects after deleting the active
  thread, and disables deletion during its active stream; verify each path with component tests.

## 7. End-to-End Verification and Delivery

- [x] 7.1 Add a deterministic fixture upstream for browser tests that can emit paginated history,
  incremental SSE, retryable failures, malformed termination, and delete outcomes; verify fixture
  state is isolated between Playwright tests.
- [x] 7.2 Add Playwright flows for new chat streaming, opening paginated history, explicit retry,
  cancellation/reconciliation, confirmed deletion, delete failure, cross-case navigation, and the
  mobile sidebar; verify the suite runs against a production build with role-based locators and no
  hard waits.
- [x] 7.3 Run lint, strict type-check, Vitest, Playwright, and production build together and resolve
  all failures; verify the complete frontend quality command exits successfully.
- [ ] 7.4 Document local startup, required backend prerequisites, routes, no-auth behavior,
  unbuffered proxy requirements, and troubleshooting for premature SSE termination; verify a clean
  local setup can open history and receive progressive events from the real FastAPI service.
- [ ] 7.5 Add the frontend to the repository's local composition/deployment boundary only after the
  ownership-removal and delete contracts are available; verify health checks pass and the browser
  reaches the backend exclusively through the Next.js proxy.
  - Compose wiring is present; runtime health and real-backend proxy verification remain pending
    until the backend implementation is ready.
