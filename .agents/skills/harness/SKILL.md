---
name: harness
description: Classify the current repository as empty, docs-only, legacy code, managed Harness, custom Harness, or mixed/conflicted; show evidence and route to the correct Harness Armor specialist. Use when the user asks where to start, invokes harness, or wants repository-state triage. Do not use for implementing a known init/build/promotion/update/check/prompt workflow or for ordinary test harnesses.
license: CC-BY-NC-4.0
compatibility: Requires repository file/search access. Python 3.9+ enables the bundled read-only detector.
metadata:
  author: harness-armor
  version: "0.1.2"
---

# Harness router

Act as the read-only entry point. Classify first, explain the evidence, and
handoff once. Do not implement a specialist workflow here.

## Inputs

- Repository root; default to the host's current repository.
- Optional stated intent such as audit, sync, initialize, promote, or generate
  prompts.
- No configuration is required.

## Load the contract

1. Treat the directory containing this file as `SKILL_ROOT`.
2. Read [references/routing.md](references/routing.md).
3. Resolve the shared root in this order:
   - `SKILL_ROOT/../../shared` for source or plugin layout;
   - `SKILL_ROOT/../.harness-armor` for a direct client installation.
4. Read `spec/harness-engineering-v1.md` from the resolved shared root. If
   neither layout exists, stop and report an incomplete installation; recommend
   `npx harness-armor doctor`.

## Workflow

1. Read applicable `AGENTS.md` files without modifying them.
2. Run `python scripts/detect_repository_state.py <root>` when Python is
   available. Otherwise perform the same bounded, read-only inventory with host
   file/search tools and say the helper was unavailable.
3. Inspect the reported evidence. The script is a candidate classifier, not a
   semantic authority. Read the minimum files needed to confirm:
   - whether substantive product documents exist;
   - whether business code exists;
   - whether `.harness/manifest.json` is valid;
   - whether custom agent guidance is coherent;
   - whether documents contain unresolved conflicts.
4. Select exactly one state. Keep `MIXED_OR_CONFLICTED` when evidence is
   ambiguous or contradictory; do not resolve conflicts for routing convenience.
5. If the user already named a valid specialist intent, hand off directly after
   confirming applicability. Do not ask them to repeat the request.
6. Otherwise route with this table:

   | State | Specialist |
   | --- | --- |
   | `EMPTY` | `harness-init` |
   | `DOCS_ONLY` | `harness-build` |
   | `LEGACY_CODE` | `harness-promotion` |
   | `MANAGED_HARNESS` | `harness-update` for explicit sync intent; otherwise `harness-check` |
   | `CUSTOM_HARNESS` | `harness-check` |
   | `MIXED_OR_CONFLICTED` | `harness-check` in read-only diagnostic mode |

7. Use a host-native Skill handoff when supported. Otherwise print the exact
   supported invocation and stop. Do not copy the specialist instructions into
   this run.

## Authorization and file boundary

This skill is always read-only. It may run bundled read-only scripts. It may not
create, edit, delete, rename, install, or normalize repository files. A request
to "fix" the result belongs to the routed specialist and its authorization gate.

## Forbidden behavior

- Do not infer repository state from a directory name or README alone.
- Do not call unknown repository scripts.
- Do not treat a file count as architecture evidence.
- Do not implement initialization, promotion, updates, checks, or prompts.
- Do not promise that a specialist ran when only a route was recommended.

## Validation and failures

- Confirm every state claim has at least one file-level reason.
- Report detector truncation, unreadable paths, missing Python, invalid state
  files, and semantic uncertainty.
- If state remains ambiguous, return `MIXED_OR_CONFLICTED` and route to a
  read-only check.

## Result format

Use [assets/state-report.md](assets/state-report.md). Include state, confidence,
evidence, uncertainties, selected specialist, invocation, and the fact that no
files changed. After a specialist later completes, recommend at most one next
step based on its actual result.
