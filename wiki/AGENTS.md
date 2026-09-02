# Wiki Writing Instructions

The `wiki/` directory contains reviewer-facing documentation. Write it for a human reader who wants to understand the system's core ideas without reading the implementation first.

## Style

- Use plain, direct language and define a technical term the first time it appears.
- Explain one idea at a time, in a clear order, as if writing a short introductory book.
- Prefer short paragraphs, small tables, and one representative example.
- Focus on the normal path and the decisions a reviewer needs to understand.
- Include edge cases only when they change the core model or prevent a serious misunderstanding.
- Avoid exhaustive field-by-field commentary, speculative future designs, and internal details that do not help explain the concept.
- Introduce database tables by their role in the data flow and a concrete example before exposing column names. Keep exhaustive schemas in technical specifications and link to them from the wiki.
- When documenting a relational model, pair the plain-language explanation with a compact visual schema that shows the main tables, keys, and joins. Do not make the reader reconstruct the table structure from prose alone.
- Use diagrams only when they make a relationship or flow easier to understand than prose.
- In process diagrams, show every material intermediate step and keep independent flows visibly separate. Label arrows with the concrete action or data they represent; avoid vague links whose meaning requires the reader to guess.

## Accuracy and scope

- Verify entity names, relationships, schemas, and behavior against the repository before documenting them.
- When documenting optional AI integrations, explain their purpose without publishing local enablement or disablement state.
- Clearly distinguish what exists today from what is planned; do not present a design contract as implemented code.
- Keep examples consistent with the synthetic dataset and label assumptions as assumptions.
- Preserve important distinctions such as entity versus identifier, evidence versus inference, and confirmed versus proposed.
- Link to deeper design or specification documents when detail is useful instead of copying that detail into the wiki.
- Update an existing page when it already owns the topic; avoid creating competing explanations of the same concept.
- Prefer the smallest model a reviewer can explain back confidently. Put prototype-grade behavior in the main narrative and move production machinery to an explicit evolution section instead of expanding the core mental model.

## Learning from feedback

When the user gives feedback about wiki documentation, consider whether it reveals a reusable preference about how the wiki should be written or presented. If it does, update this file with a short, general instruction so the preference is applied in future sessions.

Do not record every correction or document-specific preference. Add a rule only when the feedback can reasonably guide multiple wiki pages and directly affects the user's preferred documentation style, clarity, structure, or level of detail.

## Structure

A page should normally contain:

1. A short statement of purpose.
2. The core concept or model.
3. The minimum example needed to make it concrete.
4. The implementation mapping, when relevant.

Stop when the reader can explain the core idea back in their own words. Completeness is not the goal; clear understanding is.
