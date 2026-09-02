# Testing

Status: `CONFIRMED` strategy; the root format, lint, type, unit, integration, and runtime commands are verified and recorded in `docs/DEVELOPMENT.md`. Phase 8's implementation-specific evaluation and acceptance commands are now backed by the final evidence below; Phase 9 has not started.

## Test Layers

1. **Static and architecture checks** — formatting, lint, typing, upstream cleanliness, dependency direction, no direct database access, typed/risk-classified tools.
2. **Unit tests** — shortage and quantity calculations, risk classification, state transitions, policy, idempotency, and error mapping.
3. **Contract tests** — versioned tool input/output/error schemas and fail-closed parsing of model output.
4. **Integration tests** — real pinned ERPNext/Frappe behavior, permissions, DocType state, and mutations.
5. **Scenario/E2E tests** — natural-language request through approval, execution, verification, audit, and reconciliation.
6. **Component evaluations** — intent/router, tool selection, argument schema, plan, policy, memory/retrieval, groundedness, and final-answer checks.
7. **Trajectory evaluations** — Action/Observation order, evidence use, repetition, no-progress detection, stop reason, replanning, recovery, and handoff.
8. **Task evaluations** — end-to-end goal success, deterministic business correctness, refusal, human intervention, and final ERP state.
9. **System evaluations** — latency, token/cost, concurrency, long-run stability, security, observability, and maintainability.
10. **Failure and security tests** — timeout, rate limit, stale approval, state drift, duplicate request, ambiguous execution result, prompt injection, memory poisoning, unauthorized tools, and secret leakage.

## Test Data

- Use a deterministic seed company and minimal Supplier, Item, Warehouse, demand, and procurement data.
- Seed and cleanup operations must be idempotent.
- Do not use confidential production data.
- CI uses MockLLM or recorded deterministic responses; local-model evaluation is a separate reproducible suite.
- Assertions verify final ERP state, not only Agent text.
- Teaching labs and business-path implementations use the same golden task when making comparative claims. Lab completion does not replace real ERP integration evidence.
- Every Agent-pattern comparison stores implementation/version, fixed input, tool trace, stop reason, final result, metrics, and environment. LLM-as-Judge may assist but never decides safety or phase exit alone.

## Metrics

- End-to-end task success.
- Correct tool selection and valid arguments.
- Grounded-claim and citation coverage.
- Approval enforcement and unauthorized mutation count.
- Duplicate transaction count.
- Reconciliation success.
- Manual versus Agent-assisted user actions, page transitions, entered fields, and elapsed time.
- Loop/repetition/no-progress stop accuracy and unsupported final-answer rate.
- Same-task Direct/ReAct/Plan-and-Execute/framework comparison, including rejected alternatives.

Finite safety suites require 100% pass. Other thresholds must be set only after a baseline dataset exists.

## Independent Verification

The test and review roles do not trust the executor's self-report. Before release/version updates, an adversarial sub-agent examines the original requirement, diff, test evidence, architecture boundaries, regression risk, data handling, and security failure modes.

## Phase 8 final evidence (2026-09-03)

The immutable Coach instance is bound to source HEAD
`562fc42671004e12c3f3b6ee9266d0385e03b04a` and case-spec SHA
`264e42eeaf7a663cab9886d2b8ec05df3f55c7368502cff8647a607290d097a3`.
The manifest is
`output/phase8/phase8-manifest-562fc42.json` (SHA-256
`1c3e88b23aa410afb2eb70f21de1f67df518a3071de58ae1e4a014246a884e39`); the
representative and formal result files are the matching `562fc42` files in
`output/phase8/`.

- Fixed order: `G1,G2,G3,G4,C1,C2,C3,S1,S2,S3,U1,U2`; every case ran once,
  `12/12 PASS`.
- Scores: grounding `4/4`; citation `3/3` (positive `2/2`, safe refusal
  `1/1`); refusal/security `3/3`; usefulness `2/2`.
- Ten Provider-eligible cases have real usage (`provider_request_count=10`);
  S1/S2 are security bypasses and did not invoke a Provider.
- `mock_substitution=false`, `provider_tools_empty=true`,
  `erp_business_zero_write=true`, `repo_status_unchanged=true`,
  `secret_leak=false`, and `selective_rerun=false`; MR/PO/stock anchors are
  equal before and after.
- The Playwright role artifact
  `output/playwright/phase8-role-acceptance-562fc42.json` is sanitized and
  bound to the current manifest/backend evidence. Its binding mode explicitly
  preserves the historical capture HEAD and does not claim a fresh screenshot.

The final local checks were: `make format-check` (277 files), `make lint`,
`make type` (109 source files), and `make unit` (`676 passed, 2 warnings`),
all exit `0`; `compileall`, `git diff --check`, fixed Frappe/ERPNext SHA
cleanliness, manifest validation, reference validation, and Harness structure
validation also passed. The real Bench app-test ran directly and returned
`210 tests ... OK`. `make integration` was not run in this instance because
the user's unrelated dirty `env/dev/scripts/dev/env.sh` was preserved; the
direct Bench app-test is recorded as the executed equivalent, not as a claim
that the Make target ran.

The independent read-only Phase 8 review is an exit gate: only its final
`PASS` is accepted. Phase 9 is explicitly not started.

## Sources

- `docs/项目方向纠偏.md` — four-layer evaluation method and learning comparison requirements.
- `docs/PRD.md` — approved testability, product acceptance, RAG, Multi-Agent, and benchmark requirements.
- `docs/ARCHITECTURE.md` — trust, data ownership, dependency, and evolution boundaries requiring verification.
