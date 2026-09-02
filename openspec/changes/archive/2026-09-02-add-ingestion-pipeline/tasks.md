## 1. Workspace and Tooling

- [X] 1.1 Update root `pyproject.toml` workspace members to `services/*`, `libs/*`, and `data/dataset`, and pytest `testpaths` to include `services` and `libs`; verify `uv lock --check` and `uv sync --frozen` succeed.
- [X] 1.2 Repair `.pre-commit-config.yaml` pre-push hooks to target `services libs data/dataset` for mypy and the workspace for pytest, remove the foreign `tflint` hook, add `pytest-asyncio` to the root dev group with `asyncio_mode = "strict"`, and register the `integration` and `live` pytest markers in the root config; verify `uv run --locked pre-commit run --all-files --hook-stage pre-push` passes on the empty workspace.
- [X] 1.3 Remove `/data/` from `.gitignore` and replace it with `data/indexes/` and `data/dataset/variants/` so the generator source and English/Greek editions are trackable; verify `git check-ignore data/indexes/x` succeeds and `git check-ignore data/dataset/README.md` fails.

## 2. Evidence Model Library

- [X] 2.1 Create `libs/evidence_model` with `pyproject.toml`, `src/evidence_model/`, and `ontology.py` defining entity types, predicates, allowed endpoint pairs, and status/method rules; verify unit tests reject a disallowed endpoint pair and an `llm`/`confirmed` combination.
- [X] 2.2 Add `provenance.py` with `SourceRef`, text-span and field locators, and a validator requiring at least one reference; verify a unit test proves a text-span quote slices correctly from sample text.
- [X] 2.3 Add `tables.py` SQLModel definitions for `records`, `entities`, `relationships`, `transactions`, `accounts`, `communications`, `chunks` (pgvector column, configurable dimension), and `ingestion_runs`, with natural-key unique constraints and projection indexes; verify `SQLModel.metadata` compiles DDL for the PostgreSQL dialect in a unit test.

## 3. Observability Library

- [X] 3.1 Create `libs/observability` with `config.py`, `providers.py` (tracer and meter providers, OTLP/HTTP exporters, idempotent configure, bounded shutdown), and `spans.py` (context manager with `record_exception=False` and `error.type`); verify tests show import is inert, same-config reconfigure is idempotent, and an escaping exception sets span status and `error.type` via an in-memory exporter.
- [X] 3.2 Add `logging.py` with structlog JSON processors, trace/span correlation, credential redaction, environment-scoped exception detail, and an `stdout|otlp` delivery switch; verify a test shows a log emitted inside a span carries matching `trace_id` and a redacted secret.
- [X] 3.3 Add `langchain.py` behind an optional `langchain` extra: an OTel callback producing one `chat <model>` span per physical model request with GenAI attributes, token usage, and the standard duration and token-usage histograms, content gated by a capture flag; verify tests with a fake chat model prove one span per attempt and no content attributes when capture is off.

## 4. Application Database in Compose

- [X] 4.1 Add the `postgres-app` service to `compose.yaml` on the ParadeDB PostgreSQL 17 image pinned by digest, with its own named volume, health check, and a `127.0.0.1:5432` binding; verify `docker compose up -d --wait postgres-app` becomes healthy and both `CREATE EXTENSION` statements succeed.
- [X] 4.2 Extend `.env.example` with `POSTGRES_APP_PASSWORD`, `AWS_REGION`, blank AWS credential passthrough, `BEDROCK_CHAT_MODEL_ID`, `BEDROCK_EMBEDDING_MODEL_ID`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `CAPTURE_AI_CONTENT`, and `LOG_EXPORT`; verify `docker compose config --quiet` fails when the password is blank and passes with a complete `.env`.

## 5. Ingestion Service Skeleton

- [X] 5.1 Create `services/ingestion` with `pyproject.toml` (workspace sources for both libraries), an async `src/ingestion/main.py`, `config/settings.py` including pool (`DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=10`, `DB_POOL_TIMEOUT_S=30`, `DB_POOL_PRE_PING=true`) and throttle (`LLM_REQUESTS_PER_MINUTE=60`, `LLM_MAX_IN_FLIGHT=60`) fields validated as positive, and `bootstrap/runtime.py`; verify `uv sync --frozen --no-dev --package ingestion` installs only the service closure and contract tests show startup fails naming a missing `BEDROCK_CHAT_MODEL_ID` and a zero `LLM_MAX_IN_FLIGHT`.
- [X] 5.2 Add `db/engine.py` building the async `postgresql+psycopg` engine with the pool settings and an `async_sessionmaker`, plus `db/extensions.py` and `db/indexes.py` creating extensions, tables, the BM25 index on `chunks.text`, and the HNSW cosine index on `chunks.embedding` through `run_sync`; verify an integration test against a test-scoped `TEST_DATABASE_URL` proves both indexes exist after bootstrap and the engine reports the configured pool size.

## 6. Deterministic Core

- [X] 6.1 Implement `domain/normalization.py` for phones (Greece default region), emails, IBANs, invoice references, money to minor units, and UTC time with originals retained; verify unit tests cover the four phone variants, `9800.00` to 980000, and the `+02:00` CDR example.
- [X] 6.2 Implement `domain/identifiers.py` regex rules for phone, email, IBAN, IMEI, and invoice references in prose returning text spans; verify unit tests find `+30 697 123 4567` in `R-01`, `694 987 6543` in `R-02`, and `INV-2231` in `R-05` at correct offsets.
- [X] 6.3 Implement `domain/chunking.py` producing whole-record chunks with offsets and a size-guarded recursive fallback; verify unit tests prove offset slicing equality for both paths.
- [X] 6.4 Implement `domain/edges.py` deterministic `COMMUNICATED_WITH`, `HELD_BY`, `TRANSFERRED_TO`, and `REFERENCES` edges with field locators; verify unit tests produce `confirmed`/`deterministic` edges from sample CDR, email, account, and transaction records.
- [X] 6.5 Implement `domain/candidates.py` span verification, rule-wins merge, endpoint type checks, and endpoint resolution with per-outcome counts; verify unit tests cover `rejected_span`, `rejected_type`, `rejected_endpoint`, accepted, and rule-over-model for a phone.

## 7. Source Adapters

- [X] 7.1 Implement `adapters/fixtures/cdr.py`, `extraction.py`, and `email.py` producing records and communication projections with normalized endpoints; verify unit tests against the English `raw/` files yield 55, 18, and 6 records with UTC event times and original timestamps.
- [X] 7.2 Implement `adapters/fixtures/bank.py` executing `bank.sql` into a transient `bank_raw` schema on a raw async connection and reading accounts and transactions into records and projections; verify an integration test yields 18 accounts and 35 transactions and that `t_88` carries `amount_minor` 980000 and `INV-2231`.
- [X] 7.3 Implement `adapters/fixtures/documents.py` parsing YAML front matter into the payload and the body into record text; verify a unit test yields 10 records and `R-01` text excludes front matter.
- [X] 7.4 Implement the `ingestion_ledger` port with fingerprint computation and a receipt store; verify unit tests show the fingerprint changes when the manifest bytes, embedding model, or chunking configuration change. (Superseded for the receipt location by 13.3.)

## 8. GenAI Extraction and Embeddings

- [X] 8.1 Define async `ports/entity_extractor.py`, `ports/relationship_extractor.py`, and `ports/text_embedder.py` with typed candidates and a transient/permanent failure taxonomy; verify mypy strict passes for the ports.
- [X] 8.2 Implement `genai/shared/throttle.py` with a process-wide LangChain in-memory rate limiter and an `asyncio.Semaphore`, both built from settings; verify unit tests with a fake slow model and a controllable clock prove in-flight calls never exceed the limit and that excess requests wait rather than fail.
- [X] 8.3 Implement `genai/embeddings/llm.py` and `embedder.py` over `BedrockEmbeddings` using async embedding, the shared throttle, and batch embedding spans; verify a unit test with a fake embeddings handle proves dimension checking, one span per batch, and the limiter being awaited before each batch.
- [X] 8.4 Implement `genai/entity_extraction/` (`prompts.py`, `schemas.py`, `llm.py` passing the shared rate limiter to `init_chat_model`, `agent.py` with `ModelRetryMiddleware`, async `extractor.py` holding the in-flight semaphore); verify unit tests with a fake chat model prove structured output translation, provider-error translation, and that the prompt delimits source text as untrusted.
- [X] 8.5 Implement `genai/relationship_extraction/` with the same shape, taking the validated entity set as a closed input; verify unit tests prove candidates referencing unknown entities are surfaced for rejection and quotes are passed through unchanged.
- [X] 8.6 Add one opt-in `live`-marked test that runs both extractors on `R-01` against Bedrock and asserts only shape, allowed types, and bounded latency; verify it is excluded from the default suite and runs with `-m live` when credentials are present.

## 9. Ingestion Use Case and Persistence

- [X] 9.1 Implement async `db/repositories.py` upserts on natural keys for records, entities, relationships, projections, chunks, and the run ledger, one transaction per source; verify an integration test running the same batch twice leaves identical counts.
- [X] 9.2 Implement async `application/ingest_case.py` orchestrating skip check, adapters, rules, chunking, embedding, concurrent per-chunk sequenced extraction in a bounded task group, validation, deterministic-order persistence, ledger, and receipt; verify unit tests with fake ports cover skip, run-on-missing-ledger, receipt-after-success, no-receipt-on-failure, and identical stored order regardless of task completion order.
- [X] 9.3 Wire `bootstrap/runtime.py` to construct the pooled engine, throttle, embedder, extractors, telemetry, and logging, and to dispose the engine and force-flush telemetry on every exit path; verify a bootstrap unit test with injected constructors proves engine disposal and telemetry shutdown run after a failing run.

## 10. Telemetry Vocabulary

- [X] 10.1 Add `observability/events.py` with the run span names, `app.ingestion.candidates` and `app.ingestion.chunks_indexed` counters, and the six named log events; verify a unit test with an in-memory exporter shows the root run span, five source spans, and candidate counter values for a fake run.

## 11. Compose Ingestion Service

- [X] 11.1 Add a workspace-root multi-stage Dockerfile for the ingestion service following the uv template with the exact Python and uv pins; verify `docker build -f services/ingestion/Dockerfile .` succeeds and the image contains no dev tools or sibling members.
- [X] 11.2 Add the `ingestion` Compose service with `restart: "no"`, dependencies on healthy `postgres-app` and `minio` and on `evidence-seed` completing successfully, a read-only mount of `config/ingestion` only, and environment passthrough including the evidence bucket settings; verify `docker compose config` shows no bind mount below `data/` and the expected dependencies.
- [ ] 11.3 Run a first `docker compose up --wait` with valid Bedrock credentials; verify the seed and ingestion containers exit 0, the receipt object `indexes/en/receipt.json` exists in the bucket, record counts match the manifest, `R-01` yields a proposed `USES` edge to the shared phone entity, and an `INV-2231` BM25 query returns `R-05` and `t_88`.
- [ ] 11.4 Restart the project; verify the seed leaves the bucket unchanged and the ingestion container exits 0 without model calls (no `chat` spans, `ingestion.run_skipped` logged), then remove the `postgres-app` volume and verify the next start re-runs, and delete `indexes/en/` from the bucket and verify the next start re-runs.

## 12. Documentation

- [X] 12.1 Update `docs/DESIGN.md` and `docs/DATA_MODEL.md` to replace SQLite/FTS5 with PostgreSQL, pgvector, and pg_search, add the derived projections section, and state the whole-record chunking default; verify no remaining `SQLite` or `FTS5` mention describes current behavior.
- [X] 12.2 Update `README.md` status table, add ingestion commands, Bedrock, application-database, and evidence-bucket configuration, the seed step, telemetry endpoint settings, credential-expiry behavior, and the AI-assisted development note; verify every command matches the final service names and paths.
- [X] 12.3 Run `openspec validate add-ingestion-pipeline --strict` and the full non-integration and integration test commands; verify every scenario in the three delta specs maps to a passing test or a documented manual verification from tasks 11.3 and 11.4.

## 13. Evidence Bucket

- [X] 13.1 Add the `evidence-seed` Compose service on a digest-pinned MinIO client image that creates the `evidence` bucket, a dedicated user and bucket-scoped policy from `EVIDENCE_S3_ACCESS_KEY`/`EVIDENCE_S3_SECRET_KEY`, and mirrors only `editions/en/data/raw` and `manifest.json` to `datasets/en/`; extend the root and service `.env.example` files; verify `docker compose up --wait evidence-seed` exits 0, a listing shows only `raw/` objects and `manifest.json`, and the evidence key is denied on the Langfuse bucket.
- [X] 13.2 Replace `DATASET_DIR` and `INDEX_DIR` in `config/settings.py` with `EVIDENCE_S3_ENDPOINT`, `EVIDENCE_S3_BUCKET`, `EVIDENCE_S3_ACCESS_KEY`, `EVIDENCE_S3_SECRET_KEY`, and `DATASET_EDITION`, and update both `.env.example` files and the env-example contract test; verify startup fails naming a missing bucket secret.
- [X] 13.3 Implement `adapters/s3/evidence_bucket.py`: materialize `datasets/<edition>/raw/` and `manifest.json` into a private temporary directory for the existing file adapters, refuse keys outside the prefix, and implement the receipt store over `indexes/<edition>/receipt.json`; wire it in bootstrap in place of the filesystem receipt and the edition path; verify unit tests with a fake object client cover listing, download, out-of-prefix refusal, receipt round trip, and absent receipt reads as none.
- [X] 13.4 Add an integration test (marker `integration`, `TEST_EVIDENCE_S3_*` against the local MinIO with a test-scoped bucket) proving the seed layout round-trips through the adapter and a receipt written after a run is read back; verify it fails fast when the variables are absent.
- [X] 13.5 Update `docs/DESIGN.md`, the change `verification.md`, and `README.md` for the bucket flow (seed, layout, reset by deleting `indexes/<edition>/`), then rerun task 12.3.
