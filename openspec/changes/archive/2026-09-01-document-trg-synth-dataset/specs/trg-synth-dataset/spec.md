## Purpose

Defines the observable, testable contract of the `trg-synth` synthetic investigation dataset generator: what it must produce, how the English and Greek editions relate, and what a consumer or test suite may rely on. Exhaustive pinned fixture content (exact personas, messages, transactions, documents, golden-question wording, and ground-truth assertion tables) remains authoritative in `docs/DATASET_SPEC.md`; this spec states the structural contract that content must satisfy.

## ADDED Requirements

### Requirement: Deterministic, Reproducible Generation
The system SHALL generate the dataset from a Python-standard-library-only generator (`dataset/make_dataset.py`) driven by an RNG seed, such that the canonical seed `20260305` with a fixed `case_id` of `case_trg_001` and a fixed `generated_at` timestamp produces byte-identical output across repeated runs on the pinned toolchain.

#### Scenario: Canonical seed is byte-identical across runs
- **WHEN** `python3 dataset/make_dataset.py --seed 20260305` is run twice into separate output directories
- **THEN** every generated file under each run's `data/` tree is byte-identical to the corresponding file from the other run

#### Scenario: Non-canonical seed is namespaced as a variant
- **WHEN** the generator is run with a seed other than `20260305`
- **THEN** output is written under a distinct `variant_id`/case namespace (e.g. `case_trg_variant_<seed>`) rather than overwriting or aliasing `case_trg_001`, and that namespace is recorded in the manifest and canonical IDs

### Requirement: Dual-Language Edition Equivalence
The system SHALL generate two parallel editions of the same case from the same code: a primary English edition at `dataset/data/` (`--locale en`, dataset version `trg-synth-en-v1.0.0`) and an alternate Greek edition at `dataset/editions/el/data/` (`--locale el`, dataset version `trg-synth-el-v1.0.0`). Both editions SHALL share the same `case_id`, the same 142 stable source-record IDs, the same file formats/schemas/directory layout, the same timestamps/amounts/accounts/devices/communication endpoints, and the same entity relationships, hard negatives, and screening outcomes. Only human-readable source text, translated-content-derived hashes, and character offsets for cited spans SHALL differ between editions.

#### Scenario: Same stable IDs resolve across editions
- **WHEN** a stable source ID such as `X-204` is looked up in both the English and Greek editions
- **THEN** both editions have a corresponding record describing the same event, with the same timestamp, endpoints, and semantic content, differing only in source-language text and content-derived identifiers

#### Scenario: Editions are not silently mergeable by hash
- **WHEN** English and Greek edition records are compared
- **THEN** their raw-file hashes, record hashes, source-reference IDs, and span offsets differ, and no code path treats matching hashes as a cross-language join key

### Requirement: Output Layout and Exact Feed Totals
The system SHALL write each edition as `data/manifest.json`, `data/raw/{cdr.csv, extraction.jsonl, emails/*.eml, bank.sql, docs/*.md}`, `data/ground_truth.json`, `data/policies/trg-policy-v1.0.0.json`, `data/fixtures/quarantine/*`, and `data/expected/previews/*`. Per-edition feed totals SHALL be exactly: 55 CDR rows, 18 extraction messages, 6 emails, 35 transactions, 18 accounts, and 10 documents (142 total), and generation SHALL fail if any total differs.

#### Scenario: Generated edition matches the declared layout and counts
- **WHEN** an edition is generated
- **THEN** every listed path exists under that edition's `data/` root and the record counts per feed exactly match the declared totals

#### Scenario: A feed-count mismatch fails the build
- **WHEN** any feed's generated row/record count does not match its declared total
- **THEN** the generator build fails rather than producing a partial or mismatched dataset

### Requirement: CDR Raw Schema
The system SHALL emit `data/raw/cdr.csv` with columns `record_id, case_id, seq, record_type, subscriber_msisdn, calling_msisdn, called_msisdn, imei, cell_id, ts_local, duration_s, sms_len, source_version`, where `record_type` is one of `MOC`, `MTC`, `SMS-MO`, `SMS-MT`; `calling_msisdn`/`called_msisdn` always express network sender/recipient roles while `subscriber_msisdn` identifies the producing line; `ts_local` is ISO 8601 with an explicit UTC offset; and CDR rows never carry message body content.

#### Scenario: Every CDR row has a valid record_type and offset timestamp
- **WHEN** a row is read from `data/raw/cdr.csv`
- **THEN** it contains all required columns, `record_type` is one of the four allowed values, and `ts_local` carries an explicit offset

### Requirement: Device Extraction Raw Schema
The system SHALL emit `data/raw/extraction.jsonl` as one JSON object per line with fields `msg_id, case_id, imei, subscriber_msisdn, direction, peer, app, ts_utc, body, source_version`, where `direction` is `in` or `out` relative to the extracted device, `app` is `sms`, `whatsapp`, or another declared enum value, and `body` may be null for content-less artifacts.

#### Scenario: Every extraction record has a valid direction and app
- **WHEN** a line is parsed from `data/raw/extraction.jsonl`
- **THEN** it is valid JSON containing all required fields, `direction` is `in` or `out`, and `ts_utc` is a UTC timestamp

### Requirement: Email Raw Schema
The system SHALL emit `data/raw/emails/<email_id>.eml` as RFC-style messages containing `Message-ID`, `From`, `To`, `Date`, `Subject`, `Content-Type`, a plain-text body, and synthetic metadata headers `X-Case-ID`, `X-Source-Record-ID`, and `X-Source-Version`. The raw `Date` header SHALL retain its original local offset; a canonical UTC `sent_at_utc` SHALL be derived without modifying that header.

#### Scenario: Every email file carries required headers and case metadata
- **WHEN** a file under `data/raw/emails/` is parsed
- **THEN** it contains `Message-ID`, `From`, `To`, `Date`, `Subject`, `X-Case-ID`, `X-Source-Record-ID`, and `X-Source-Version`, and the `Date` header's original offset is unchanged by canonicalization

### Requirement: Bank Raw Schema
The system SHALL emit `data/raw/bank.sql` as PostgreSQL-compatible SQL creating an `accounts` table (`case_id, account_id, iban, holder_name, holder_type, bic, opened_date, source_version`, primary key `(case_id, account_id)`, unique `(case_id, iban)`, `holder_type` constrained to `person`/`organization`) and a `transactions` table (`case_id, txn_id, booking_ts_utc, value_date, debtor_name, debtor_iban, debtor_bic, creditor_name, creditor_iban, creditor_bic, amount_text, currency, status, remittance_info, source_version`, primary key `(case_id, txn_id)`, foreign keys from `debtor_iban`/`creditor_iban` to `accounts.iban`). Every amount SHALL originate as an exact decimal string (`amount_text`) and normalize to integer minor units (`amount_minor`) without using binary floating point.

#### Scenario: Importing bank.sql produces referentially consistent tables
- **WHEN** `data/raw/bank.sql` is imported into PostgreSQL 14+
- **THEN** every transaction's `debtor_iban` and `creditor_iban` resolve to an existing account in the same case, and every `amount_text` parses exactly under its currency's decimal scale to the expected `amount_minor`

### Requirement: Document Raw Schema
The system SHALL emit `data/raw/docs/<document_id>.md` as Markdown files whose front matter contains `document_id, case_id, document_date, genre, source_reliability, source_version`, with the document body treated as untrusted source text regardless of a document's declared reliability or genre.

#### Scenario: Every document has parseable front matter
- **WHEN** a file under `data/raw/docs/` is parsed
- **THEN** its front matter contains `document_id, case_id, document_date, genre, source_reliability, source_version`, and downstream processing treats the body as untrusted content

### Requirement: Canonical Envelope and Derived Table Schemas
The system's canonical layer SHALL represent every ingested raw record as a canonical envelope containing `case_id, record_id, record_version_id, source_system, source_record_id, source_version_id, raw_object_uri, raw_content_hash, record_type, event_time_utc, original_time_value, normalized_payload, parser_version, ingested_at`, and SHALL expose derived tables `entity_mentions`, `canonical_entities`, `mention_resolutions`, `relationship_assertions`, `event_crosswalks`, and `canonical_transactions` with the fields documented in `docs/DATASET_SPEC.md` §4.6. Exact-identifier resolution SHALL unify only same-type asset nodes (a phone number identifies one `PHONE` node, an IBAN one `FINANCIAL_ACCOUNT` node, an email address one `EMAIL_ADDRESS` node) and SHALL NOT automatically merge `PERSON` or `ORGANIZATION` entities on that basis.

#### Scenario: Shared asset identifiers do not merge person entities
- **WHEN** two different `PERSON` entities are linked to the same `PHONE` node via separate `USES` assertions
- **THEN** the phone number resolves to one `PHONE` entity while the two `PERSON` entities remain distinct, unmerged entities

### Requirement: Stable, Deterministic Identifier Formats
The system SHALL derive canonical identifiers deterministically: `record_version_id = case_trg_001:<source>:<source_record_id>:v1`, `mention_id = <record_version_id>:mention:<field-or-span>:<ordinal>`, `assertion_id = rel:<subject>:<predicate>:<object>:<canonical-assertion-payload-hash>`, and `event_link_id = same-event:<cdr-id>:<extraction-id>:reconciliation@1`. Source-local IDs (e.g. CDR `c01`–`c55`, extraction `X-###`, email `eM#`, transaction/account IDs, document IDs `R-##`/`N-D#`/`A-D1`) SHALL be stable semantic IDs, never row numbers, and SHALL be unique within a build.

#### Scenario: Changed content produces a new version instead of overwriting
- **WHEN** a source record's content changes between generation runs for the same source-record ID
- **THEN** the system creates a new `record_version_id` rather than overwriting a version already referenced by an existing citation

### Requirement: Manifest and Provenance Hashing
The system SHALL write `manifest.json` for every edition recording its language, edition role, dataset version, locale-qualified source versions, per-record-type hash-serialization contract, and a SHA-256 hash for every raw file and every source record, computed over a documented canonical UTF-8 serialization (sorted keys, stable decimal/timestamp lexemes, LF line endings). `manifest.json` SHALL also list non-corpus artifacts (e.g. `ground_truth.json`, `policies/*.json`, `expected/previews/*`) with their file hashes, distinct from the corpus record hashes.

#### Scenario: Manifest hash matches recomputed content hash
- **WHEN** a raw file or a source record's canonical serialization is re-hashed with SHA-256
- **THEN** the result matches the hash recorded for that file or record in `manifest.json`

### Requirement: Versioned Reconciliation, Identity, and Screening Policy
The system SHALL ship a versioned policy artifact at `data/policies/trg-policy-v1.0.0.json` pinning, at minimum, reconciliation parameters (timestamp tolerance, device-to-network role normalization, ambiguous-candidate abstention), identity-resolution parameters (exact same-type-asset auto-merge; `shared_phone_person_merge` and `shared_iban_person_or_org_merge` both set to `never`; weighted-sum scoring with `theta_auto`/`theta_min` thresholds; scores documented as ranking features, not probabilities), approximate-reference-matching tolerances, and screening rule parameters (`structuring_sub_threshold`, `comms_before_transfer`). Every policy-scoped result SHALL record which `policy_version` produced it.

#### Scenario: Shared phone or IBAN never force-merges people or organizations
- **WHEN** two different `PERSON` (or `ORGANIZATION`) entities are linked through the same phone number or IBAN
- **THEN** identity resolution under the pinned policy does not merge those entities, consistent with `shared_phone_person_merge: never` and `shared_iban_person_or_org_merge: never`

#### Scenario: Ambiguous reconciliation candidates abstain rather than force a match
- **WHEN** a CDR/extraction reconciliation candidate pair does not uniquely satisfy the pinned matching predicate
- **THEN** the system abstains from linking them rather than selecting an arbitrary candidate

### Requirement: Quarantine Fixtures
The system SHALL generate deliberately malformed parser-input fixtures under `data/fixtures/quarantine/` (e.g. malformed timestamp/timezone, invalid IBAN checksum, over-scale currency amount, duplicate source ID with conflicting content, malformed email headers, unsupported document format), each producing a documented quarantine outcome. Quarantine fixtures SHALL be excluded from all corpus totals, indexes, profiles, and golden-answer denominators.

#### Scenario: A quarantine fixture is rejected and excluded from corpus totals
- **WHEN** ingestion processes a file under `data/fixtures/quarantine/`
- **THEN** the record is quarantined with its documented error code and is not counted toward any feed's corpus total

### Requirement: Ground-Truth and Test-Only Artifact Isolation
The system SHALL treat `data/ground_truth.json` and everything under `data/expected/` as test-only artifacts that MUST NOT be ingested, indexed, or exposed to a runtime application. `ground_truth.json` SHALL define, at minimum, entity/resolution/assertion ground truth, required event crosswalks, expected screening fire/no-fire outcomes, and exactly twelve golden-question rubrics (each with required claims, acceptable evidence sets, forbidden claims, required caveats, and coverage requirements), all resolving to existing source records, fields/spans, entities, or assertions.

#### Scenario: Runtime ingestion excludes test-only artifacts
- **WHEN** the runtime ingestion path processes an edition's `data/` tree
- **THEN** it does not read, index, or expose `ground_truth.json` or any file under `expected/`

#### Scenario: Every ground-truth reference resolves to a real record
- **WHEN** a reference inside `ground_truth.json` (a source record, span, entity, or assertion ID) is resolved against the generated corpus
- **THEN** the referenced record, span, entity, or assertion exists in that edition's generated output

### Requirement: Build, Verify, and Contract-Test CLI
The system SHALL provide a command-line generator supporting `--seed`, `--locale {en,el}`, `--check` (independent manifest/invariant verification), and `--output PATH` (custom packaging location, non-canonical default `dataset/variants/<locale>/<seed>/data/`), and SHALL provide a contract-test suite (`dataset/tests/test_dataset_contract.py`, runnable via `python3 -m unittest discover -s dataset/tests -p 'test_*.py'`) that validates feed counts, schemas, pinned fragments, exact money handling, reconciliation, hard negatives, quarantine behavior, ground-truth reference integrity, and all twelve golden-question rubrics.

#### Scenario: `--check` independently verifies a generated edition
- **WHEN** `python3 dataset/make_dataset.py --seed 20260305 --check` is run against an already-generated `data/` tree
- **THEN** it verifies manifest hashes and the documented hard invariants and reports success or a specific failure without regenerating the tree

#### Scenario: Contract tests pass against a freshly generated dataset
- **WHEN** both editions are generated with the canonical seed and `python3 -m unittest discover -s dataset/tests -p 'test_*.py'` is run
- **THEN** all contract tests pass
