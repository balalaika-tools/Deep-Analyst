# Synthetic Investigation Dataset

> **Synthetic data only.** All people, organizations, identifiers, transactions,
> and events are fictional and exist only for deterministic software testing.

`trg-synth` supplies communications, financial records, and documents for the
cross-source investigation prototype. Most records are ordinary background data
or hard negatives. No individual record establishes wrongdoing.

## Editions

| Edition | Path | Version | Intended use |
|---|---|---|---|
| English | `data/dataset/editions/en/data/` | `trg-synth-en-v1.0.0` | Primary prototype and evaluation fixture |
| Greek | `data/dataset/editions/el/data/` | `trg-synth-el-v1.0.0` | Meaning-equivalent multilingual regression fixture |

The editions describe the same case. Stable source IDs align them; content
hashes and text offsets do not.

## Contents per edition

| Source | Format | Records |
|---|---|---:|
| Carrier CDR | `raw/cdr.csv` | 55 |
| Device extraction | `raw/extraction.jsonl` | 18 |
| Email | `raw/emails/*.eml` | 6 |
| Accounts | `raw/bank.sql` | 18 |
| Transactions | `raw/bank.sql` | 35 |
| Documents | `raw/docs/*.md` | 10 |
| **Total** | | **142** |

`bank.sql` is PostgreSQL-compatible and defines readable `accounts` and
`transactions` tables.

## Generate and verify

Run from the repository root:

```bash
# English primary edition
uv run --package dataset make-dataset --seed 20260305
uv run --package dataset make-dataset --seed 20260305 --check

# Greek edition
uv run --package dataset make-dataset --seed 20260305 --locale el
uv run --package dataset make-dataset --seed 20260305 --locale el --check

# Contract tests for both editions
uv run pytest data/dataset/tests
```

`dataset` is a `uv` workspace member (see the repository-root `pyproject.toml`)
with its own `data/dataset/pyproject.toml`, `src/dataset/` package layout, and
`make-dataset` console script. `uv sync` from the repository root installs it
in editable mode; `uv sync --package dataset --no-dev` installs only its own
(empty) dependency set, isolated from any sibling service added later.

Non-canonical seeds default to:

```text
data/dataset/variants/<locale>/<seed>/data/
```

Use `--output PATH` only when a caller needs a different destination. Do not edit
generated evidence by hand; change the generator and regenerate so manifests,
hashes, and source spans remain consistent.

## Runtime boundary

Only files under `raw/` are case evidence. Runtime ingestion must exclude:

- `ground_truth.json`;
- `expected/`;
- `fixtures/quarantine/`.

These files exist only for tests and evaluation. Reading them at runtime would
leak the answer key into the assistant.

## Important conventions

- Canonical seed: `20260305`.
- Case: `case_trg_001`.
- Activity window: 20 February through 10 March 2026.
- Raw CDR and email timestamps retain their original offset; comparisons use UTC.
- Exact decimal money is preserved and normalized to integer minor units.
- Stable source IDs, manifests, and SHA-256 hashes make changes reproducible.
- English and Greek editions should normally be indexed separately.

The active fixture contract is [docs/DATASET_SPEC.md](../../docs/DATASET_SPEC.md).
The historical exhaustive contract remains under `deprecated/docs/`.
