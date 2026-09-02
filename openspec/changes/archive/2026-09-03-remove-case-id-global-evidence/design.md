## Context

See `proposal.md` for motivation. Today the same field participates in raw fixture schemas, canonical identity, PostgreSQL keys and indexes, trusted reader session state, tool contracts, agent checkpoints, API requests, and frontend routing. A caller-provided value therefore controls evidence visibility before the agent can reason about relevance. The repository is a local single-user prototype with one configured evidence corpus, no authentication boundary, and regenerable synthetic data.

The read-only SQL policy, bounded retrieval, provenance verification, checkpoint durability, and per-thread serialization remain valid controls and must survive this change. The active `add-investigation-chat-frontend` change still describes case-scoped routes and must be reconciled rather than implemented alongside contradictory requirements.

## Goals / Non-Goals

**Goals:**

- Remove the case concept from every active source format, persisted evidence shape, identity rule, runtime contract, conversation state, API, and UI route.
- Give every conversation the same bounded read access to the complete configured evidence store.
- Preserve deterministic generation, idempotent ingestion, globally resolvable provenance, read-only execution, and independent durable thread history.
- Leave the repository with no active behavior or documentation that can silently reintroduce a conversation-selected evidence partition.

**Non-Goals:**

- Add tenants, workspaces, authorization, per-user data access, or another renamed partition key.
- Allow unrestricted SQL, writes, unbounded retrieval, or unverified citations.
- Support loading two semantically duplicate language editions into the same store simultaneously; a deployment continues to ingest one configured edition.
- Preserve local prototype evidence rows or conversation checkpoints at the cost of retaining the removed field.

## Decisions

### 1. Remove the concept instead of defaulting or ignoring it

The field will be deleted from public and internal contracts. The API will strictly reject the obsolete request shape rather than accept and ignore it, because silent compatibility would preserve confusion and allow clients to believe the value has meaning. No replacement `workspace_id`, `dataset_id`, or fixed default scope will be introduced.

Alternatives rejected:

- Hard-code the canonical fixture value: this retains hidden filtering and fails when new sources are ingested.
- Keep the field as passive row metadata: this preserves composite identity and makes accidental reactivation likely.
- Let the model choose a case: relevance belongs in tool queries, but access to the configured corpus must not depend on an opaque partition selected by a model or user.

### 2. Use global, source-qualified identities

Canonical record identity will derive from source-system identity plus the stable source-record identity; record-version identity will add version/hash material. Transaction, account, communication, chunk, mention, entity, relationship, and provenance identities will use their existing stable business or source inputs without a case component. Local identifiers that can collide across source systems must be qualified at the canonical boundary.

Database uniqueness and foreign keys will be rebuilt around these global identities. Exact normalized identifiers may continue to reuse one typed entity globally, while actor-name non-merge rules remain unchanged. Source references will resolve directly by global record identity and content hash.

Alternative rejected: generate random replacement IDs, because that breaks deterministic fixtures, idempotent ingestion, reproducible ground truth, and stable citations.

The canonical formulas are UTF-8 strings with literal separators; hashes are lowercase
SHA-256 hex truncated to 32 characters where a compact identifier is required:

| Identity | Formula |
| --- | --- |
| Record | `<source_system>:<source_record_id>` |
| Record version | `<record_id>:<content_hash>` |
| Keyed entity | `<entity_type>:<normalized_key>` |
| Unkeyed entity | `<entity_type>:<record_id>:<label_slug>` |
| Relationship | `sha256(<subject_entity_id>|<predicate>|<object_entity_id>|<source_record_id>)[:32]` |
| Mention | `<record_version_id>:mention:<field-or-span>:<ordinal>` |
| Chunk | `<record_id>#<char_start>-<char_end>` |
| Account projection | `bank_account:<account_id>` with its parent record identified as `bank_account:<account_id>` |
| Transaction projection | `bank_transaction:<txn_id>` with its parent record identified as `bank_transaction:<txn_id>` |
| Communication projection | the parent record identity for the source-system item (`cdr:<record_id>`, `device_extraction:<msg_id>`, or `email:<email_id>`) |
| Ingestion run | `sha256(<dataset_version>|<fingerprint>|<embedding_model_id>)[:32]` |

`source_system` is mandatory at the record boundary. Thus identical local identifiers
such as `shared-1` from `cdr` and `email` produce distinct records, while the same
source item regenerated or ingested twice produces the same identity. Projection keys
remain meaningful source-qualified business identifiers; their database parent key is
the global record identity.

### 3. Keep global access behind the existing bounded read boundary

The `agent_read` views will expose all rows but continue to allowlist columns. The executor will no longer set session scope; it will retain a read-only transaction, AST policy gate, bound parameters, timeouts, row/byte limits, and server-owned outer limit. Retrieval will remove partition predicates while retaining source/time filters, exclusions, top-k, fusion, and deduplication. Graph traversal will remove same-partition checks while retaining deterministic bounds and source resolution.

The removed isolation mechanism is not replaced because the prototype has no tenant authorization requirement. If such a requirement appears later, it must be introduced at an authenticated workspace/tenant boundary with an explicit product model and migration.

### 4. Provenance validation becomes record-local

Evidence cards and tool outcomes will no longer compare every item with a thread-level scope. Each result will instead be validated by resolving its global record identity, checking its content hash, validating its locator, and ensuring referenced graph objects exist. A single answer may legitimately combine evidence from any source represented in the store.

This preserves grounding guarantees while removing an unrelated access decision from conversation state.

### 5. Threads are the only conversation boundary

`thread_id` remains the checkpointer identity and per-thread lock key. Control state retains state-schema and policy versions but no evidence partition. Request fingerprints cover request ID, thread ID, and exact message. A fresh thread gets empty conversation state but the same global evidence access as every other thread.

The checkpoint application-state version will be bumped. Because the removed field exists inside serialized checkpoint blobs and the environment is a regenerable local prototype, the supported migration is an explicit checkpoint reset during the data rebuild, not permanent compatibility code. This destructive step requires separate confirmation during apply.

### 6. Replace case navigation with conversation navigation

The frontend root will become a conversation home containing `New conversation` and recent threads. Supported history routes will be `/threads/<thread_id>`; `/cases/...` routes will redirect to the home and will never interpret their old segment as data scope. A new conversation creates a new thread identity and an empty transcript. Thread summaries and workspace headings contain no case field.

The active `add-investigation-chat-frontend` planning change must be updated, completed, or superseded so that only the conversation-only capability remains authoritative. Implementation must not leave both route models active.

### 7. Rebuild generated and local derived state

The generator will be changed first, then both editions and all expected/ground-truth artifacts will be regenerated. The evidence database schema will be migrated by recreation from the new authoritative schema, followed by a fresh ingestion run. Old checkpoints will be removed in the same explicitly approved local reset so the removed field does not survive in serialized blobs.

Before the reset, implementation will report the exact Docker volumes/tables affected and offer a backup. No planning or apply command may silently delete them.

## Risks / Trade-offs

- **[Global IDs collide after removing the namespace]** → Qualify source-local identifiers at the canonical boundary and add collision/uniqueness contract tests before regenerating fixtures.
- **[Global retrieval broadens result sets and latency]** → Preserve bounded top-k, time/source filters, deterministic fusion, statement limits, and performance tests; optimize indexes for global filters.
- **[Cross-source entity resolution creates false merges]** → Preserve typed exact-key reuse and the existing rule that actors are never merged by similar names.
- **[Breaking API and URL changes disrupt the current frontend]** → Change backend contracts and frontend client/routes in one apply sequence and add contract plus end-to-end tests.
- **[Old checkpoints or database rows retain the removed field]** → Use an explicit, approved clean local rebuild and verify active schemas, rows, generated artifacts, and checkpoints afterward.
- **[The active frontend change reintroduces case-scoped behavior]** → Reconcile that change before implementation completion and make repository-wide checks part of acceptance.
- **[Future multi-tenant needs cannot use this isolation]** → Treat future tenancy as a new authenticated architecture rather than preserving an unused prototype mechanism.

## Migration Plan

1. Reconcile the active frontend change and update the authoritative specifications before code changes.
2. Change global identity rules and remove the field from evidence models and the fixture generator; regenerate and validate both editions.
3. Update ingestion schemas, upsert keys, projections, provenance, and receipts against the new global identity contract.
4. Update database initialization, views, indexes, reader code, query execution, retrieval, graph traversal, and grounding validation.
5. Bump checkpoint state, remove the field from agent state and API contracts, and update observability metadata.
6. Replace frontend routes, launcher, contracts, recent-thread presentation, and navigation.
7. Run unit, contract, integration, dataset, and end-to-end regression suites, including the Aegean question and independent-thread tests.
8. After separate destructive-action approval, back up if requested, rebuild the local evidence/checkpoint database, re-seed object storage, rerun ingestion, and verify the live application.

Rollback before the local reset is a code/spec revert. Rollback after reset requires restoring the optional backup or regenerating the prior fixture and schema; old conversations are otherwise intentionally unrecoverable.
