## 1. Reconcile Contracts and Inventory

- [x] 1.1 Reconcile or supersede the active `add-investigation-chat-frontend` change so its case-scoped routes and request requirements cannot be applied alongside this change; verify `openspec validate --strict` succeeds for both the resulting active change set and this change.
- [x] 1.2 Inventory every active `case_id`, case-scoped, cross-case, and `/cases/` occurrence across source, generated data, schemas, tests, configuration, and documentation; record intentional historical-archive exclusions and verify the inventory covers all workspace packages.
- [x] 1.3 Define and document the global deterministic identity formulas for records, versions, entities, relationships, mentions, chunks, projections, accounts, communications, and transactions; verify collision fixtures cover identical source-local IDs from different source systems.

## 2. Remove Case Identity from the Synthetic Dataset

- [x] 2.1 Update dataset source builders and raw serializers to omit the removed field from bank SQL, CDR, extractions, emails, document front matter, and manifests; verify dataset unit and structure tests reject any emitted case field.
- [x] 2.2 Replace case-qualified raw primary keys, foreign keys, and stable ID builders with the approved global source-qualified formulas; verify repeated generation remains byte-identical and collision tests pass.
- [x] 2.3 Update ground-truth builders, provenance DAGs, source references, expected previews, quarantine fixtures, rubrics, validation rules, and golden assertions to use global identities; verify all dataset contract and validation suites pass.
- [x] 2.4 Regenerate both English and Greek checked-in editions and verify exact inventory counts, bilingual stable-ID equivalence, integrity manifests, and the absence of the removed field in generated artifacts.

## 3. Remove Case Identity from Evidence Models and Ingestion

- [x] 3.1 Remove the field from shared evidence-model contracts, canonical envelopes, entities, relationships, mentions, resolutions, transaction projections, and source references; verify serialization and ontology tests pass without compatibility aliases.
- [x] 3.2 Update ingestion parsers, normalizers, identity resolution, edge generation, chunking, projections, provenance, and run accounting to consume the field-free dataset; verify focused unit tests preserve exact amounts, timestamps, locators, and relationship status.
- [x] 3.3 Replace case-qualified ingestion upserts and natural keys with global keys and update database indexes and foreign keys; verify two identical ingestion runs are idempotent and source-local collision fixtures remain distinct.
- [x] 3.4 Remove case scope from ingestion configuration, CLI/runtime arguments, object-store receipts, telemetry attributes, and Compose wiring; verify startup, readiness, one-shot ingestion, and receipt/no-op integration tests pass.

## 4. Convert the Evidence Store to a Global Corpus

- [x] 4.1 Update owner initialization and schema validation to create field-free source tables and projections with global constraints and indexes; verify schema contract tests assert the new columns, keys, privileges, and index definitions.
- [x] 4.2 Rebuild `agent_read` views without session-variable predicates while preserving allowlisted columns and the read-only reader role; verify an approved reader query sees all matching rows and mutation/temp-object attempts still fail.
- [x] 4.3 Remove the session setting and scope parameter from guarded SQL execution while preserving AST validation, bound parameters, transactions, timeouts, cancellation, and row/byte limits; verify SQL policy and executor tests cover global reads and forbidden mutations.
- [x] 4.4 Update canonical record and provenance resolution to validate each returned row by global record identity, content hash, and locator; verify forged, missing, or mismatched provenance is rejected independently of any conversation state.

## 5. Make Every Investigation Tool Global

- [x] 5.1 Remove scope fields and checks from search, structured-query, and graph tool input/output schemas and runtime invocation context; verify strict validation rejects obsolete model-authored scope fields.
- [x] 5.2 Remove partition predicates from BM25 and vector retrieval while preserving source/time filters, exclusions, fusion, deduplication, attempt limits, and top-k bounds; verify the Aegean narrative and transaction chunks are retrievable from a fresh invocation.
- [x] 5.3 Remove same-partition checks from entity loading, relationship loading, record resolution, and deterministic graph traversal; verify a fully sourced global path is returned and unresolved or unbounded paths remain rejected.
- [x] 5.4 Replace case-based evidence-card and tool-outcome validation with record-local provenance validation; verify one answer can combine valid evidence from differently sourced records without weakening citation checks.

## 6. Make Agent State, History, and API Thread-Only

- [x] 6.1 Remove the field from control state, current-turn input, evidence cards, projection validation, request fingerprints, graph configuration, checkpoint metadata, and observability attributes; verify state/domain tests contain no case binding and preserve policy immutability.
- [x] 6.2 Bump the application checkpoint schema and define obsolete checkpoint handling consistent with the approved local rebuild; verify an old checkpoint fails safely before agent or evidence work and a new checkpoint serializes no removed field.
- [x] 6.3 Change invocation requests to accept only request ID, thread ID, and message, and update SSE, replay, conflict, history, list, and deletion contracts accordingly; verify API contract tests strictly reject the obsolete request property and expose no case field in responses.
- [x] 6.4 Update history reading, pagination, thread summaries, resumption, idempotency, interruption, and per-thread locking to use thread identity alone; verify separate threads have independent histories while both can retrieve the same global evidence.
- [x] 6.5 Update agent prompts and guardrails to describe global evidence selection without case terminology or hidden scope; verify prompt snapshots and injection tests retain tool, grounding, and safety constraints.

## 7. Replace the Case Frontend with Conversation Navigation

- [x] 7.1 Remove the case launcher, case-ID input/help text, case headings, case-aware thread grouping, and all related component tests; verify the application root shows `New conversation` and recent conversations with no scope input.
- [x] 7.2 Replace `/cases/[caseId]` and `/cases/[caseId]/threads/[threadId]` with new-conversation and `/threads/[threadId]` routes; verify legacy case URLs redirect to the conversation home and never generate an API scope value.
- [x] 7.3 Update frontend contracts, API client, workspace state, submission payloads, navigation, deletion copy, and recent-thread rendering to use only request and thread identities; verify TypeScript, lint, component, and contract tests pass.
- [x] 7.4 Add browser coverage proving `New conversation` starts an empty transcript, two conversations remain independent, a recent conversation restores its own history, and both can query the shared evidence corpus; verify the Playwright suite passes.

## 8. Documentation, Regression, and Local Rebuild

- [x] 8.1 Update active OpenSpec main specs, README files, wiki architecture/data-layer documentation, examples, and curl commands to describe a global evidence store with thread-only conversations; verify active documentation contains no obsolete field or `/cases/` route.
- [x] 8.2 Add an end-to-end regression asking whether Aegean made three booked consecutive-business-day transfers totaling EUR 29,000; verify the answer cites `t_85`, `t_86`, and `t_88` and reports EUR 29,000.
- [x] 8.3 Run all dataset, evidence-model, ingestion, investigation-agent, frontend, type, lint, contract, integration, and end-to-end checks; verify every configured project check succeeds before any local state reset.
- [ ] 8.4 Enumerate the exact local evidence and checkpoint state requiring replacement, offer a backup, and obtain explicit user approval before any destructive reset; verify no delete or volume-recreation command runs without that approval.
- [ ] 8.5 After approval, rebuild the local database and object-store evidence from the field-free editions, rerun ingestion, and verify live table schemas, generated artifacts, checkpoints, API payloads, UI routes, and Langfuse metadata contain no removed field.
- [ ] 8.6 Run a final repository-wide search excluding immutable archived change history and verify no active source, schema, generated artifact, test, configuration, or documentation contains `case_id`, case-scoped behavior, or supported `/cases/` navigation.
