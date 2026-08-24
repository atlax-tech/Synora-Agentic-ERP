---
name: harness-prompt
description: Generate source-grounded execute, test, and review engineering prompts for every step in an existing implementation plan, with independent roles and explicit file/verification boundaries. Use when a repository already has product facts, Harness constraints, and a real roadmap or plan. Do not use to improve generic chat prompts, invent a plan, implement code, or let executors self-approve.
license: CC-BY-NC-4.0
compatibility: Requires repository file/search access. Python 3.9+ enables source fingerprint and Harness structure checks.
metadata:
  author: harness-armor
  version: "0.1.2"
---

# Generate independent engineering prompts

Create three role-specific contracts per real plan step: execute, test, and
review. The prompt files coordinate agents; they do not perform the work.

## Inputs

- Repository root with product definition and applicable Harness constraints.
- A named implementation plan, roadmap slice, or ordered set of steps.
- Optional output plan name; otherwise derive a safe kebab-case name from the
  source plan title.

## Load the contract

1. Treat this file's directory as `SKILL_ROOT`.
2. Read [references/prompt-contract.md](references/prompt-contract.md).
3. Resolve shared resources from `SKILL_ROOT/../../shared` or
   `SKILL_ROOT/../.harness-armor`.
4. Read `spec/harness-engineering-v1.md`. Read the local templates
   [assets/execute.md](assets/execute.md), [assets/test.md](assets/test.md),
   [assets/review.md](assets/review.md), and
   [assets/prompt-index.md](assets/prompt-index.md).

## Applicability gate

1. Read applicable `AGENTS.md` and authoritative product/architecture/testing
   documents.
2. Locate a concrete ordered plan. Confirm steps have a real source, intended
   outcome, and enough project context to define boundaries.
3. If the plan is absent or only aspirational themes exist, stop with
   `UNRESOLVED`; do not invent implementation steps. Recommend the appropriate
   planning workflow.
4. If product or Harness constraints are missing/conflicted, route to
   `harness-build`, `harness-promotion`, or `harness-check` before prompt
   generation.

## Source pass

1. For each plan step, collect the exact plan source, related product
   requirements, architecture/design constraints, allowed code area, forbidden
   changes, tests, acceptance behavior, and dependencies on other steps.
2. Mark unsupported assumptions `UNRESOLVED` inside the prompt. Never turn them
   into instructions to implement a guessed feature.
3. Fingerprint key sources with
   `python scripts/fingerprint_sources.py <root> <paths...>` and record the
   source digest or locator in `PROMPT_INDEX.md`.

## Output plan and authorization

1. Propose this exact tree:

   ```text
   docs/prompts/<plan-name>/
   ├── PROMPT_INDEX.md
   ├── step-001/
   │   ├── execute.md
   │   ├── test.md
   │   └── review.md
   └── ...
   ```

2. An explicit generation request authorizes creation of a new prompt tree.
   Existing prompt files are user-owned: do not overwrite them. Offer a new
   versioned plan name or request exact-file approval.

## Generation workflow

1. Create one directory per source plan step in original order. Keep each step a
   single task; split a source step only when its own acceptance evidence
   requires independent work, and record the mapping.
2. Render `execute.md` with required reading, current facts, allowed and
   forbidden files, functional and technical constraints, non-degradation
   rules, verification, completion evidence, small Conventional Commits, and
   final report format.
3. Render `test.md` as an independent verification task: requirement sources,
   expected behavior, normal/error/edge cases, unit/integration/end-to-end scope,
   manual acceptance, failure evidence, and verdict. Do not trust executor prose.
4. Render `review.md` with original task, constraints, expected diff scope,
   actual test evidence, regression/architecture/data/security risks, severity,
   and exactly one verdict: `PASS`, `CHANGES_REQUIRED`, or `BLOCKED`.
5. Make roles independent. Execute may not declare review success; test may not
   repair implementation; review may not substitute self-authored evidence for
   an inspected diff and test results.
6. Create `PROMPT_INDEX.md` mapping every step and prompt to source locators,
   prerequisites, outputs, and current unresolved items.
7. Run local reference checks and inspect every generated prompt for unresolved
   placeholders, cross-step leakage, and invented facts.

## Allowed and forbidden changes

Allowed: a new approved `docs/prompts/<plan-name>/` tree.

Forbidden: business code, source plan edits, existing prompt overwrite,
generated commits, execution/testing/review actions, collapsed roles, invented
requirements, or unsupported client-specific control syntax.

## Idempotency and failures

If the identical prompt tree already exists and sources are unchanged, write
nothing. If sources drift, report regeneration as a proposal; preserve user
edits. On incomplete plan, conflict, permission failure, or broken references,
report the exact step and source evidence and do not claim a complete set.

## Result format

Report plan source, step count, created/preserved files, source mapping,
unresolved items, role-separation checks, reference validation, and manual
acceptance steps. State explicitly that no implementation, tests, or review were
performed by generating the prompts.
