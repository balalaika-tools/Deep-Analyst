# Records, normalization, graph, and Postgres — a practical guide

All examples here are **real data** from the synthetic dataset
(`data/dataset/editions/en/data/raw/...`) and **real code** from
`services/ingestion` and `libs/evidence_model`. For the short conceptual
picture, see [`wiki/data-layer.md`](../wiki/data-layer.md) first — this guide
goes deeper, with files/functions and real IDs.

## 0. The picture in one sentence

Each source record first becomes a **record** (common shape), then
deterministic rules build **entities/relationships** from whatever is already
structured, and only **free text** is passed through an LLM to propose
additional entities/relationships. The graph is not a separate database — it
is 3 Postgres tables.

```text
raw source → SourceRecord (records table)
              │
              ├─ structured fields → deterministic rules → entities + relationships (status=confirmed)
              │
              └─ free text (if present) → chunking → embeddings (chunks table)
                                                 → LLM entity extraction → LLM relationship extraction
                                                 → entities + relationships (status=proposed)
```

---

## 1. Step 1: source → record (one example per type)

Each source has its own adapter under `services/ingestion/src/ingestion/adapters/...`.
The adapter does two things: (a) fills the common `SourceRecord` envelope, (b)
calls deterministic normalization on whichever field needs it.
`record_id = "{source_system}:{source_record_id}"`.

### CDR (calls/SMS) — `adapters/fixtures/cdr.py`

Raw row (`raw/cdr.csv`):
```
record_id=c18, record_type=MTC, calling_msisdn=306930000101, called_msisdn=306930000102,
imei=350000000000022, ts_local=2026-02-20T09:10:00+02:00, duration_s=792
```
→ becomes record `cdr:c18`:
```python
SourceRecord(
  record_type="cdr", event_time_utc=<UTC ts>, text=None,   # not prose
  payload={..., "normalized": {"calling_msisdn": "306930000101",
                                "called_msisdn": "306930000102",
                                "imei": "350000000000022"}},
)
```
In parallel, a `CommunicationProjection` is created (channel `call`, from/to endpoints).

### Bank (accounts + transactions) — `adapters/fixtures/bank.py`

Raw SQL row (`raw/bank.sql`):
```sql
('t_88', '2026-03-05T14:30:00Z', ..., 'Aegean Trade OE', 'GR9201100010000000000046118',
 'Meridian Consulting Ltd', 'GR3601100010000000000054401', '9800.00', 'EUR',
 'booked', 'consulting services INV-2231', ...)
```
→ record `bank:t_88`:
```python
SourceRecord(
  record_type="transaction", text="consulting services INV-2231",  # remittance info = its "text"
  payload={..., "normalized": {"amount_minor": 980000, "currency": "EUR",
                                "debtor_iban": "GR9201...6118",
                                "creditor_iban": "GR3601...4401",
                                "invoice_refs": ["INV-2231"]}},
)
```
The account `acct_pa` (holder `Alexandros Mavridis`) becomes a separate record `bank:acct_pa`.

### Device extraction (SMS/app messages) — `adapters/fixtures/extraction.py`

Raw row (`raw/extraction.jsonl`, JSON lines):
```json
{"app":"sms","body":"leaving tomorrow, same place as last time","direction":"out",
 "imei":"356923107744818","msg_id":"X-204","peer":"+306949876543",
 "subscriber_msisdn":"+306971234567","ts_utc":"2026-03-04T21:14:00Z"}
```
→ record `extraction:X-204`:
```python
SourceRecord(
  record_type="extraction_message",
  text="leaving tomorrow, same place as last time",   # text IS present here — prose!
  payload={..., "normalized": {"subscriber_msisdn": "306971234567",
                                "peer": "306949876543", "imei": "356923107744818"}},
)
```
In parallel, a `CommunicationProjection` is created (channel `sms`), exactly like CDR.
The important part here: this record goes through **both paths** — deterministic
(`COMMUNICATED_WITH` between the two phones, §3a) **and** LLM (because it has `text`, §3b).

### Document (report) — `adapters/fixtures/documents.py`

`raw/docs/R-01.md`: YAML front matter + text. An excerpt:
> "A. Mavridis / Alexandros Mavridis... uses telephone +30 697 123 4567... possible,
> unconfirmed association with Meridian Consulting Ltd."

→ record `docs:R-01`:
```python
SourceRecord(record_type="document", text="<entire markdown body>",
             payload={"document_id": "R-01", "genre": "surveillance_report", ...})
```
There is no "normalization" here beyond date — the text stays as-is, because
normalization only touches *fields* (phones, IBANs...), not prose.

### Email — `adapters/fixtures/email.py`

`raw/emails/eM4.eml`:
```
From: "Akinita Saronikou IKE" <receipts@akinita-saronikou.example>
To: "Elena Vasileiou" <elena.vasileiou@personal.example>
Subject: Rent receipt
We received the March rent of €750.00. Transaction reference nT02.
```
→ record `email:eM4`, `text = "Rent receipt\n\nWe received..."`,
`payload.normalized = {"from": "akinita-saronikou.example"..., "to": "elena.vasileiou@personal.example"}`
(emails are normalized with `normalize_email` — lowercase, trimmed). A parallel
`CommunicationProjection`, channel `email`.

**(Note: `adapters/filesystem/receipt.py` is not yet a source of records — it is
simply the file that tracks "has this edition already run?" for idempotency, §5.)**

---

## 2. Normalization: **always deterministic, never LLM**

`services/ingestion/src/ingestion/domain/normalization.py` — pure functions,
no model, deterministic (same input → same output, always):

| Function | Example input → output |
|---|---|
| `normalize_phone` | `"+30 697 123 4567"` → `"306971234567"` |
| `normalize_email` | `"Elena.V@Example.com "` → `"elena.v@example.com"` |
| `normalize_iban` | `"gr80 0110..."` → `"GR8001100010000000000017719"` |
| `normalize_imei` | `"350-000-000-000022"` → `"350000000000022"` |
| `normalize_invoice_ref` | `"inv-2231"` → `"INV-2231"` |
| `money_to_minor_units("9800.00","EUR")` | → `980000` (int, not float — precision) |
| `to_utc` / `rfc2822_to_utc` | any timezone → UTC datetime |

These same functions are reused inside **free text** too: `domain/identifiers.py`
scans a text chunk with regex (phones, IBANs, IMEIs, emails, `INV-\d+`) and
whatever matches is passed through the same `normalize_*`. In other words: if a
phone number appears inside prose (e.g. in R-01), it is found **deterministically**,
not by the LLM. The LLM doesn't even see these identifiers as its own job.

**Answer to the question:** normalization (skeleton fields: phones, IBANs,
amounts, time, references) is 100% deterministic code. The LLM only enters at
the **next** step — to read meaning into free text (names of people/organizations/
locations, and relationship proposals).

---

## 3. Step 2: the Entity → Relationship → Entity graph, per record

There are **two separate paths** feeding the same graph — see `domain/edges.py`
(deterministic) and `genai/*/extractor.py` (LLM). Both write to the same tables,
but with a different `status`/`method`.

### 3a. Deterministic path — from structured fields

Runs **always**, without a model, as soon as each source is loaded
(`application/ingest_dataset.py::_load_source`). One function per record type:

- `communication_edges(cdr/email record)` → `PHONE/EMAIL --COMMUNICATED_WITH--> PHONE/EMAIL`
- `account_edges(account record)` → `FINANCIAL_ACCOUNT --HELD_BY--> PERSON/ORGANIZATION`
- `transaction_edges(transaction record)` → `FINANCIAL_ACCOUNT --TRANSFERRED_TO--> FINANCIAL_ACCOUNT`,
  and `TRANSACTION --REFERENCES--> INVOICE_REF` for each reference found in the remittance text

Example with `bank:t_88` from above:
```python
subject = FINANCIAL_ACCOUNT:GR9201100010000000000046118       # debited
object  = FINANCIAL_ACCOUNT:GR3601100010000000000054401       # credited
relationship = TRANSFERRED_TO, status=confirmed, method=deterministic,
                attributes={"txn_id": "t_88", "amount_minor": 980000, "currency": "EUR"}
# + TRANSACTION:t_88 --REFERENCES--> INVOICE_REF:INV-2231
```
These are always `status=confirmed` — the rule copied an explicit, structured
fact. There is no interpretation, so no LLM is needed.

### 3b. LLM path — only over free text (prose)

Runs **only** when `record.is_prose` is true (`domain/records.py:38`): i.e.
`record_type in {document, email, extraction_message}` **and** a non-empty
`text` exists. So: reports, emails, and the SMS/app messages from device
extraction (e.g. `X-204` above) — **not** CDR (no message body, `text=None`)
and **not** transactions (the remittance text is only picked up by the
deterministic rule in §3a via regex, it is not a prose record — never sent to
an LLM). The text is first split into chunks, and **per chunk** two separate
LLM calls happen in sequence:

**Call 1 — entity extraction** (`genai/entity_extraction/`): the chunk's text
is sent, the model must return a JSON schema:
```python
EntityCandidateOut(entity_type: "PERSON"|"ORGANIZATION"|"LOCATION",
                    text: str,       # exact, copied excerpt
                    aliases: list[str])
```
Only these 3 types are allowed for the model (`LLM_ENTITY_TYPES`). Never
PHONE/IBAN/DEVICE — the rule already finds those.

**Call 2 — relationship extraction** (`genai/relationship_extraction/`): takes
the same text **and** a list of `KNOWN ENTITIES` = whatever survived Call 1
**plus** whatever the deterministic rule found in the same chunk
(phones/IBANs/emails). The model must pick subject/object **exclusively** from
this list:
```python
RelationshipCandidateOut(predicate: "USES"|"ASSOCIATED_WITH"|"DIRECTOR_OF"|"KIN_OF",
                          subject_type, subject_text, object_type, object_text,
                          quote: str)   # exact sentence/excerpt proving it
```

**How the two connect** (the deterministic↔LLM "bridge"): the same chunk is
first scanned deterministically (`find_identifiers`) for PHONE/IBAN/EMAIL/IMEI/
INVOICE_REF. These entities go into Call 2's "known entities" so the LLM can
say e.g. "Mavridis --USES--> the phone 306971234567" — the exact same
phone-entity that already exists, not something new.

**Validation after the LLM** (`domain/candidates.py`) — nothing enters the
graph without this:
1. **Span check** — the `text`/`quote` must exist *verbatim* in the chunk
   (`_exact_span`). Otherwise → rejected.
2. **Type** — must be within the allowed set (`LLM_ENTITY_TYPES` / `LLM_PREDICATES`).
3. **`SUPERSEDED_BY_RULE`** — if the text the model proposed already contains an
   identifier the rule found, it is rejected (the rule always wins).
4. **Ontology endpoints** (`ontology.py::check_endpoint_types`) — e.g. `USES`
   is only allowed as `PERSON→PHONE` or `PERSON→DEVICE`. A proposed
   `PHONE --HELD_BY--> PERSON` is always rejected, no matter what the model said.
5. **Endpoint resolution** — subject/object must already exist among the
   "known entities" (rule or accepted LLM entity); a relationship with
   subject==object is rejected.
6. Whatever survives is written with **`status=proposed, method=llm` — always,
   never `confirmed`** (this is enforced in code at
   `ontology.py::check_status_method`, it is not a convention).

Worked example with `docs:R-01`:
```
chunk text: "...Alexandros Mavridis... uses telephone +30 697 123 4567..."
  deterministic: PHONE 306971234567 found in the text (regex)
  LLM call 1:    PERSON "Alexandros Mavridis" (with alias "Alex")
  LLM call 2:    USES(subject="Alexandros Mavridis", object="+30 697 123 4567",
                       quote="He uses telephone +30 697 123 4567.")
  validation:    span ok, type ok, PERSON→PHONE allowed, and both entities
                 known → ACCEPTED as proposed/llm
```
The same document also mentions "Dimitris Mavridis... his cousin" — this is
**never** merged with Alexandros: two PERSON entities with a similar name are
**never** automatically identified as the same (only keyed types like
PHONE/IBAN reuse an entity based on the same normalized value; PERSON/
ORGANIZATION have no such key).

---

## 4. How each entity/relationship gets its ID (and why nothing gets duplicated)

`libs/evidence_model/src/evidence_model/drafts.py` — the ID is **generated**,
not assigned randomly:

```python
# "keyed" type (PHONE, EMAIL_ADDRESS, FINANCIAL_ACCOUNT, DEVICE, TRANSACTION, INVOICE_REF, VESSEL)
entity_id = f"{entity_type}:{normalized_key}"
# e.g. "PHONE:306971234567"  -> the SAME id wherever this phone appears

# "non-keyed" actor type (PERSON, ORGANIZATION, LOCATION)
entity_id = f"{entity_type}:{scope_record_id}:{label_slug(label)}"
# e.g. "PERSON:docs:R-01:alexandros-mavridis" -> scoped to the record, never auto-merged

relationship_id = sha256(f"{subject_id}|{predicate}|{object_id}|{source_record_id}")[:32]
```

So: a phone number that appears in 3 different records (a CDR, an email, a
report) becomes **one** `PHONE:...` node with 3 `source_refs` (evidence
pointers) — not 3 separate nodes. A person mentioned in 2 different documents
becomes **two separate** nodes unless something else (a relationship) links
them explicitly — this is intentional, not a bug.

---

## 5. How the graph is actually "built" inside Postgres

**There is no separate graph database.** It's a ParadeDB instance (regular
PostgreSQL + the `pgvector` and `pg_search` extensions). The "graph" is 3 plain
tables:

```sql
-- libs/evidence_model/src/evidence_model/tables.py
records        (record_id PK, source_system, source_record_id, record_type,
                event_time_utc, text, payload JSONB, source_path, content_hash,
                UNIQUE(source_system, source_record_id))

entities       (entity_id PK, entity_type, label, normalized_key, source_refs JSONB,
                UNIQUE(entity_type, normalized_key))

relationships  (relationship_id PK, subject_entity_id FK→entities,
                predicate, object_entity_id FK→entities,
                status, method, occurred_at, source_refs JSONB, attributes JSONB)
```
Plus 4 "projections" that are rebuilt every time from the records (fast
filters, not additional truth): **`transactions`, `accounts`, `communications`,
`chunks`** (`chunks.embedding` is the vector column).

### Bootstrap (once, at the start of each run)

`db/extensions.py` + `db/indexes.py::bootstrap_store`:
```python
CREATE EXTENSION IF NOT EXISTS vector;      # pgvector
CREATE EXTENSION IF NOT EXISTS pg_search;   # BM25 lexical search
SQLModel.metadata.create_all(...)           # creates the 8 tables (CREATE TABLE)
CREATE INDEX ... USING bm25 (chunk_id, text, source_system, record_id)   -- on chunks
CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)                -- on chunks
```
There is no Alembic/migrations — it's a prototype: it is rebuilt every time,
it does not perform schema migrations.

### The write order within one run (`ingest_dataset.py` → `db/store.py`)

1. **`persist_source`** — upsert `records` + the corresponding projections
   (`accounts`, `transactions`, `communications`), per source, as it is loaded.
2. **`persist_chunks`** — after chunking+embedding of the text, upsert `chunks`.
3. **`persist_graph`** — after *all* chunks are done (deterministic + LLM
   candidates have already been merged in memory), upsert `entities` first,
   then `relationships` (foreign keys require it).

### Why re-running never duplicates anything

Every write is an `INSERT ... ON CONFLICT (key) DO UPDATE`
(`db/repositories.py::_upsert`), where `key` is the row's **primary key** — not
a random UUID, but an ID that is **deterministically computed from the natural
key** of the source (§4):

| Table | `ON CONFLICT` key (PK) | How it's generated |
|---|---|---|
| `records` | `record_id` | `f"{source_system}:{source_record_id}"` (e.g. `"bank:t_88"`) |
| `entities` | `entity_id` | from `entity_type` + `normalized_key`, or record+label for actors (§4) |
| `relationships` | `relationship_id` | `sha256(subject|predicate|object|record_id)[:32]` (§4) |
| `chunks` | `chunk_id` | `f"{record_id}#{char_start}-{char_end}"` |

(The schema also has separate `UNIQUE` constraints on the same natural key —
e.g. `uq_records_natural_key` on `(source_system, source_record_id)`,
`uq_entities_key` on `(entity_type, normalized_key)` — as a second safety net
in the schema, but the write's `ON CONFLICT` hits the PK itself.)

So a full re-run over the same dataset produces **exactly the same IDs**, so
each row simply replaces itself — zero duplicates. (If the run fingerprint —
dataset + embedding model + chunking config — matches an already-completed
run, the pipeline doesn't even touch the database, it sees this from the
receipt.)

---

## 6. Full path, one end-to-end example

```
raw/docs/R-01.md ("...Alexandros Mavridis...uses telephone +30 697 123 4567...")
      │
      ▼ adapter (documents.py)
records: record_id="docs:R-01", text=<markdown body>
      │
      ▼ chunking + embedding (is_prose=True)
chunks: chunk_id="docs:R-01#0-812", embedding=[...] (pgvector)
      │
      ├─ deterministic (identifiers.py, over the same chunk text)
      │     entities: PHONE:306971234567
      │
      └─ LLM (entity_extraction → relationship_extraction, validated)
            entities: PERSON:docs:R-01:alexandros-mavridis
            relationships: PERSON:docs:R-01:alexandros-mavridis
                            --USES(status=proposed, method=llm)-->
                            PHONE:306971234567
      │
      ▼ persist_graph (upsert)
entities table:       2 new rows (or reuse if PHONE already existed from CDR)
relationships table:  1 new row, status=proposed
```

In parallel, from `bank:t_88` (with no LLM at all):
```
FINANCIAL_ACCOUNT:GR9201...6118 --TRANSFERRED_TO(confirmed,deterministic)--> FINANCIAL_ACCOUNT:GR3601...4401
TRANSACTION:t_88 --REFERENCES(confirmed,deterministic)--> INVOICE_REF:INV-2231
```

A query over the same graph can follow `Mavridis --USES--> phone` (proposed)
and `Mavridis's account --?--> t_88` if a relevant HELD_BY chain exists
(confirmed) — but each step carries its own `status`, so the final answer
knows exactly which part of the chain is proven and which is a hypothesis.

---

## 7. Where to look next

| Topic | File |
|---|---|
| Deterministic normalization | `services/ingestion/src/ingestion/domain/normalization.py` |
| Regex identifiers in free text | `services/ingestion/src/ingestion/domain/identifiers.py` |
| Deterministic entities/relationships | `services/ingestion/src/ingestion/domain/edges.py` |
| LLM prompts/schemas | `services/ingestion/src/ingestion/genai/entity_extraction/`, `.../relationship_extraction/` |
| Validation after the LLM | `services/ingestion/src/ingestion/domain/candidates.py` |
| Ontology (types, predicates, endpoints) | `libs/evidence_model/src/evidence_model/ontology.py` |
| ID generation | `libs/evidence_model/src/evidence_model/drafts.py` |
| Postgres tables | `libs/evidence_model/src/evidence_model/tables.py` |
| Upserts / writing to the database | `services/ingestion/src/ingestion/db/repositories.py`, `db/store.py` |
| Extensions/indexes (BM25, HNSW) | `services/ingestion/src/ingestion/db/extensions.py`, `db/indexes.py` |
| Orchestration of a full run | `services/ingestion/src/ingestion/application/ingest_dataset.py` |
| Conceptual overview (no code) | `wiki/data-layer.md`, `wiki/architecture.md` |
