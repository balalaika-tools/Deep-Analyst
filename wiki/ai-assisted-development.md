# AI-Assisted Development

## Purpose

AI is used as an engineering assistant for scope critique, specification,
implementation, tests, documentation, and consistency checks. It does not
replace the repository's executable evidence: generated changes are reviewed
against source files, OpenSpec requirements, linters, type checks, and tests.

The setup has four layers:

```text
project instructions  decide the standing rules
skills                provide reusable task-specific workflows
MCP servers           expose external tools and live information
OpenSpec              records intent, decisions, requirements, and tasks
```

This repository follows the standard Codex concepts documented by OpenAI for
[project instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[skills](https://learn.chatgpt.com/docs/build-skills), and
[MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Repository layout

```text
Deep-Analyst/
├── AGENTS.md -> CLAUDE.md       shared repository engineering rules
├── .agents/skills/              canonical, versioned skill library
├── .codex/config.toml           project MCP configuration for Codex
├── .mcp.json                    shared MCP server declarations
├── .claude/
│   ├── skills/                  links back to .agents/skills
│   ├── commands/opsx/           OpenSpec workflow commands
│   └── settings.local.json      client-specific MCP settings
├── openspec/
│   ├── config.yaml              spec-driven workflow selection
│   ├── specs/                   accepted system behavior
│   └── changes/                 proposed, active, and archived changes
└── wiki/
    └── AGENTS.md                documentation-specific writing rules
```

`AGENTS.md` and `CLAUDE.md` resolve to one root instruction file, so Codex and
Claude receive the same code-style conventions. The wiki adds narrower
instructions for reviewer-facing documentation. A rule closer to the file being
edited refines the repository-wide rule.

The canonical skills live under `.agents/skills`. `.claude/skills` uses symbolic
links to that directory instead of maintaining a second copy. This makes the
same workflow available to multiple assistants while keeping one version under
review.

## How skills are used

A skill is a `SKILL.md` workflow with a name, a trigger description, and focused
instructions. It may also include reference material, scripts, templates, or
tool dependencies. An assistant can select a skill because the request matches
its description, or the user can request one explicitly.

The following inventory covers the **project-local, versioned skills**. Codex
may also expose built-in, user-level, or plugin skills, but those are environment
capabilities rather than dependencies of this repository.

### Backend, AI, and engineering quality

| Skill | Short purpose |
|---|---|
| `observability` | Designs and reviews OpenTelemetry traces, metrics, structured logs, GenAI telemetry, propagation, and Collector routing. |
| `prompt-writing` | Creates or improves reusable prompts and agent instructions with explicit inputs, outputs, and constraints. |
| `pytest` | Builds high-value Python tests for APIs, async code, workers, databases, integrations, and AI workflows. |
| `python-backend-structure` | Organizes Python services around application logic, ports, adapters, bootstrap, persistence, GenAI, and test boundaries. |
| `python-uv-workspace-monorepo` | Standardizes uv workspaces, internal packages, shared tooling, lockfiles, CI, and lean service images. |
| `settings-config` | Guides typed settings, YAML baselines, `.env.example`, secret handling, and external secret-manager integration. |

### Frontend engineering

| Skill | Short purpose |
|---|---|
| `feature-arch` | Organizes React code by business feature and protects module boundaries. |
| `msw` | Defines MSW v2 HTTP and GraphQL mocks, setup, isolation, timing, and debugging. |
| `nextjs` | Applies Next.js 16 App Router rules for routing, caching, Server Components, Actions, security, and deployment. |
| `nuqs` | Manages type-safe URL query state and search parameters in Next.js. |
| `playwright` | Designs reliable browser tests, selectors, authentication state, mocking, parallelism, and CI behavior. |
| `react` | Applies React 19.2 guidance for components, hooks, effects, Actions, concurrency, and the React Compiler. |
| `react-hook-form` | Guides React Hook Form v7 state, validation, subscriptions, field arrays, and UI integration. |
| `shadcn` | Composes checked-in shadcn/ui primitives with correct theming, accessibility, forms, and state behavior. |
| `tailwind` | Applies Tailwind CSS v4 theming, utilities, responsive design, dark mode, and build-performance practices. |
| `tanstack-query` | Handles TanStack Query v5 fetching, mutations, caching, prefetching, invalidation, and render optimization. |
| `tdd` | Provides the red-green-refactor workflow when tests are intentionally written before implementation. |
| `typescript` | Covers TypeScript configuration, type safety, async patterns, module design, errors, and migration concerns. |
| `ui-design` | Guides accessible, responsive, usable, and performant interface design and implementation. |
| `vercel-composition-patterns` | Designs scalable React APIs using composition, compound components, render props, and context. |
| `vercel-react-best-practices` | Reviews React and Next.js code for data-fetching, bundle, rendering, and runtime performance. |
| `vitest` | Covers Vitest 4 setup, async tests, mocks, environments, assertions, snapshots, and worker performance. |
| `web-design-guidelines` | Audits an interface for web usability, accessibility, and design-quality guidelines. |
| `zod` | Defines Zod 4 schemas, parsing, runtime validation, type inference, and error handling. |

### Diagramming

| Skill | Short purpose |
|---|---|
| `archify` | Generates polished, validated architecture, workflow, sequence, and data-flow diagrams as inline-SVG HTML, and produced the [architecture diagram](architecture.md) used in this wiki. |

### OpenSpec workflow

| Skill | Short purpose |
|---|---|
| `openspec-explore` | Investigates a problem and clarifies decisions without changing implementation. |
| `openspec-propose` | Creates a change proposal, design, delta specifications, and implementation tasks. |
| `openspec-update-change` | Revises existing planning artifacts and keeps proposal, design, specs, and tasks coherent. |
| `openspec-apply-change` | Implements the checked tasks of an approved OpenSpec change and verifies the work. |
| `openspec-sync-specs` | Copies accepted delta requirements into the main specifications without archiving the change. |
| `openspec-archive-change` | Finalizes a completed change and moves its history into the archive. |

Skills narrow the workflow; they do not override source evidence or grant extra
permissions. The assistant still follows repository instructions and the local
sandbox or approval policy.

## MCP servers

Model Context Protocol (MCP) connects the assistant to tools or information that
do not live in the checked-out files. The repository declares the same core
servers in `.mcp.json` and `.codex/config.toml`.

| MCP server | Transport | Use in this project |
|---|---|---|
| `langchain-docs` | Remote HTTP | Retrieves current LangChain and LangGraph documentation when implementation details may have changed. |
| `playwright` | Local `npx` process | Inspects and exercises browser behavior for UI and end-to-end work. |
| `github` | Remote HTTP | Reads or performs explicitly requested GitHub repository operations. |
| [`context7`](https://context7.com/docs/resources/all-clients) | Local `npx` process | Retrieves current, version-specific library documentation and code examples for the assistant's working context. |

## Typical working loop

1. The developer states the objective and any constraints.
2. Repository instructions establish the standing engineering and documentation
   rules.
3. The relevant skill supplies the task-specific workflow.
4. OpenSpec records material design decisions before broad implementation.
5. The assistant inspects source files and uses MCP only when live external
   information or tools are necessary.
6. Changes are applied in small, reviewable units and verified with the relevant
   tests, static checks, or OpenSpec validation.
7. The developer reviews the result and remains responsible for acceptance.

This is the last page in the suggested reading order — see [wiki/README.md](README.md) for the
full list, or the root [README](../README.md) for how to run the system yourself.
