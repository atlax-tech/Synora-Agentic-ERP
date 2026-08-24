# Testing

Status: `CONFIRMED` target strategy; product test commands are `UNRESOLVED` until code exists.

## Test Layers

1. **Static and architecture checks** — formatting, lint, typing, upstream cleanliness, dependency direction, no direct database access, typed/risk-classified tools.
2. **Unit tests** — shortage and quantity calculations, risk classification, state transitions, policy, idempotency, and error mapping.
3. **Contract tests** — versioned tool input/output/error schemas and fail-closed parsing of model output.
4. **Integration tests** — real pinned ERPNext/Frappe behavior, permissions, DocType state, and mutations.
5. **Scenario/E2E tests** — natural-language request through approval, execution, verification, audit, and reconciliation.
6. **Agent evaluations** — intent, tool selection, argument validity, plan completeness, groundedness, refusal, and recovery.
7. **Failure and security tests** — timeout, rate limit, stale approval, state drift, duplicate request, ambiguous execution result, prompt injection, unauthorized tools, and secret leakage.

## Test Data

- Use a deterministic seed company and minimal Supplier, Item, Warehouse, demand, and procurement data.
- Seed and cleanup operations must be idempotent.
- Do not use confidential production data.
- CI uses MockLLM or recorded deterministic responses; local-model evaluation is a separate reproducible suite.
- Assertions verify final ERP state, not only Agent text.

## Metrics

- End-to-end task success.
- Correct tool selection and valid arguments.
- Grounded-claim and citation coverage.
- Approval enforcement and unauthorized mutation count.
- Duplicate transaction count.
- Reconciliation success.
- Manual versus Agent-assisted user actions, page transitions, entered fields, and elapsed time.

Finite safety suites require 100% pass. Other thresholds must be set only after a baseline dataset exists.

## Independent Verification

The test and review roles do not trust the executor's self-report. Before release/version updates, an adversarial sub-agent examines the original requirement, diff, test evidence, architecture boundaries, regression risk, data handling, and security failure modes.

## Sources

- `docs/PRD.md` — approved testability, product acceptance, RAG, Multi-Agent, and benchmark requirements.
- `docs/ARCHITECTURE.md` — trust, data ownership, dependency, and evolution boundaries requiring verification.
