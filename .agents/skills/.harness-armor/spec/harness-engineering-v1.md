# Harness Engineering specification v1.0.0

This file is the sole normative Harness Armor contract. Skills may summarize
it, but must link here for the complete rules.

## 1. Knowledge placement

1. Put durable project knowledge in the repository.
2. Keep `AGENTS.md` short: identify the project, map authoritative documents,
   name verified commands, and state critical change boundaries.
3. Put product, architecture, design, development, testing, acceptance,
   decisions, and development history in focused files under `docs/` when those
   topics are material.
4. Do not duplicate prose merely to make the Harness look complete. Link to an
   existing authority or record a gap.

## 2. Evidence and epistemic status

Every project-specific conclusion must be one of:

- `CONFIRMED`: supported by a cited document, code location, configuration, or
  observed command output.
- `INFERRED`: reasoned from named evidence; record the reasoning and uncertainty.
- `UNRESOLVED`: evidence is absent or insufficient.
- `CONFLICTED`: authoritative sources disagree.

Never invent project facts, silently resolve a conflict, reduce a requirement
for implementation convenience, or present a recommended future architecture
as the current architecture.

## 3. Traceability

Trace product requirements through architecture or design decisions to code,
tests, and acceptance evidence where those artifacts exist. Preserve the
source path and, when useful, a stable heading, line, symbol, or digest. Missing
links are reported as gaps; they are not filled with guesses.

## 4. Agent change protocol

Before changing a repository, the host agent must:

1. Read applicable `AGENTS.md` files and the documents relevant to the task.
2. Scan repository state with documented exclusions and limits.
3. Build a fact list with epistemic statuses and sources.
4. State the intended file-level change set and verification plan.
5. Cross an explicit authorization gate when the selected skill requires one.

During and after changes, the host agent must:

1. Stay within the authorized file perimeter.
2. Preserve user-owned content and managed-section boundaries.
3. Avoid modifying business code from Harness-only workflows.
4. Run proportionate validation and report actual output.
5. Record unresolved items, conflicts, and unrun checks.
6. Provide repeatable manual acceptance steps.

## 5. Managed state

A managed Harness uses `.harness/manifest.json`, `source-index.json`, and
`unresolved.json`, validated by the versioned schemas. The manifest records
managed paths, ownership mode, specification version, generator version, and
state links. Source fingerprints are evidence of change, not semantic truth.

Ownership modes:

- `managed`: created and wholly maintained by an authorized Harness workflow.
- `managed-section`: only explicitly marked regions may be updated.
- `observed`: read for evidence but never owned or overwritten.
- `user`: explicitly user-owned; proposals may reference it but may not replace it.

When a managed file differs from its recorded fingerprint, treat it as a user
change until evidence shows otherwise. Propose a diff; do not silently replace.

## 6. Safety and validation

- Scanners are read-only by default, respect `.gitignore`, exclude secrets,
  dependencies, caches, and build outputs, and enforce file-count and byte limits.
- Never follow a symlink outside the repository root.
- Never run an unknown script solely because it appears in a repository.
- Never claim an unrun build or test passed.
- A placeholder, mock, fixture, or generated skeleton is not production
  completion unless the requirement explicitly asks for it.
- More files do not imply a healthier Harness. Score evidence, consistency,
  boundaries, verification, and updateability.

## 7. Updates and drift

Drift is any evidence-backed difference between recorded state and current
product documents, architecture, implementation, commands, tests, or Harness
content. Detection is read-only. A Harness update must present evidence,
affected files, a file-level plan, risk, and an authorization gate before
writing. After an authorized update, revalidate, refresh fingerprints, and add a
development log entry.

## 8. Prompt role separation

Generated engineering prompts separate three roles:

- `execute`: implement one scoped task and collect evidence.
- `test`: independently verify required behavior across normal, error, and edge
  paths without trusting the executor's self-report.
- `review`: inspect the original task, diff, evidence, architecture, regression,
  data, and security risks and return `PASS`, `CHANGES_REQUIRED`, or `BLOCKED`.

No role may collapse the other two into a self-attestation.

## 9. Compatibility

Public skill behavior relies only on the open Agent Skills structure and normal
host file/search/shell capabilities. Client metadata may improve discovery but
must not change the core workflow or become required for correctness.

