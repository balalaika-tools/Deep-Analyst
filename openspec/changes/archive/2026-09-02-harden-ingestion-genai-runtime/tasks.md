## 1. Configuration and Regression Contracts

- [x] 1.1 Add agent regression tests that exercise a model profile with native structured output
  and no `tool_choice` support, and verify both extraction agents return their typed schemas
  without constructing a tool-selection request.
- [x] 1.2 Add settings and environment-contract tests for one ingestion trace endpoint, absence of
  a second GenAI-provider endpoint, removal of `EMBEDDING_BATCH_SIZE`, and unchanged validation of
  positive physical request limits; verify the focused configuration suite fails before
  implementation.
- [x] 1.3 Add observability-library tests for one provider and one representative mixed span tree;
  verify the root, GenAI spans, and required ancestors carry projection classification, operational
  siblings do not, all spans share one trace ID, and the provider flushes and shuts down once.

## 2. Observability Foundations

- [x] 2.1 Keep one global trace provider and route all ingestion spans through the existing
  GenAI-capable trace receiver while leaving metric and log destinations unchanged; verify the
  ownership/lifecycle tests from task 1.3 pass and no span is exported twice.
- [x] 2.2 Add centralized `app.telemetry.category="genai"` classification for the ingestion root,
  GenAI boundaries, and required logical ancestors; verify tests enforce ancestor closure and the
  existing bounded error contract without span events.
- [x] 2.3 Add a Collector Langfuse projection filter and preserve the complete Tempo branch with
  GenAI payload deletion; verify a Collector contract fixture keeps identical trace/span IDs,
  emits no orphaned Langfuse child, excludes operational siblings, and strips captured content
  only from Tempo.
- [x] 2.4 Change OTLP log serialization so large rendered traceback detail is carried in the log
  body and not unbounded structured metadata; verify a synthetic traceback larger than 64 KiB
  produces exactly one record with native trace/span context, bounded searchable attributes, and
  environment-appropriate detail.

## 3. Native Structured Extraction and Correlation

- [x] 3.1 Replace explicit tool strategies in both extraction agents with their raw Pydantic
  response schemas, retaining transient retry middleware; verify unit tests cover typed entity and
  relationship responses, validation failures, and one callback model span per retry attempt.
- [x] 3.2 Propagate the ingestion ledger run ID as `app.workflow.run.id` on the root and every
  classified span and as `workflow_run_id` on searchable boundary logs; verify tests show direct
  trace/log lookup and prove the ID is absent from every metric attribute set.
- [x] 3.3 Represent each prose chunk's sequenced entity and relationship work as an
  `invoke_workflow extract_chunk` span inside the ingestion trace; verify the exported hierarchy
  contains both `invoke_agent` children and their physical `chat` attempts, shares the root trace
  ID, and is retained as a connected subtree in the Langfuse projection.

## 4. Record-Grouped Embeddings and Physical Request Limits

- [x] 4.1 Preserve record and chunk identity through the text-embedding port, group chunks by
  source record, and reassemble vectors in original global order; verify multi-record unit tests
  cover multiple chunks per record, concurrent completion in a different order, and unchanged
  persistence ordering.
- [x] 4.2 Create one `invoke_workflow indexing_embeddings` span per source record inside the
  ingestion trace and one `embeddings` client span around each chunk's `aembed_query()` call;
  verify workflows carry run ID, record ID, source system, and input count, children carry chunk
  ID/index and offsets, all share the root trace ID, and no raw text appears when capture is off.
- [x] 4.3 Move embedding rate-limit and semaphore acquisition to each individual model operation,
  remove list-level `aembed_documents()` throttling, and verify concurrent fake calls never exceed
  `LLM_MAX_IN_FLIGHT`, consume one rate token per chunk, wait rather than fail, and preserve error
  translation.
- [x] 4.4 Pass a derived botocore `Config(max_pool_connections=LLM_MAX_IN_FLIGHT)` to both chat and
  embedding clients, and verify construction tests inspect the effective pool capacity while the
  shared semaphore remains the only concurrency control.
- [x] 4.5 Remove `EMBEDDING_BATCH_SIZE` from settings, YAML, `.env.example`, fixtures, and tests;
  retain `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` as the single ingestion trace override and do not add
  `GENAI_OTLP_TRACES_ENDPOINT`, then verify settings precedence and environment-example parity
  tests pass.

## 5. Integration Verification and Documentation

- [x] 5.1 Run the scoped ingestion and observability unit/contract suites plus repository Ruff and
  mypy checks, and verify all commands complete successfully with no new warnings or skipped
  required tests.
- [x] 5.2 Update local documentation with the single-endpoint contract, dual backend views, direct
  trace-ID and retained-span correlation, `workflow_run_id` retry/business search, content policy,
  and non-destructive Compose workflow; verify every documented command and environment name
  matches the resolved Compose and settings contracts.
- [x] 5.3 Validate Compose, start the local dependencies, and run ingestion with configured
  Bedrock credentials and AI content capture disabled; verify the run completes successfully and
  its receipt is written.
- [x] 5.4 Verify with content capture off that Tempo contains the complete ingestion tree,
  Langfuse contains the connected root plus only required ancestors and GenAI subtrees, both views
  use the same trace ID and retained span IDs, Loki contains searchable correlated boundary logs
  including a large failure case, and Prometheus contains bounded GenAI/application metrics with
  no run or trace identifier label.
- [x] 5.5 Run a canary ingestion with content capture enabled and verify approved GenAI input/output
  appears in Langfuse while the canary payload is absent from Tempo and Loki and common secret
  redaction remains active in both trace branches.
- [x] 5.6 Run strict OpenSpec validation for `harden-ingestion-genai-runtime` after implementation
  evidence is recorded, and verify the change remains ready for archive without modifying stored
  evidence or deleting Compose volumes.

## Implementation Evidence — 2026-09-02

- Hermetic repository suite: `144 passed, 10 deselected`; the deselected tests are the explicitly
  excluded integration/live markers. Repository Ruff lint and format checks passed, and strict
  mypy passed for 160 source files.
- Compose interpolation and configuration validation passed. The pinned LGTM container loaded the
  Collector configuration and remained healthy; its Prometheus self-metrics reported successful
  Tempo and Langfuse span exports with no failed-span series.
- Capture-off ingestion trace `79826d159632dd1a04b078b10e99cf54` completed with workflow run
  `e669cfd7b7f24d29983e46ef667d08ea`: Tempo retained all 309 spans, Langfuse retained exactly the
  303 classified spans, every retained trace/span identity matched, no orphan or operational-only
  observation remained, all classified spans carried the workflow run ID, and neither backend
  contained captured content.
- Loki retained the correlated ingestion boundary logs. A separate canary delivered exactly one
  77,895-byte failure body with bounded `event.name` and `error.type` attributes plus native trace
  and span context. Ingestion and GenAI Prometheus series contained no run, record, chunk, trace,
  or span identifier label.
- Capture-enabled ingestion trace `b959dbcf38e57d6fd9894726d044d464` completed with workflow run
  `5450d0cc54cf4ed9a65c3f65c2e9d5d2`: Langfuse retained approved input and output on 66 chat
  observations; Tempo retained zero content attributes; the nine correlated Loki records
  contained zero payload occurrences; common-secret canaries were absent from both trace views;
  and every one of the 303 retained observation IDs still matched Tempo.
- The exact `indexes/en/receipt.json` object was removed only to force each required live canary
  and was recreated by the successful run. The final receipt and completed ledger row were
  verified. No Compose volume, stored trace, log, metric, or Langfuse object was deleted.
