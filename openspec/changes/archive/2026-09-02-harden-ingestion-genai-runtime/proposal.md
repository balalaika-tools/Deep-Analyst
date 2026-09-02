## Why

The ingestion service currently fails before reaching Bedrock because its explicit tool-based
structured-output strategy asks the configured Terra model for unsupported `tool_choice`
behavior. Its telemetry also sends the complete operational span tree to Langfuse, where source,
database, and persistence spans obscure the GenAI work, while Tempo still needs that complete
tree and must not retain captured prompts or model outputs.

## What Changes

- Use the configured model's native structured-output capability for entity and relationship
  extraction while retaining schema validation and bounded retry behavior.
- Emit one bounded ingestion trace from the `run ingestion` root through every operational and
  GenAI child, then let the Collector produce two destination-specific views without changing
  the trace or retained span identities.
- Send the complete trace to Tempo after removing heavy GenAI input/output attributes, and send
  an ancestor-closed projection to Langfuse containing the root, GenAI workflows, agents, model,
  embedding, retrieval, and tool spans, plus only the minimal ancestors needed to preserve the
  hierarchy.
- Nest both extraction agents and their physical chat attempts under each chunk workflow, and
  nest one physical Titan embedding span per chunk under its record-level embedding workflow.
- Correlate Tempo, Langfuse, and structured logs directly with the shared trace ID; retain
  `app.workflow.run.id`/`workflow_run_id` for business-run search and retry grouping, and retain
  record/chunk identifiers only on the scopes where they apply.
- Redefine `LLM_MAX_IN_FLIGHT` as the process-wide limit on physical Bedrock requests, charge the
  rate limiter once per physical request, and size each boto connection pool to at least that
  concurrency.
- **BREAKING**: Remove `EMBEDDING_BATCH_SIZE`; the configured Titan integration accepts one text
  per request, so the setting does not represent provider-side batching.
- Preserve opt-in AI content capture in Langfuse while ensuring captured GenAI payloads never
  reach Tempo or logs; retain bounded error attributes, one owning exception log, and
  backend-isolated telemetry export failures.
- Keep large local failure detail out of OTLP structured metadata so Loki retains the single
  correlated `ingestion.run_failed` record instead of rejecting it at its metadata-size limit.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingestion-pipeline`: Correct structured extraction, physical Bedrock request limiting and
  connection reuse, single-trace multi-backend observability, GenAI workflow projection, and
  trace/log correlation requirements.

## Impact

The change affects the ingestion application's chunk-indexing orchestration, embedding and
extraction adapters, shared model throttle, Bedrock client construction, span classification,
Collector trace pipelines, settings/YAML/environment documentation, focused unit and contract
tests, and local Docker Compose end-to-end verification in Tempo, Loki, Prometheus, and Langfuse.
It does not change evidence schemas, extraction ontology, stored chunk ordering, or the existing
local backend deployment topology.
