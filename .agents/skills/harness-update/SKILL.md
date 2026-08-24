---
name: harness-update
description: Detect evidence-backed drift between a managed repository's product sources, architecture, implementation, commands, tests, and Harness; produce a file-level sync plan and apply it only after explicit approval. Use when a Harness Armor managed repository changed or synchronization is requested. Do not use for dependency updates, unmanaged Harness audits, or any silent overwrite.
license: CC-BY-NC-4.0
compatibility: Requires repository file/search access. Python 3.9+ enables manifest validation, fingerprints, drift detection, and structure checks.
metadata:
  author: harness-armor
  version: "0.1.2"
---

# Update a managed Harness

Work in two phases: immutable read-only proposal, then an explicitly authorized
application. Invocation alone never authorizes writes.

## Inputs

- Repository root with a valid `.harness/manifest.json`.
- Optional scope or suspected change.
- A separate approval after the file-level proposal for any write phase.

## Load the contract

1. Treat this file's directory as `SKILL_ROOT`.
2. Read [references/authorization-and-drift.md](references/authorization-and-drift.md).
3. Resolve shared resources from `SKILL_ROOT/../../shared` or
   `SKILL_ROOT/../.harness-armor`.
4. Read `spec/harness-engineering-v1.md` and the state schemas. Stop if missing.

## Phase A — read-only proposal

1. Read applicable `AGENTS.md` files and `.harness/manifest.json`.
2. Run `python scripts/validate_manifest.py <root>`. If invalid, remain
   read-only and route to `harness-check`; do not repair state implicitly.
3. Run `python scripts/detect_drift.py <root>`.
4. Read every changed source and the affected code/config/test context. Hash
   differences prove change, not meaning.
5. Detect and classify:
   - product or design source changes;
   - current architecture/module changes;
   - build/test/CI command changes;
   - Harness content drift;
   - invalid or obsolete rules;
   - user edits to managed content;
   - missing, added, or deleted evidence.
6. Build [assets/update-plan.md](assets/update-plan.md) with evidence, affected
   files, proposed diff by file, ownership, risk, validations, and rollback.
7. Separate `NO_CHANGE`, `SAFE_MANAGED_UPDATE`, `USER_EDIT_CONFLICT`,
   `SOURCE_CONFLICT`, and `MANUAL_DECISION` items.
8. Stop and request explicit approval naming the plan version or exact files.
   Do not update timestamps, manifests, logs, or generated content before it.

## Authorization gate

Valid approval must clearly refer to the displayed plan. Scope it to the named
files and changes. General conversation, the original invocation, or approval
of one file does not authorize all proposed files. If evidence changes after the
plan, invalidate approval and regenerate the proposal.

## Phase B — authorized application

1. Re-check applicable files and hashes immediately before editing.
2. Apply only approved changes. Preserve user-owned content and edit only
   authorized managed sections when ownership is partial.
3. Never resolve a `USER_EDIT_CONFLICT` or `SOURCE_CONFLICT` unless the user's
   approval states the resolution.
4. Update Harness documents, then source fingerprints and manifest baselines.
5. Add a Chinese development log containing actual validations and manual
   acceptance steps.
6. Run `python scripts/validate_harness_structure.py <root>` plus the approved
   project checks.
7. Re-run drift detection. Remaining drift must be explained, not hidden.
8. On validation failure, revert only edits made by this phase when their
   pre-edit bytes are known and unchanged by others. Otherwise stop and report
   the precise partial state.

## Allowed and forbidden changes

Allowed only after approval: named Harness-owned files/sections, state
fingerprints, and a development log. Business code can be read but not modified.

Forbidden: unapproved writes, silent overwrite, requirement reduction, source
document rewriting, automatic refactoring, unknown script execution, or
claiming unrun tests passed.

## Idempotency and failures

An unchanged managed repository returns `NO_CHANGE` and writes nothing. Report
invalid manifests, scan truncation, unreadable sources, changed evidence after
approval, concurrent edits, and incomplete validation. Use `harness-check` for
diagnosis when safe application is not possible.

## Result format

Phase A reports the immutable plan and approval request. Phase B reports the
approved scope, actual diffs, preserved conflicts, manifest/log updates, command
evidence, residual drift, rollback status, and manual acceptance steps.
