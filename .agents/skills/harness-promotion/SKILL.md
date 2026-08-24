---
name: harness-promotion
description: Inspect real code, configuration, tests, CI, and documentation in an existing non-managed repository, then add a truthful Harness Engineering layer without refactoring behavior. Use when promoting a legacy Node, Python, Java, or other codebase for safe AI-agent work. Do not use for empty/docs-only repositories, deployment promotion, managed-Harness sync, or automatic code modernization.
license: CC-BY-NC-4.0
compatibility: Requires repository file/search access. Python 3.9+ enables bounded scans, fingerprints, and structure validation.
metadata:
  author: harness-armor
  version: "0.1.2"
---

# Promote an existing codebase

Document the repository as it actually works so future agents can change it
safely. Recommendations remain recommendations; this skill does not refactor.

## Inputs

- Repository root containing business code.
- Optional target modules or known risk areas.
- Existing instructions and documentation, which must be preserved unless a
  specific managed-section edit is approved.

## Load the contract

1. Treat this file's directory as `SKILL_ROOT`.
2. Read [references/code-evidence.md](references/code-evidence.md).
3. Resolve shared resources from `SKILL_ROOT/../../shared` or
   `SKILL_ROOT/../.harness-armor`.
4. Read `spec/harness-engineering-v1.md`, schemas, and templates. Stop if the
   shared runtime is absent.

## Applicability gate

1. Read every applicable `AGENTS.md` or client guidance file.
2. Run `python scripts/scan_repository.py <root>`.
3. Confirm business code exists and a valid Harness Armor manifest does not.
4. Route empty repositories to `harness-init`, docs-only repositories to
   `harness-build`, managed repositories to `harness-update` or
   `harness-check`, and mixed/conflicted structures to a read-only check.

## Evidence pass

1. Inventory languages, package/build files, entry points, primary modules,
   tests, CI/CD, containers, migrations, deployment configuration, and existing
   instructions.
2. Read actual entry points and representative core modules. Do not infer the
   architecture from directory names.
3. Establish evidence for:
   - current architecture and business flows;
   - module boundaries and dependency direction;
   - build, start, test, lint, and migration commands;
   - database and state handling;
   - external services and environment variable names, never secret values;
   - CI/CD behavior and manual release steps;
   - high-risk areas and existing change conventions.
4. Compare README/design claims with code and configuration. Use the columns in
   [assets/reality-ledger.md](assets/reality-ledger.md): code fact, document
   claim, inference, and recommendation.
5. Run only commands whose source and effect are understood, safe, and relevant.
   A command printed in README is not automatically trusted. Never run deploy,
   migration, destructive, credential, or production commands here.
6. Fingerprint the final evidence set with
   `python scripts/fingerprint_sources.py <root> <paths...>`.

## Harness plan and authorization

1. Propose a file-level plan for a short `AGENTS.md`, focused docs, state files,
   and any managed sections.
2. An explicit promotion request authorizes creation of missing Harness files.
   Editing an existing instruction or document requires a displayed diff plan
   and explicit approval. Prefer links and additive managed sections over
   replacement.
3. Mark all business code, configuration, tests, and current documentation
   `observed` or `user`; this workflow never owns them.

## Write workflow

1. Document current product behavior and architecture with source paths and
   symbols. Label gaps and conflicting statements.
2. Record verified commands and the evidence used to verify them. If a command
   was discovered but not run, say `DISCOVERED, NOT RUN`.
3. Define safe change boundaries, required validations, and manual acceptance
   for high-risk paths.
4. Preserve existing norms. Add proposed improvements in a clearly labeled
   recommendation section; never describe them as implemented.
5. Write managed state and source fingerprints.
6. Validate using `python scripts/validate_harness_structure.py <root>` plus
   safe project checks that actually ran.
7. Re-check the diff for any business-code or external-behavior change. If one
   exists, revert only that unauthorized edit and report it.

## Allowed and forbidden changes

Allowed: new Harness documents/state and explicitly approved managed sections.

Forbidden: business-code refactors, dependency updates, command changes,
configuration normalization, deployment, migration execution, secret reading,
invented architecture, or claims of unrun success.

## Failure handling

If the codebase is too large for configured limits, narrow the scan transparently
and list excluded areas. If architecture evidence conflicts, preserve the
conflict and route follow-up diagnosis to `harness-check`. On validation failure,
leave user files unchanged and report partial new files exactly.

## Result format

Report inspected evidence, current architecture summary, command status,
documentation/code conflicts, created/approved files, preserved files,
recommendations not implemented, actual validations, and manual acceptance
steps. Recommend `harness-prompt` only when a concrete implementation plan is
present.
