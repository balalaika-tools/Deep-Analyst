# Deep Analyst — Investigation Assistant Wiki

## The scenario

This project is a prototype AI-powered assistant that helps an analyst investigate suspected
economic crime across three systems that don't talk to each other — a **communications log**, a
**financial transactions database**, and a **document store** of reports and files. Today an
analyst queries each system separately and pieces the story together by hand, with no durable trail
back to the evidence.

To make that concrete, we invented a small fictional case: a bank Suspicious Activity Report
describing three transfers kept just under a reporting threshold, moved from a company called
Aegean Trade to a company called Meridian Consulting. Around that core we generated communications
records, account and transaction data, emails, and case documents — most of it ordinary background
noise and deliberately confusing near-matches (a second, unrelated person who shares a surname, a
look-alike invoice reference, a phone used by more than one person). Every person, organization,
transaction, and identifier in the dataset is fictional. See [Dataset](dataset.md) for the full
inventory and safety note.

We then built a system that can answer a question such as:

> Is there any indication that Mavridis is connected to the €9,800 transfer on 5 March?

by composing evidence from all three sources into one preliminary, cited view — while keeping
every uncertain step explicitly uncertain, instead of turning a plausible-looking connection into
an accusation.

## What this wiki covers

The [design document](../docs/DESIGN.md) is the authoritative, detailed account of the system:
scope, trade-offs, risks, and production evolution. This wiki is a shorter, plainer-language tour
for a reviewer who wants the core ideas first, in a suggested reading order:

1. **[Architecture](architecture.md)** — the big picture: what the pieces are and how evidence
   flows from raw sources to a cited answer.
2. **[Data Layer](data-layer.md)** — how three differently-shaped sources become one small,
   evidence-backed graph, and the rules that keep it honest.
3. **[Dataset](dataset.md)** — the concrete synthetic case used to build and test the system.
4. **[Agent Layer](agent-layer.md)** — the investigation agent itself: its main agent, its two
   specialist sub-agents, its one deterministic tool, and how it remembers a conversation.
5. **[AI-Assisted Development](ai-assisted-development.md)** — the project instructions, skills,
   and workflow used to build this repository with AI coding assistants, and where that's noted in
   the code.

Each page links forward to the next. Everything described here is implemented and runnable
locally, not a mock — see the root [README](../README.md) for how to start it and ask it a
question yourself.

Start with → [Architecture](architecture.md)
