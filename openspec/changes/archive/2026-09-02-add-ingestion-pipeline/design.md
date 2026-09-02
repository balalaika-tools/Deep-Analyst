## Context

See `proposal.md` for motivation. Current state that shapes the approach:

- The repository is a virtual uv workspace with one member (`data/dataset`); there is no
  `services/` or `libs/`, no Dockerfile, and no runtime dependency. Root tooling pins Python
  3.13.14 and uv 0.12.4; Ruff, mypy strict, and pytest are configured centrally. The
  pre-commit pre-push hooks reference paths from another repository and currently cannot pass.
- `docs/DESIGN.md` and `docs/DATA_MODEL.md` assume SQLite with FTS5. The Compose project
  already runs PostgreSQL 17, but it belongs to Langfuse by the archived `local-langfuse`
  design, and the running instance has neither `vector` nor `pg_search`.
- The English fixture is small: the largest document is under 1 KB, emails are under 500
  bytes, and there are 34 prose records (10 documents, 6 emails, 18 device messages). A
  400-token window would never split anything.
- The same phone appears as `+30 697 123 4567`, `697 123 4567`, `306971234567`, and
  `+306971234567` across sources. Phone normalization is the join key the demo depends on.
- `bank.sql` is PostgreSQL-compatible DDL plus `INSERT` statements with `TEXT` amounts and
  timestamps.
- A separately planned OpenTelemetry Collector and Grafana stack will receive OTLP traces and
  metrics; Langfuse remains a trace backend behind that collector.
- Bedrock is the provider for both embeddings (`amazon.titan-embed-text-v2:0`, 1024
  dimensions) and chat extraction; model identifiers and region are runtime environment
  values, and AWS credentials are temporary session credentials supplied through `.env`.
- The Compose project already runs a MinIO instance as Langfuse object storage; the
  archived `local-langfuse` design forbids reusing Langfuse's *bucket* as application
  storage but says nothing against a separate, isolated bucket on the same instance.
- There is exactly one environment, local. No staging or production variants exist, so
  configuration has one set of defaults and exception detail is always full.

## Goals / Non-Goals

**Goals:**

- One vertical slice from fixtures to a queryable evidence store, with every stored fact
  traceable to a record and locator.
- Deterministic core (parsing, normalization, rules, chunking, candidate validation) testable
  without a database or a model.
- Shared schema and telemetry plumbing that the future agent service reuses without importing
  ingestion internals.
- Compact, convention-based telemetry that demonstrates GenAI observability without custom
  span sprawl.

**Non-Goals:**

- Migrations (Alembic), incremental ingestion, checkpoints, quarantine handling, the Greek
  edition, PDF/OCR, semantic chunking, rerankers, and the retrieval or agent services.
- Person or organization identity resolution beyond exact identifier reuse.
- Building or configuring the OpenTelemetry Collector itself; this change only exports to a
  configurable OTLP endpoint.

## Decisions

### Dedicated `postgres-app` container on the ParadeDB image

Add a second PostgreSQL service running the ParadeDB image for PostgreSQL 17, pinned by
digest like every other image, with its own named volume and a loopback-only published port.
ParadeDB bundles `pg_search` (BM25) and `pgvector`, so one image satisfies both indexes.

Alternatives rejected: reusing Langfuse's PostgreSQL (violates the archived design and lacks
the extensions); `pgvector/pgvector:pg17` with native `tsvector` ranking (not BM25, which the
design documents promise); a separate search engine (an extra system for 34 chunks).

### Three source-of-truth tables plus derived projections

`records`, `entities`, and `relationships` stay the only authoritative tables. Four derived
tables are rebuilt from records on every run: `transactions`, `accounts`, `communications`,
and `chunks`. Each carries a `record_id` foreign key. Structured filters for the future
`query_records` tool use typed columns with B-tree indexes on `(case_id, booking_ts_utc)`,
`(case_id, amount_minor)`, IBANs, and communication endpoints.

Alternatives rejected: querying `payload_json` through JSON path casts (untyped, unindexed,
string keys throughout the tool); generated columns on `records` (nullable columns for every
record type on one shared table); keeping the `bank_raw` staging schema as the query surface
(source-shaped `TEXT` columns, no equivalent for communications).

### Evidence served from a seeded bucket, not bind mounts

The ingestion container reads its evidence from an S3-compatible bucket rather than from the
repository checkout. A one-shot `evidence-seed` Compose service (the MinIO client image,
pinned by digest) runs once per project start: it creates the `evidence` bucket if absent,
creates a dedicated MinIO user and a policy scoped to that bucket from `EVIDENCE_S3_ACCESS_KEY`
and `EVIDENCE_S3_SECRET_KEY`, and mirrors only `data/dataset/editions/<edition>/data/raw/` and
`manifest.json` to `datasets/<edition>/`. The seed is the only container that sees the
repository dataset, and it sees only those two paths. The ingestion service then depends on
`evidence-seed` completing successfully and on `minio` and `postgres-app` being healthy, and
mounts nothing from the repository except its read-only policy YAML.

Inside the service, an S3 adapter (`adapters/s3/evidence_bucket.py`, boto3 against
`EVIDENCE_S3_ENDPOINT`) materializes the edition prefix into a private temporary directory at
the start of a run; the existing file adapters then run unchanged against that directory. At
33 KB this is one listing and a few dozen small downloads, which is simpler and easier to test
than streaming every parser through an object client, and it keeps the runtime evidence
boundary as a bucket policy rather than a set of host paths. The same adapter and settings
target real S3 later by changing the endpoint and credentials.

Bucket layout:

```text
evidence/
  datasets/<edition>/raw/...          uploaded by the seed
  datasets/<edition>/manifest.json    uploaded by the seed
  indexes/<edition>/receipt.json      written by ingestion after a completed run
```

Alternatives rejected: bind-mounting `raw/` and `manifest.json` read-only (works, but ties the
container to the host checkout and does not generalize to a deployment); a second MinIO
service (cleaner isolation, but a heavier stack for one bucket); reusing MinIO's root
credentials in the ingestion service (grants Langfuse's bucket to application code).

### Bank source loaded through a transient staging schema

`bank.sql` is executed as-is inside a `bank_raw` schema in the application database
(`DROP SCHEMA IF EXISTS ... CASCADE` first, `search_path` set for the transaction), then rows
are read back with plain `SELECT`. The schema is dropped at the end of the run. This avoids
writing an SQL parser and keeps the source file as the evidence.

Alternative rejected: regex parsing of `VALUES` tuples, which is fragile and duplicates what
PostgreSQL already does.

### Whole-record chunks with offsets; no header splitter

Each prose record becomes one chunk with `char_start=0` and `char_end=len(text)`. The chunk
table keeps offsets so a multi-chunk record is a data shape, not a code change. A size guard
applies a recursive character splitter with start indexes only when a record exceeds the
configured window; at fixture scale this path never runs and is covered by a unit test only.
YAML front matter is parsed into the payload and excluded from chunk text; email subject and
body form the email text; transaction `remittance_info` is the transaction record's text so an
exact reference such as `INV-2231` is lexically retrievable from both a document and a
transaction.

Alternative rejected: `MarkdownHeaderTextSplitter`, which moves headers into metadata and
loses character offsets, breaking citation validation.

### Rules first, then two sequenced model calls per chunk

Deterministic identifier rules (phone, email, IBAN, IMEI, invoice reference) run over free
text before any model call and create or reuse typed entities with text-span references. Then:

1. **Entity extraction** returns `PERSON`, `ORGANIZATION`, `LOCATION` candidates with exact
   text, offsets, and aliases. The host verifies each span, drops candidates whose normalized
   value matches a rule entity (rule wins), and produces the validated entity list for the
   chunk.
2. **Relationship extraction** receives the chunk and the validated entity list as a closed
   set, plus the rule entities present in the chunk. It returns `USES`, `ASSOCIATED_WITH`,
   `DIRECTOR_OF`, `KIN_OF` candidates whose endpoints reference that set by exact text, with a
   supporting quote and offsets.

Validation contract, applied in order and counted per outcome: quote equals chunk text at
offsets (`rejected_span`); predicate endpoint types allowed by the ontology
(`rejected_type`); both endpoints resolve to an entity known for this chunk, with identifier
objects resolved through normalization to rule entities (`rejected_endpoint`). Accepted edges
are stored `proposed` / `llm`.

Sequencing costs a second call per chunk (34 extra calls) and buys endpoints that cannot
reference unknown entities. Alternative rejected: one combined call, whose relationship
endpoints frequently drift from its own entity list.

### Async at every I/O boundary, pooled async engine

The service entrypoint, orchestration, database access, model calls, embeddings, and receipt
writes are `async`. The engine is SQLAlchemy's async engine over `postgresql+psycopg` with
the async-adapted queue pool configured from settings (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`,
`DB_POOL_TIMEOUT_S`, `DB_POOL_PRE_PING`); sessions are `AsyncSession` created per unit of
work from one `async_sessionmaker` owned by bootstrap and disposed on shutdown. Schema and
index DDL run through `run_sync` on an async connection. The `bank.sql` staging script runs
as one multi-statement execute on a raw async connection.

Pure domain functions (normalization, identifier rules, chunking, edge rules, candidate
validation) stay synchronous: they never await and become harder to test as coroutines.
Fixture parsers are synchronous byte-to-record functions invoked from the async orchestration;
the files total about 33 KB, so thread offloading would add machinery without benefit.

Per-chunk extraction runs concurrently inside an `asyncio.TaskGroup`, bounded by the model
throttle below. Persistence happens per source in one transaction after its candidates are
validated, in deterministic record order, so upserts stay idempotent regardless of task
completion order.

Alternative rejected: `asyncpg`; psycopg 3 is one driver for both the async engine and the raw
staging execution, and pgvector supports it.

### Model throttling: requests per minute and in-flight limit

All Bedrock traffic passes through one `genai/shared/throttle.py` object built by bootstrap
from settings: `LLM_REQUESTS_PER_MINUTE` (default 60) and `LLM_MAX_IN_FLIGHT` (default 60).
The rate limit is LangChain's in-memory token-bucket rate limiter attached to the chat model
via `init_chat_model(rate_limiter=...)`, so it applies to every physical request including
retries, and the same limiter instance is awaited explicitly by the embedder before each
batch. The in-flight limit is one `asyncio.Semaphore` acquired by the extractors and the
embedder around each logical call. Both are process-wide and shared by entity extraction,
relationship extraction, and embeddings. `genai/shared/` is justified here by three current
consumers.

Alternatives rejected: a per-agent limiter (three buckets would exceed the provider quota
together); a semaphore alone (bursts on start would still trip Bedrock throttling).

### Extraction agents with retry middleware

Each extraction task is built with LangChain 1.x `create_agent`: no tools, the pydantic output
schema as `response_format`, and `ModelRetryMiddleware` for transient provider failures
(throttling, timeouts, connection errors) with bounded attempts and backoff. The GenAI
callback fires below the middleware, so every attempt is its own model span. Structured-output
validation failures are handled by the structured-output strategy's error feedback, verified
against the installed LangChain version during implementation, not assumed.

Layout per task follows the mandatory GenAI boundary:

```text
genai/shared/throttle.py        rate limiter + in-flight semaphore, shared by all three
genai/entity_extraction/        prompts.py schemas.py llm.py agent.py extractor.py
genai/relationship_extraction/  prompts.py schemas.py llm.py agent.py extractor.py
genai/embeddings/               llm.py embedder.py
```

`llm.py` builds the Bedrock chat model via `init_chat_model` with the configured model
identifier and region; `agent.py` wraps it with middleware and response format; `extractor.py`
implements the port and translates provider failures into the port's failure taxonomy.
Prompts delimit source text explicitly as untrusted data and instruct the model to return
nothing not present in the chunk.

Alternative rejected: `model.with_retry()` on a bare structured call. It is simpler, but the
agent harness gives the same shape the investigation agent will use and makes retry policy a
named, testable object.

### Workspace layout

```text
libs/
  evidence_model/src/evidence_model/
    ontology.py       # EntityType, Predicate, allowed endpoint pairs, status/method rules
    tables.py         # SQLModel tables: records, entities, relationships, projections, chunks,
                      # ingestion_runs; pgvector column type
    provenance.py     # SourceRef, TextSpanLocator, FieldLocator
  observability/src/observability/
    config.py         # frozen library-owned config; no environment reads
    providers.py      # TracerProvider/MeterProvider construction, OTLP/HTTP exporters,
                      # idempotent configure, bounded shutdown/force-flush
    spans.py          # start_span context manager with the error contract
    logging.py        # structlog processors: trace correlation, redaction, JSON, env-scoped
                      # exception detail
    langchain.py      # OTel GenAI callback + standard GenAI metrics (optional extra
                      # `langchain`, imports langchain_core only here)
services/ingestion/src/ingestion/
  main.py             # settings -> bootstrap.run
  bootstrap/runtime.py
  config/settings.py  # pydantic-settings, flat, env-only
  application/ingest_case.py
  domain/             normalization.py identifiers.py chunking.py edges.py candidates.py
  ports/              entity_extractor.py relationship_extractor.py text_embedder.py
                      ingestion_ledger.py
  adapters/fixtures/  cdr.py extraction.py email.py bank.py documents.py edition.py
  adapters/s3/        evidence_bucket.py (materialize datasets/<edition>, receipt object)
  genai/              (see above, plus shared/throttle.py)
  db/                 engine.py session.py extensions.py repositories.py indexes.py
  observability/events.py   # named log events and the run span vocabulary
```

Both libraries earn their boundary: the schema is the contract between two deployables, and
the telemetry lifecycle is identical for both. Each stays flat and holds no service policy.
Sessions and engines are service-local; both services use the async engine and their own
pool settings.

### Idempotency receipt plus ledger

Fingerprint = SHA-256 over the manifest bytes, embedding model identifier, chunking
configuration, and a pipeline version constant. The run is skipped only when the receipt
object `indexes/<edition>/receipt.json` in the evidence bucket holds the fingerprint **and**
`ingestion_runs` has a completed row with it. An empty or absent index prefix therefore
always runs the pipeline, which is the operator's intuition; the fingerprint on top of it is
what distinguishes a reset database volume, or a changed embedding model, from an ingested
store. All writes upsert on natural keys: `(case_id, source_system, source_record_id)`
for records, `(case_id, entity_type, normalized_key)` for keyed entities, a deterministic
relationship key `(case_id, subject, predicate, object, source_record_id)` for edges, and
`(record_id, char_start, char_end)` for chunks. The receipt is written last. A run failure
leaves no receipt and a `failed` ledger row.

Alternative rejected: an "index prefix is empty" check alone, which cannot tell a reset
database volume from an ingested one and would wrongly skip after the embedding model changes.

### Schema creation without Alembic

Bootstrap runs `CREATE EXTENSION IF NOT EXISTS vector` and `pg_search`, then
`SQLModel.metadata.create_all`, then the BM25 index on `chunks.text` and an HNSW cosine index
on `chunks.embedding`. Index DDL lives in `db/indexes.py` because SQLModel does not express
either index type. Alembic is deliberate production evolution.

### Compact telemetry

Trace shape for one run:

```text
run ingestion                                    root, app.job.name, app.outcome, counts
  load cdr | load extraction | load email | load bank | load documents
  index chunks
    embeddings amazon.titan-embed-text-v2:0      one per batch, gen_ai.operation.name=embeddings
  extract <record_id is an attribute, not the name>
    invoke_agent entity_extractor
      chat <model>                               one per physical attempt, token usage
    invoke_agent relationship_extractor
      chat <model>
```

Metrics: `gen_ai.client.operation.duration`, `gen_ai.client.token.usage` (standard, recorded
by the callback), `app.ingestion.candidates{kind,outcome}`,
`app.ingestion.chunks_indexed{source_system}`. No metric carries record identifiers.

Logs: structlog JSON with `trace_id` and `span_id`, events `ingestion.run_started`,
`ingestion.run_skipped`, `ingestion.source_loaded`, `ingestion.candidate_rejected`,
`ingestion.run_completed`, `ingestion.run_failed`. Exceptions are logged, not attached as span
events; spans carry `error.type` and status only. One log delivery owner, selected by
`LOG_EXPORT=stdout|otlp`, default stdout. `CAPTURE_AI_CONTENT` defaults to false. The batch job
force-flushes both providers before exit on every path.

Alternatives rejected: the Langfuse LangChain handler (second span owner, vendor-bound);
per-record custom spans for every rule (noise at fixture scale).

### Configuration

One pydantic-settings class per service with two owners. Everything with a safe default is
application policy baselined in `config/ingestion/<environment>.yaml` (pool, throttle,
chunking, embedding width, receipt location, OTLP endpoints, log delivery, telemetry identity,
content-capture policy, log level), mounted read-only into the container beside the virtual
environment and discovered by walking upward from the settings module
(`INGESTION_CONFIG_DIR` is the operator escape hatch); an environment variable of the same
name overrides a key for one process. The deployment contract is environment-only with no
YAML or Python fallback: `DATABASE_URL`, `EVIDENCE_S3_ENDPOINT`, `EVIDENCE_S3_BUCKET`,
`EVIDENCE_S3_ACCESS_KEY`, `EVIDENCE_S3_SECRET_KEY`, `DATASET_EDITION`, `AWS_REGION`,
`BEDROCK_CHAT_MODEL_ID`, `BEDROCK_EMBEDDING_MODEL_ID`. Every service directory carries a
`.env.example` with exactly three sections: REQUIRED (the contract above), OVERRIDABLE (every
YAML policy key, commented, with its baseline), and OPTIONAL (rare runtime-only knobs:
`ENVIRONMENT_NAME`, `SERVICE_INSTANCE_ID`, `INGESTION_CONFIG_DIR`); a contract test keeps
that file aligned with the settings class. Precedence is kwargs, process environment, `.env`,
YAML, class defaults, so any YAML key can be overridden per run. Throttle and pool values
are validated as positive integers. AWS credentials are passed through by Compose from
`.env` and read by the SDK chain; they are never settings fields.

### Testing

Profiles per member, mirroring the repository test conventions:

- `tests/unit/domain`: normalization equivalence classes (phone variants, money, UTC),
  identifier regexes on real sentences, chunk offset slicing, deterministic edges, candidate
  validation outcomes.
- `tests/unit/adapters`: each fixture parser against the real English `raw/` files; oracle is
  the manifest counts and known values such as `t_88`. The S3 adapter is tested with a fake
  object client (listing, download, receipt round trip, refusal of paths outside the prefix).
- `tests/unit/genai`: extractors with a fake chat model scripting structured output and
  provider errors; prompt delimiting of untrusted text; the throttle proves that in-flight
  calls never exceed the configured limit under a fake slow model and that the rate limiter is
  awaited before each embedding batch, using a controllable clock.
- `tests/unit/application`: `ingest_case` with fake ports covering skip, re-run, and
  receipt-after-success.
- `tests/integration/db` (marker `integration`, requires a test-scoped `TEST_DATABASE_URL`
  against `postgres-app`): extensions and schema creation, upsert idempotency, BM25 and HNSW
  index presence and an `INV-2231` lexical query.
- `tests/integration/adapters/s3` (marker `integration`, requires `TEST_EVIDENCE_S3_*` against
  the local MinIO with a test-scoped bucket): seed layout round trip and receipt persistence.
- `libs/*/tests`: ontology validation; observability import inertness, idempotent
  configuration, span error contract via in-memory exporter.
- No live Bedrock call in the default suite; one opt-in `live` marker test for structured
  output compatibility.
- Async tests use `pytest-asyncio` in strict mode with explicit markers; engines and sessions
  are created inside the test's loop, never at import time.

## Risks / Trade-offs

- [ParadeDB image drift or licensing surprises] → pin by digest; the pg_search dependency is
  isolated to `db/indexes.py` and one retrieval query, so falling back to native FTS is a
  contained change.
- [Bedrock session credentials expire mid-run] → retries classify authentication failures as
  permanent, the run fails fast with no receipt, and the next start re-runs; documented in
  README.
- [Structured-output behavior differs on Bedrock from the LangChain examples] → verify the
  strategy on the installed version with the live marker test before wiring the fake-model
  unit tests to a shape.
- [LLM rejects too much or too little] → `app.ingestion.candidates` by outcome makes the
  ratio visible per run; prompts stay small and cite the ontology table verbatim.
- [Duplicate `PERSON` entities confuse a reviewer] → this is the documented invariant; the
  wiki and README explain that the demo path runs through exact identifiers.
- [Telemetry export blocks exit] → bounded shutdown timeout in the library; export failure is
  logged and never changes the exit code.
- [Throttle defaults exceed the account's Bedrock quota] → both limits are settings; the
  `chat` spans with `error.type` and the retry middleware make throttling visible and
  survivable, and a lower value needs no code change.
- [Concurrent tasks interleave writes] → extraction only reads; persistence runs per source in
  deterministic order after validation, so task completion order cannot change stored state.
- [Two libraries before a second consumer exists] → both hold only the shared contract and
  no ingestion policy; the agent change is the acceptance test of the boundary.

## Migration Plan

1. Fix root tooling (workspace members, test paths, pre-commit paths) so the empty workspace
   passes lint, type-check, and test gates.
2. Add libraries, then the service skeleton with settings and bootstrap; verify a scoped
   `uv sync --package ingestion` installs only its closure.
3. Add `postgres-app`, `evidence-seed`, and `.env.example` entries; validate
   `docker compose config` and a seeded bucket listing.
4. Implement adapters and deterministic extraction with unit tests, then the store and
   integration tests, then chunking and embeddings, then GenAI extraction, then telemetry.
5. Add the Compose `ingestion` service and Dockerfile; run a first start, confirm the receipt,
   restart, confirm the skip.
6. Update DESIGN, DATA_MODEL, and README.

Rollback is removing the new members and Compose services; the Langfuse stack and dataset are
untouched. Deleting the `postgres-app` volume is the only destructive step and is documented
separately.

## Open Questions

- Exact ParadeDB image tag and digest at implementation time; resolved when the image is
  pulled and recorded in `.env.example`.
- Name of the collector Compose service and whether it scrapes container stdout, which decides
  the default of `LOG_EXPORT`; the application contract is the same either way.
- Whether the observability library's `langchain` extra should also carry the tool-call
  middleware now or when the agent service arrives; it does not affect ingestion.
