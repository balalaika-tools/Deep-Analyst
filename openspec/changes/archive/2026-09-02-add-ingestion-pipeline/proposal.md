## Why

The design documents describe an ingestion path from heterogeneous fixtures to indexed
records, typed entities, and sourced relationships, but no runtime code exists yet: the
repository has only the dataset generator and a Langfuse stack. The investigation agent
cannot be built until real records, projections, indexes, and graph edges exist in a store
it can query, and the take-home is judged on that vertical slice being real, tested, and
observable.

## What Changes

- Add a `services/ingestion` deployable (uv workspace member, one-shot Compose service)
  that reads only the English edition's `raw/` evidence plus `manifest.json` from an
  object-store bucket seeded from the repository, builds common records, normalizes hard fields, creates deterministic entities and confirmed
  relationships, chunks and embeds text, runs constrained LLM extraction, and stores
  proposed semantic relationships.
- Replace the SQLite/FTS5 storage assumption in the design documents with a dedicated
  application PostgreSQL container (`postgres-app`) running pgvector for embeddings and
  pg_search for BM25. **BREAKING** for the documents only; no runtime code depended on
  SQLite.
- Add typed, rebuildable projection tables (`transactions`, `accounts`, `communications`,
  `chunks`) beside the three source-of-truth tables so structured filters and hybrid text
  retrieval never depend on JSON path expressions.
- Serve evidence through an S3-compatible bucket instead of bind mounts: a one-shot
  `evidence-seed` Compose service uploads only `raw/` and `manifest.json` of the configured
  edition to a dedicated `evidence` bucket on the local MinIO, under a dedicated access key,
  and the ingestion container mounts nothing from the repository but its policy YAML.
- Make ingestion idempotent and skippable: a fingerprint receipt object under
  `indexes/<edition>/` in the same bucket combined with an `ingestion_runs` row decides whether
  a Compose start re-runs the pipeline, and every write upserts on natural keys. An empty
  index prefix therefore always means "run".
- Add two internal libraries: `libs/evidence_model` (SQLModel tables, ontology, provenance
  references shared by ingestion and the future agent) and `libs/observability` (OpenTelemetry
  lifecycle, span helpers, structlog with trace correlation, and a LangChain GenAI callback
  behind an optional extra).
- Emit compact, convention-based telemetry over OTLP to a configurable collector endpoint:
  one run trace with per-source and per-model-call spans, standard GenAI token and duration
  metrics, two ingestion counters, and trace-correlated JSON log events.
- Read Bedrock model identifiers, region, database URL, and telemetry settings from the
  service's own typed configuration; AWS credentials come only from the standard SDK
  credential chain via the ignored `.env`.
- Update `docs/DESIGN.md`, `docs/DATA_MODEL.md`, and `README.md` to match the storage and
  projection decisions, and repair the root tooling (`pyproject.toml` workspace members and
  test paths, `.pre-commit-config.yaml` hook paths copied from another repository).

## Capabilities

### New Capabilities

- `ingestion-pipeline`: One-shot ingestion of the synthetic case fixture into the evidence
  store: source adapters, normalization, deterministic and constrained LLM extraction,
  chunking and embedding, idempotent re-run behavior, runtime evidence boundary, and the
  telemetry the run emits.
- `evidence-store`: The PostgreSQL schema the ingestion service writes and the agent will
  read: the three source-of-truth tables, the small ontology and its endpoint rules,
  provenance references, derived projection tables, and the lexical and vector indexes.
- `local-app-database`: A dedicated application PostgreSQL container in the local Compose
  project with the required extensions, persistent volume, loopback-only exposure, plus the
  seeded evidence bucket and the one-shot ingestion service that together enforce the dataset
  runtime boundary without bind mounts.

### Modified Capabilities

None. The `local-langfuse` and `trg-synth-dataset` requirements are unchanged; ingestion
consumes the dataset contract and exports telemetry to a configurable OTLP endpoint that the
separately planned collector will own.

## Impact

- New workspace members: `services/ingestion`, `libs/evidence_model`, `libs/observability`;
  root `pyproject.toml` members, pytest `testpaths`, and pre-commit/pre-push paths change.
- New Python runtime dependencies: SQLModel/SQLAlchemy with psycopg, pgvector, pydantic-settings,
  LangChain 1.x with `langchain-aws`, OpenTelemetry API/SDK/OTLP-HTTP exporter, structlog.
- Compose gains `postgres-app` (ParadeDB image, pinned by digest), a one-shot `evidence-seed`
  service (MinIO client image, pinned by digest), and a one-shot `ingestion` service with a
  workspace-root Dockerfile; `.env.example` gains the application database password, the
  evidence bucket access key, AWS credential passthrough, Bedrock model identifiers, and OTLP
  settings. Each service directory also carries its own `.env.example` documenting the
  required, optional, and overridable variables of that service.
- External runtime requirements: AWS Bedrock access to the configured Titan embedding model
  and the configured chat model; an OTLP endpoint is optional and export failure never fails
  a run.
- Documentation: `docs/DESIGN.md`, `docs/DATA_MODEL.md`, `README.md` status and commands.
- Local state: the `evidence` bucket in the Langfuse MinIO volume holds the uploaded raw
  evidence and the ingestion receipt; the `postgres-app` named volume holds the evidence store.
  The bucket is application data on a shared MinIO instance, isolated by its own bucket, user,
  and policy; it is not Langfuse's bucket.
