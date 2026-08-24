# Repository state and routing reference

## Precedence

1. A present and valid Harness Armor manifest establishes `MANAGED_HARNESS`.
2. A malformed managed state or directly conflicting evidence establishes
   `MIXED_OR_CONFLICTED`.
3. Business code without a complete Harness establishes `LEGACY_CODE`, even
   when ordinary project documentation is present.
4. Product/design/requirements sources without business code establish
   `DOCS_ONLY`.
5. A coherent non-Harness-Armor agent guidance system establishes
   `CUSTOM_HARNESS`.
6. No substantive files, or only README/LICENSE/.gitignore/editor basics,
   establishes `EMPTY`.

A substantive README alone remains `EMPTY` unless it defines enough product
requirements to act as the product source. When that judgment is uncertain,
show the uncertainty and use `MIXED_OR_CONFLICTED` rather than guessing.

Custom layouts do not need Harness Armor filenames. Deterministic evidence for
a coherent custom Harness requires an agent entry file, architecture evidence,
verification or quality-gate evidence, and at least one product, continuity, or
Harness-overview source. Partial guidance around business code remains
`LEGACY_CODE` with an uncertainty instead of being promoted by file count.

## Managed routing intent

- Words such as sync, update Harness, changed architecture, changed commands,
  or drift select `harness-update`.
- Words such as audit, score, health, consistency, or check select
  `harness-check`.
- A bare router call defaults to the safer read-only `harness-check`.

## Handoff contract

A handoff contains the state, evidence paths, uncertainties, and the user's
original intent. It does not contain invented project conclusions or a copied
specialist workflow.
