# trg-synth-dataset Specification

## Purpose

Defines the observable contract of the implemented `trg-synth` fixture generator.
This capability owns generation, source formats, deterministic editions, runtime
artifact boundaries, and contract verification. It does not prescribe the
prototype ingestion schema, ontology, retrieval implementation, or agent.

## Requirements

### Requirement: Deterministic canonical generation

The generator SHALL produce the canonical case `case_trg_001` from seed
`20260305` with stable source IDs, fixed generated metadata, and reproducible
content under the pinned toolchain.

#### Scenario: Repeated canonical builds are equivalent

- **WHEN** the canonical edition is generated twice with the same locale
- **THEN** corresponding files, records, IDs, and manifest hashes match

#### Scenario: A non-canonical seed is isolated

- **WHEN** generation uses a seed other than `20260305`
- **THEN** the output uses a distinct variant path and case namespace rather than
  overwriting or aliasing `case_trg_001`

### Requirement: Parallel English and Greek editions

The generator SHALL provide an English primary edition at `dataset/data/` and a
meaning-equivalent Greek edition at `dataset/editions/el/data/`. Both editions
SHALL preserve the same case, stable source IDs, timestamps, amounts, accounts,
devices, communication endpoints, planted relationships, and expected safety
behavior.

#### Scenario: A stable source ID aligns across editions

- **WHEN** the same source ID is read from both editions
- **THEN** it describes the same event or evidence item while translated text,
  hashes, and character offsets may differ

### Requirement: Exact source inventory

Each edition SHALL contain exactly 55 CDR records, 18 extraction messages, six
emails, 35 transactions, 18 accounts, and ten documents: 142 source records in
total, comprising 44 core-story and 98 background records.

#### Scenario: Generated counts differ from the contract

- **WHEN** any source count differs from the declared inventory
- **THEN** generation or independent verification fails with a specific error

### Requirement: Source-shaped raw artifacts

Each edition SHALL expose evidence under `raw/` as `cdr.csv`,
`extraction.jsonl`, `emails/*.eml`, PostgreSQL-compatible `bank.sql`, and
`docs/*.md`. Source timestamps, exact money lexemes, identifiers, and text SHALL
remain available for downstream provenance.

#### Scenario: Money is normalized by a consumer

- **WHEN** a consumer normalizes a transaction amount
- **THEN** it can derive exact integer minor units from the preserved decimal
  source value without using binary floating point

#### Scenario: Time is normalized by a consumer

- **WHEN** a consumer converts a local source timestamp to UTC
- **THEN** the original timestamp and offset remain available for citation and
  review

### Requirement: Manifest and integrity metadata

Each edition SHALL include a manifest containing dataset and source versions,
file and record counts, stable IDs, and SHA-256 integrity hashes.

#### Scenario: Evidence bytes are modified

- **WHEN** independent verification recomputes a changed file or record hash
- **THEN** verification fails rather than accepting the modified content

### Requirement: Runtime evidence isolation

Only artifacts under `raw/` SHALL be eligible for runtime ingestion. The
generator's `ground_truth.json`, `expected/`, and `fixtures/quarantine/` outputs
SHALL remain test-only.

#### Scenario: A runtime consumer scans an edition

- **WHEN** runtime ingestion enumerates evidence
- **THEN** it excludes the ground truth, expected outputs, and quarantine
  fixtures from records, indexes, and agent-visible content

### Requirement: Safety controls remain present

The fixture SHALL retain the critical hard negatives and safety cases used by
the prototype, including similar actor names, shared phone use, timezone
differences, similar invoice references, innocent amount-band transactions, and
the instruction-like `A-D1` document.

#### Scenario: A generated edition is contract-tested

- **WHEN** the contract suite runs
- **THEN** the safety fixtures and their expected non-merge, non-overclaim, and
  untrusted-content semantics remain present

### Requirement: Build and verification commands

The generator SHALL support `--seed`, `--locale {en,el}`, `--output`, and
`--check`, exposed as the `dataset` uv workspace member's `make-dataset`
console script. The repository SHALL provide contract tests runnable with
`uv run pytest` from the repository root.

#### Scenario: The canonical dataset is verified

- **WHEN** `uv run --package dataset make-dataset --seed 20260305 --check` and
  `uv run pytest dataset/tests` are run
- **THEN** manifest integrity and the implemented dataset contract pass
