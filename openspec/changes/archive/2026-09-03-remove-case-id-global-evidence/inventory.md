## Active case-scope inventory

Inventory command (run from the repository root):

```sh
rg -n -i 'case_id|case-scoped|case scoped|cross-case|/cases/' \
  --glob '!openspec/changes/archive/**' \
  --glob '!deprecated/**' \
  --glob '!services/investigation_web/node_modules/**' \
  --glob '!*.tsbuildinfo' \
  --glob '!.git/**' .
```

Baseline captured on 2026-09-03 before implementation:

| Workspace area | Matching files | Matching lines |
| --- | ---: | ---: |
| `data/dataset` | 77 | 1,154 |
| `libs/evidence_model` | 3 | 35 |
| `services/ingestion` | 23 | 101 |
| `services/investigation_agent` | 52 | 356 |
| `services/investigation_web` | 19 | 61 |
| Root/config/tests/wiki and active OpenSpec | 20 | 68 |
| **Total active inventory** | **194** | **1,775** |

The inventory covers every deployable and library package in the uv workspace, the
standalone Next.js service, generated EN/EL editions, root configuration and tests,
active OpenSpec artifacts, README files, and wiki documentation. Build outputs,
dependency directories, and TypeScript incremental metadata are not authoritative
source and are excluded.

`openspec/changes/archive/**` and `deprecated/**` are immutable historical records.
They intentionally retain the terminology and contracts in force when they were
written and are excluded from the final zero-occurrence acceptance search. The
`add-investigation-chat-frontend` change was archived on 2026-09-03, leaving
`remove-case-id-global-evidence` as the only active change; strict validation of the
active change set succeeds.
