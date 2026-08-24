---
name: harness-init
description: Create a fact-safe Harness Engineering skeleton for an empty or basic-files-only repository, including a short AGENTS.md, project document stubs, and managed state. Use when starting an undefined repository or explicitly invoking harness-init. Do not use when substantive product documents or business code already exist, and never guess the product or technology stack.
license: CC-BY-NC-4.0
compatibility: Requires repository file access. Python 3.9+ enables bundled state and structure validation.
metadata:
  author: harness-armor
  version: "0.1.2"
---

# Initialize an undefined repository

Create a useful knowledge skeleton without filling unknowns with plausible
fiction.

## Inputs

- Repository root.
- Optional project name or known purpose supplied by the user.
- Existing README, LICENSE, `.gitignore`, and editor files are preservation
  constraints, not blank space.

## Load the contract

1. Treat the directory containing this file as `SKILL_ROOT`.
2. Read [references/init-policy.md](references/init-policy.md).
3. Resolve `shared` from `SKILL_ROOT/../../shared` or, for direct installs,
   `SKILL_ROOT/../.harness-armor`.
4. Read `spec/harness-engineering-v1.md` and the templates under
   `templates/`. Stop with an installation error if the shared root is absent.

## Applicability gate

1. Read applicable `AGENTS.md` instructions if any exist.
2. Run `python scripts/detect_repository_state.py <root>`.
3. Continue only for `EMPTY`, including repositories containing only basic
   administrative files.
4. If the state is `DOCS_ONLY`, stop and route to `harness-build`. For code,
   route to `harness-promotion`. For any Harness or conflict, route to
   `harness-check`.

## Fact ledger

Before writing, list every supplied fact and every required unknown. Use
`CONFIRMED` only for user statements or existing file evidence. Mark everything
else `UNRESOLVED` using the exact marker in
[assets/unresolved-marker.md](assets/unresolved-marker.md). Do not infer users,
features, language, framework, database, deployment, or commands.

## Write workflow

1. Propose the new-file set. The default set is:
   - `AGENTS.md`;
   - `docs/PRD.md`, `ARCHITECTURE.md`, `DESIGN.md`, `DEVELOPMENT.md`,
     `TESTING.md`, `ACCEPTANCE.md`, and `ROADMAP.md`;
   - `.harness/manifest.json`, `source-index.json`, and `unresolved.json`.
2. Treat an explicit request to initialize as authorization to create only
   missing files in that set. If any target exists, do not overwrite it; show
   the collision and request a narrower decision.
3. Create a short `AGENTS.md` knowledge map from the shared template.
4. Create focused document skeletons. Include only confirmed facts and explicit
   unresolved markers. Do not create empty filler sections merely to increase
   coverage.
5. Create the state files with ownership for files actually created. Record
   pre-existing files as `user` or `observed`, never `managed`.
6. Create `docs/decisions/README.md` and
   `docs/development-log/README.md` only when the client cannot preserve empty
   directories and those maps add useful instructions.
7. Run `python scripts/validate_harness_structure.py <root>` and relevant local
   reference checks.
8. If validation fails, remove only newly created files from this run when safe;
   otherwise report the partial state exactly. Never alter pre-existing files
   during cleanup.

## Allowed changes

Create missing Harness files and directories listed in the approved plan. Do not
modify existing repository files, business code, package metadata, CI, or Git
configuration.

## Forbidden behavior

- Do not create an implementation, dependency manifest, framework scaffold, or
  pretend build/test command.
- Do not rewrite README or LICENSE.
- Do not mark unresolved placeholders `CONFIRMED`.
- Do not silently replace an earlier partial Harness.

## Idempotency and failure handling

A second run on an unchanged initialized repository must produce no file diff;
route it to `harness-check`. Report collisions, permission failures, incomplete
runtime, truncated scans, and unrun validators. Never claim initialization
succeeded when structure validation did not complete.

## Result format

Report: applicability evidence, confirmed facts, unresolved items, files
created, files preserved, validation commands with actual results, and manual
acceptance steps. End by recommending that the user fill `docs/PRD.md` and
then invoke `harness-build` once substantive product sources exist.
