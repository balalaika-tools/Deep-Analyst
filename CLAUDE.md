## Code Style

These are strong defaults, not mechanical rules. Deviations are acceptable when
they improve clarity and should be easy to justify during review.

1. **Keep functions focused and readable.**
   Prefer functions under ~40 lines, cyclomatic complexity ≤ 10, and nesting
   depth ≤ 3. Treat these as review signals, not hard limits. Use guard clauses
   and early returns when they make control flow clearer.

2. **Give each unit one cohesive responsibility.**
   Functions, classes, and modules should have one clear reason to change.
   If their description combines unrelated responsibilities, split them.
   Coordination of closely related steps may remain together.

3. **Write comments that add information.**
   Public APIs should document their contract when it is not already obvious
   from the name, signature, and types. Comment why a decision exists, not what
   the code visibly does. Remove commented-out code and decorative banners.

4. **Keep modules cohesive.**
   Around 300 lines, review whether the module contains multiple responsibilities
   and should become a package. Do not split files solely to satisfy a line count.

5. **Organize primarily by business capability.**
   Prefer packages such as `billing/`, `ingestion/`, and `pricing/` over
   catch-all modules such as `utils.py`, `helpers.py`, or `common.py`.
   Technical packages are appropriate for explicit boundaries such as APIs,
   persistence, and infrastructure. Use precise names.

6. **Use explicit interfaces.**
   Type-hint public interfaces and important internal boundaries. Avoid mutable
   default arguments. Prefer keyword-only arguments when they improve clarity.
   Introduce a dataclass or parameter object when the values form a cohesive
   concept—not merely because an arbitrary argument count was exceeded.
   Use `*args` and `**kwargs` only for genuinely generic adapters or wrappers.

7. **Represent failures explicitly.**
   Raise specific exceptions for exceptional conditions. Do not use bare
   `except`, silently suppress errors, or use ambiguous sentinel values.
   `None` is acceptable for a legitimate optional result when expressed in
   the return type.

8. **Keep the core deterministic where practical.**
   Separate business decisions from network, database, filesystem, clock, and
   other external effects. Keep side effects at explicit boundaries so core
   behavior can usually be tested without mocks.

9. **Avoid speculative abstraction.**
   Prefer a small amount of clear duplication over an abstraction whose shape
   is not yet understood. Extract shared behavior once a stable pattern emerges.
   Do not add plugin, factory, or configuration layers for a single concrete use.

10. **Respect the repository's conventions deliberately.**
    Follow established patterns and libraries unless there is a concrete reason
    to improve them. Call out new dependencies, architectural patterns, and
    deliberate departures from existing conventions before introducing them.


