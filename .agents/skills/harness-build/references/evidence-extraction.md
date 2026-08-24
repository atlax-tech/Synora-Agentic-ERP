# Document evidence extraction

## Source order is evidence, not authority

Read all relevant sources. A newer timestamp may be useful evidence, but does
not silently override a contractual requirement. Record declared source
precedence when the repository provides one; otherwise preserve conflicts.

## Extraction rules

- Copy identifiers and requirement meaning, not large verbatim passages.
- Use path plus heading or stable locator.
- Split compound requirements when their verification differs.
- Keep business rules separate from proposed implementation.
- Record negative requirements and prohibited degradation explicitly.
- Treat visual examples as design evidence only after inspecting them with an
  appropriate host tool.

## Architecture boundary

In a docs-only repository, architecture may be required, proposed, or unknown.
Do not write framework/module diagrams as current implementation. Label
evidence-backed design constraints `CONFIRMED`, reasoned choices `INFERRED`, and
choices awaiting a decision `UNRESOLVED`.

