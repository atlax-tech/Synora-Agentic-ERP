---
name: harness-build
description: Read substantive product, requirements, design, and planning sources in a docs-only repository and build a project-specific, traceable Harness without business code. Use when the repository has product documents but no implementation, or when harness-build is invoked. Do not use for empty repositories, legacy code promotion, ordinary build commands, or managed-Harness updates.
license: CC-BY-NC-4.0
compatibility: Requires repository file/search access. Python 3.9+ enables bounded scans, fingerprints, and validation.
metadata:
  author: harness-armor
  version: "0.1.2"
---

# Build a product-specific Harness from documents

Turn real product sources into a usable engineering knowledge system while
keeping every conclusion traceable.

## Inputs

- Repository root containing substantive product sources and no business code.
- Optional user priorities or source precedence. Record these as explicit user
  evidence; do not silently choose a preferred document.

## Load the contract

1. Treat this file's directory as `SKILL_ROOT`.
2. Read [references/evidence-extraction.md](references/evidence-extraction.md).
3. Resolve shared resources at `SKILL_ROOT/../../shared` or
   `SKILL_ROOT/../.harness-armor`.
4. Read `spec/harness-engineering-v1.md`, `schemas/`, and `templates/` from that
   root. Stop if the installation is incomplete.

## Applicability gate

1. Read applicable `AGENTS.md` files.
2. Run `python scripts/scan_repository.py <root>` and inspect all likely product
   sources.
3. Confirm substantive product documentation exists and no actual business code
   exists. Generated snippets inside documents do not by themselves count as an
   implementation.
4. Route `EMPTY` to `harness-init`, code to `harness-promotion`, managed state to
   `harness-update` or `harness-check`, and conflicting repository structure to
   `harness-check`.

## Evidence extraction

1. Inventory and classify product, requirements, design, research, roadmap,
   acceptance, and constraint sources.
2. Read every relevant source, not only README.
3. Build the ledger in [assets/fact-ledger.md](assets/fact-ledger.md) for:
   product goals, users, scenarios, functional requirements, business rules,
   design requirements, technical constraints, non-functional requirements,
   acceptance conditions, and plan steps.
4. Mark each item `CONFIRMED`, `INFERRED`, `UNRESOLVED`, or `CONFLICTED`. Include
   source path and heading/locator for confirmed and conflicted items. Explain
   inference reasoning.
5. Preserve both sides of every conflict. Do not demote a required feature into
   a roadmap item or simplify a user journey to make implementation easier.
6. Run `python scripts/fingerprint_sources.py <root> <paths...>` for the final
   source set.

## Harness plan and authorization

1. Propose the file-level Harness plan: short `AGENTS.md`, only the necessary
   focused docs, `.harness` state, and decision/log maps.
2. An explicit build request authorizes creation of missing Harness files in the
   plan. It does not authorize overwriting any existing file. Present collisions
   and request approval for a managed section or alternate path.
3. Keep source documents user-owned. Never replace them with generated summaries.

## Write workflow

1. Write current product requirements without adding facts.
2. Write architecture and design documents that separate required constraints,
   evidence-backed inferences, open decisions, and recommendations.
3. Write development, testing, acceptance, and roadmap documents only to the
   level supported by evidence. Unknown commands stay unresolved.
4. Write a concise `AGENTS.md` knowledge map with source-of-truth pointers and
   verified commands only.
5. Write `.harness/manifest.json`, `source-index.json`, and `unresolved.json`.
   Record source digests and ownership modes.
6. Validate with `python scripts/validate_harness_structure.py <root>` and the
   repository's safe document checks, if known.
7. Re-read generated conclusions against the ledger. Any unsupported statement
   becomes `INFERRED` or `UNRESOLVED`, not a polished fiction.

## Allowed and forbidden changes

Allowed: new or explicitly approved Harness files and managed sections.

Forbidden: business code, dependency manifests, product-source rewrites,
unapproved overwrites, feature reduction, invented stack or commands, and
claims that an unrun check passed.

## Failure handling

On missing sources, unreadable files, conflicts, or validation failure, preserve
the ledger and report what is blocking. Roll back only untouched files created
by this run. A second unchanged run must propose no churn; use `harness-check` to
assess the resulting Harness.

## Result format

Report source inventory, status counts, generated/updated/preserved files,
conflicts, unresolved items, actual validation evidence, and manual acceptance
steps. Recommend `harness-prompt` only when an actionable plan now exists.
