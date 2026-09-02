## Context

See `proposal.md` for motivation and `specs/ingestion-pipeline/spec.md` for the behavioral
contract. The ingestion process already owns one OpenTelemetry `TracerProvider`, configured with
the GenAI-capable receiver, so the operational `run ingestion` root and all of its children reach
both Tempo and Langfuse. That preserves causal structure and identity, but Langfuse also receives
source loading, database, HTTP-client, and persistence noise. The existing Tempo branch removes
captured GenAI payload attributes while the Langfuse branch preserves them; neither branch yet
creates the focused, connected Langfuse view required by this change.

The application still holds `(SourceRecord, Chunk)` pairs when indexing begins, but flattens
them into one text list before calling the embedder. On the installed non-Cohere path,
`BedrockEmbeddings.aembed_documents()` fans that list out to one `aembed_query()` per text;
both embedding and chat async methods execute synchronous boto calls in the default thread
executor. The current semaphore therefore reserves one slot for an embedding list while that
list can create many simultaneous HTTP requests. Neither Bedrock client currently configures
botocore connection-pool capacity, whose default is smaller than the configured concurrency.

The ingestion ledger creates a stable run ID before model work begins. It remains useful for
business-run search and retry grouping, while the OpenTelemetry trace ID is the direct correlation
key for the two backend views and every log emitted during the bounded run.

## Goals / Non-Goals

**Goals:**

- Make native structured extraction work with the configured Terra Bedrock model.
- Preserve one trace identity from the ingestion entry boundary through operational and GenAI
  work, with a complete redacted Tempo view and a connected GenAI-focused Langfuse view.
- Group embeddings by source record/document without hiding individual model operations.
- Make the configured in-flight limit a safe upper bound on simultaneous Bedrock client calls
  and provision reusable HTTP connections for that bound.
- Preserve deterministic vector/result ordering, failure semantics, destination-specific content
  policy, and one telemetry lifecycle owner.

**Non-Goals:**

- Changing the evidence schema, chunking algorithm, ontology, prompts, or candidate validation.
- Replacing boto3/LangChain AWS with an unofficial async AWS client.
- Introducing provider-side embedding batches where the configured Titan API accepts one text.
- Sending logs to Langfuse, copying trace IDs into metric labels, or duplicating spans with a
  Langfuse-specific callback.
- Changing the deployed Tempo, Loki, Prometheus, or Langfuse components; this change modifies
  only the existing Collector's trace processing and routing policy.
- Defining production sampling, retention, or capacity policy from local fixture traffic.
- Treating an unbounded worker process lifetime, durable handoff, or multi-turn conversation as
  one trace; the root here is one finite ingestion run.

## Decisions

### Let LangChain select native structured output from the raw schema

Both agents will pass their Pydantic schema directly as `response_format`. With the installed
model profile advertising native structured output, LangChain's automatic strategy resolves to
the provider strategy instead of constructing a synthetic tool and forcing `tool_choice`. The
entity and relationship agents remain tool-free and keep the existing transient-error retry
middleware; the model callback remains below that middleware so separate application-level retry
attempts produce separate chat spans.

Keeping explicit `ToolStrategy` was rejected because Terra rejects the resulting tool-selection
request before inference. Binding a hand-written JSON parser was rejected because it would
duplicate provider schema enforcement and weaken the typed response contract. A live-marked
compatibility test will verify the installed LangChain/model-profile path; default tests continue
to use deterministic fakes.

### Own the bounded run with one tracer provider and one trace export path

The shared observability lifecycle will retain exactly one global `TracerProvider`. Every
ingestion, workflow, agent, chat, embedding, and operational span uses that provider and normal
OpenTelemetry parenting. In local Compose, the ingestion trace exporter targets the existing
GenAI-capable OTLP/HTTP receiver on port 4328; metrics and logs retain their existing operational
OTLP paths. `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` remains the single trace endpoint override and no
`GENAI_OTLP_TRACES_ENDPOINT` setting is introduced.

The Collector fans each received trace into two pipelines:

| Pipeline | Span set | GenAI content | Destination |
| --- | --- | --- | --- |
| Tempo | complete ingestion trace | removed | Tempo |
| Langfuse | root plus ancestor-closed GenAI projection | preserved when capture is enabled | Langfuse |

Fan-out does not rewrite trace IDs, span IDs, parent IDs, timestamps, or status. Consequently,
both backends represent the same ingestion operation rather than correlated but independent
traces. The provider is flushed and shut down once, and exporter failures remain isolated from
the ingestion outcome.

Two providers with detached roots were rejected because they fragment one bounded operation,
require explicit context switching and Span Links, and make trace ID correlation impossible. An
application exporter per backend was rejected because it puts destination policy and credentials
in the service and risks duplicate export. Sending the unfiltered trace to both backends was
rejected because it recreates the current Langfuse noise.

### Build an ancestor-closed Langfuse projection

The application explicitly classifies every span that belongs to the GenAI view with the neutral
attribute `app.telemetry.category="genai"`. The classified set contains:

- the `run ingestion` root, required to establish the Langfuse trace;
- `invoke_workflow`, `invoke_agent`, `chat`, `embeddings`, `retrieval`, and `execute_tool` spans;
- any minimal business span between the root and one of those spans.

Operational-only siblings and descendants, including source loading, database access, outbound
HTTP details, and persistence, remain unclassified. The Langfuse Collector branch drops every
unclassified span. Classification MUST obey one invariant: if a span is retained, every parent
between it and the root is also retained. Dropping an unclassified leaf or complete subtree is
safe; retaining a classified descendant below an unclassified parent is a contract failure.

The marker is centralized in observability helpers rather than scattered as string literals.
Tests construct representative extraction, embedding, tool, database, and persistence trees and
assert both the marker invariant and the actual Collector output. Filtering only spans with a
`gen_ai.*` model attribute was rejected because it drops workflow, agent, tool, and root spans and
creates orphans. A trace-wide GenAI marker without span projection was rejected because it would
continue sending every operational span to Langfuse.

The ledger run ID is recorded as `app.workflow.run.id` on the root and every classified span, and
as `workflow_run_id` on the small set of structured boundary logs. Trace-level Langfuse fields are
likewise propagated to retained observations as required by its OTLP mapping. Record ID, stable
chunk ID, chunk index, and offsets are observation-level attributes on only the record/chunk
workflows and model calls to which they apply. Every log keeps the `trace_id` and `span_id` of its
actual current span; a separate causal trace field and Span Links are unnecessary inside this
single trace. No run, record, chunk, trace, or span identifier becomes a metric attribute.

### Use one extraction workflow subtree per prose chunk

The application represents each per-chunk extraction business unit directly as a classified
GenAI workflow inside the ingestion trace:

```text
run ingestion
└── invoke_workflow extract_chunk
    ├── invoke_agent entity_extraction
    │   └── chat terra
    └── invoke_agent relationship_extraction
        └── chat terra
```

The workflow span carries `gen_ai.operation.name=invoke_workflow`, a stable workflow name,
`app.workflow.run.id`, source system, record ID, chunk ID/index, and offsets. IDs remain
attributes rather than span names. Each retry creates another `chat` child under the same agent.
Candidate validation remains in the workflow's application execution but does not gain noisy
function-level spans. The root, workflow, agents, and chat attempts are present in both backends;
unrelated ingestion siblings remain Tempo-only.

One detached trace per chunk was rejected because it loses direct trace identity with the run and
requires secondary correlation. One span per agent without the workflow parent was rejected
because it loses the entity-then-relationship unit the application needs to understand one chunk.

### Use one embedding workflow subtree per source record and one model span per chunk

Chunk indexing will group the existing `(SourceRecord, Chunk)` sequence by record without losing
global order. Record groups may execute concurrently, but each opens one classified workflow span
inside the ingestion trace:

```text
run ingestion
└── index chunks
    └── invoke_workflow indexing_embeddings
        ├── embeddings amazon.titan-embed-text-v2:0  chunk index 0
        ├── embeddings amazon.titan-embed-text-v2:0  chunk index 1
        └── embeddings amazon.titan-embed-text-v2:0  chunk index 2
```

The workflow carries the run ID, record ID, source system, and `app.embedding.input_count`. Every
child is a `CLIENT` span around one `aembed_query()` and carries model/provider identity plus its
chunk ID/index and offsets. The application reassembles vectors by original position before
persistence, so concurrency cannot change stored ordering. A one-chunk record still gets one
workflow and one child, keeping the trace contract uniform. If `index chunks` remains the physical
parent, it is classified as the minimal ancestor and appears in Langfuse; its persistence siblings
do not.

One detached trace per embedding call was rejected because document-level inspection would
require cross-trace search for ordinary use. One ingestion-wide embedding workflow was rejected
because it mixes unrelated source records and loses the document boundary. A batch model span
around several Titan calls was rejected because it hides independent provider latency and
failures and incorrectly represents several API calls as one physical model operation.

### Bound individual model operations and derive boto pool capacity

`LLM_MAX_IN_FLIGHT` remains the one process-wide semaphore shared by chat and embeddings. A
tool-free extraction agent can have at most one Bedrock operation active at a time, so reserving
one slot for its invocation safely bounds chat concurrency across sequential retries, although it
also conservatively holds the slot during retry backoff. Each embedding coroutine waits for its
own rate-limit token and then reserves its own semaphore slot immediately around
`aembed_query()`; it never reserves one slot for a list of texts.

The existing shared token bucket remains attached to the chat model, where LangChain acquires one
token for every application-level retry attempt. Embeddings acquire the same bucket explicitly
once per `aembed_query()`. Botocore transport retries remain sequential inside the same client
operation, connection slot, and model span; the application does not claim HTTP-attempt-level
visibility below the SDK.

Both Bedrock clients receive `botocore.config.Config(max_pool_connections=LLM_MAX_IN_FLIGHT)`.
The pool is capacity for reusable connections, not a throttle; deriving it from the actual
concurrency bound avoids a second tuning knob. The executor, runnable work, or provider may yield
lower actual concurrency, but none may exceed the semaphore. `EMBEDDING_BATCH_SIZE` is removed
from settings, YAML policy, environment examples, and tests.

Keeping the old embedding batch setting as a scheduling window was rejected because it has no
independent requirement: a bounded worker/semaphore path already limits active network work, and
the name implies provider batching that does not occur. A separately configurable pool size was
rejected because values below the request bound only create connection churn, while larger values
provide no current benefit.

### Preserve signal-specific content and error ownership

The application collects GenAI inputs and outputs only when `CAPTURE_AI_CONTENT` is enabled and
masks known secrets before serialization. The Collector then applies a common secret-attribute
redaction policy to both branches. The Tempo branch additionally deletes heavy GenAI content
attributes such as messages, embedding inputs, tool definitions, arguments, and results while
retaining model identity, token usage, latency, status, and bounded business metadata. The
Langfuse branch applies the projection filter but does not delete approved captured GenAI
content. Logs never receive prompt, output, embedding, tool, or document payloads.

This ordering deliberately separates two decisions: whether content is collected and which
backend may retain it. Capture disabled means neither backend receives content; capture enabled
does not authorize Tempo or Loki to store it. Metrics remain on the existing operational path and
never contain payloads or identifiers.

Spans carry status and bounded `error.type` without exception events. The top-level ingestion run
continues to own the single escaping-exception log, which carries the same trace ID visible in
both trace backends plus `workflow_run_id`. A failure in an operational-only span is detailed in
Tempo and Loki; the retained root still reports the terminal run outcome in Langfuse. Recovered
model retries remain visible as model spans and do not create duplicate stack-trace logs.

For OTLP log delivery, the rendered local traceback is placed in the log body rather than in an
OTLP attribute. Searchable fields such as event name, service, `error.type`, `workflow_run_id`,
and bounded operation metadata remain attributes, while trace and span IDs remain native log
context. This preserves full local failure detail without asking Loki to store a large
`ExceptionGroup` traceback as structured metadata, which currently exceeds its per-record limit
and causes the only failure record to be dropped. Stdout JSON keeps the same information and the
central environment detail policy remains the sole redaction/truncation owner.

## Risks / Trade-offs

- **[A classified child has an unclassified parent]** → Centralize classification, assert the
  ancestor-closed invariant over exported span trees, and contract-test the Collector output for
  zero orphaned Langfuse observations.
- **[The span-level Collector filter changes across releases]** → Keep the exact Collector image
  pinned, validate its configuration in CI, and exercise a synthetic mixed operational/GenAI tree
  before promotion.
- **[Captured content leaks into Tempo or logs]** → Keep content collection opt-in, mask before
  serialization, apply branch-specific deletion, and verify capture-on canary text is present only
  in Langfuse.
- **[A single run trace grows too large]** → Keep the root boundary to one finite ingestion run and
  measure span count and serialized size. If production work becomes unbounded or crosses durable
  handoffs, introduce linked per-job traces in a separate change instead of extending this root.
- **[Per-chunk embedding spans increase trace volume]** → Retain them because each represents a
  physical model call; group them under bounded record-level workflows and rely on the Langfuse
  projection to remove unrelated operational volume.
- **[Semaphore slots are underused during chat retry backoff]** → Accept conservative utilization
  for the current tool-free sequential agents; revisit only if measurements show it limits the
  ingestion run materially.
- **[Thread-executor capacity is lower than the configured request limit]** → Treat
  `LLM_MAX_IN_FLIGHT` as an upper bound, not a throughput guarantee; do not add an executor-size
  setting without measured need.
- **[Native strategy selection changes in a dependency update]** → Pin dependencies and retain a
  live-marked Terra compatibility test plus fake-model contract tests.
- **[Existing local overrides still set `EMBEDDING_BATCH_SIZE` or point ingestion traces at the
  operational-only receiver]** → Document the configuration migration and make the committed YAML
  and environment example authoritative before the Compose smoke run.
- **[A large exception body reaches a backend line-size limit]** → Keep structured metadata
  bounded, test with a traceback larger than the previously rejected case, and retain the
  environment-specific detail policy as the place to introduce measured truncation if needed.

## Migration Plan

1. Add failing tests for native structured output, single-provider ownership, shared trace
   identity, ancestor-closed classification, record/chunk grouping, physical embedding
   concurrency, and client pool configuration.
2. Keep the existing single provider, add centralized projection classification, and update the
   Collector to produce the complete redacted Tempo view and filtered content-preserving Langfuse
   view from the same receiver.
3. Migrate the two agents to raw schema response formats and verify the optional live Terra path.
4. Replace flattened embedding batches with record-grouped, per-chunk calls, preserve result
   order, and move throttling to each model operation.
5. Remove `EMBEDDING_BATCH_SIZE`, document the single trace endpoint contract, update committed
   policy/environment documentation, and pass the derived boto pool configuration to both clients.
6. Move OTLP traceback detail from structured metadata into the log body and verify a large
   `ExceptionGroup` produces one correlated Loki record.
7. Run unit, Collector-contract, lint, and type checks, then run Compose ingestion with content
   capture both off and on. Verify identical trace IDs and retained span identities across Tempo
   and Langfuse, a complete redacted Tempo tree, a connected GenAI-only Langfuse projection,
   correlated Loki logs, bounded metrics, and destination-specific content behavior.

No evidence-store migration is required. Rollback restores the previous Collector processing and
embedding orchestration code; stored records and vectors remain compatible. Ordinary Compose
shutdown preserves all backend data, and no volume deletion is part of either migration or
rollback.
