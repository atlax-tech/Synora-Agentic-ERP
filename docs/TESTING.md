# Testing

Status: `CONFIRMED` strategy; the root format, lint, type, unit, integration, and runtime commands are verified and recorded in `docs/DEVELOPMENT.md`. Phase 8's implementation-specific evaluation and acceptance commands remain backed by the final evidence below. Phase 9's implementation-specific evidence and independent review passed on 2026-09-04; its final public/Harness synchronization is pending explicit approval.

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

## Phase 9 final evidence (2026-09-04)

The evidence is bound to implementation HEAD `8b7ff1b1dc51449b51f0335ed63ae2c34bc5772e` and evidence commit `a87f254c2339ee9253d4c0802b38e4b9dcfb7103`. The fixed case-spec SHA is `05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`.

- P9.5 real GLM v12 quality-first A/B: single `7/12, 8/12, 7/12`, multi `8/12, 9/12, 8/12`; multi p95 `9598 ms` versus single `16388 ms`; security counters are all zero. Planner and Reviewer Adoption Cards are `ADOPT`; token totals `11051` versus `5700` are recorded and are not a veto. The later v13 stochastic failure remains preserved.
- P9.6–P9.8 formal protocol acceptance: MCP stdio, real loopback TCP A2A lifecycle/cancel/error/timeout/race, and fixed ANP discovery/rejection all `PASS`; ERP business writes `0`.
- P9.9 real acceptance: Buyer’s real GLM Planner → Reviewer path is `ACCEPTED`; Viewer is denied, System Manager receives only a redacted summary, controlled recovery paths pass, and ERP anchors are unchanged with business writes `0`. Buyer, Viewer, and System Manager screenshots are bound to the same implementation HEAD.
- P9.10: format-check, lint, type, unit (`843 passed`), integration (`210 tests OK`), focused Phase 9 suite (`44 passed`), artifact/zero-write, lock/compile/import, upstream SHA/dirty, ponytail, and Harness manifest/structure/reference checks exited `0`; `detect_drift.py` exited `1` only for the documented pre-sync managed-document drift.
- The independent adversarial review returned `PASS`. Full digests, commands, risks, rubric, unrun items, and provider search order are in `output/phase9/phase9-final-manifest-8b7ff1b.json` and the stage report draft.

## Sources

- `docs/项目方向纠偏.md` — four-layer evaluation method and learning comparison requirements.
- `docs/PRD.md` — approved testability, product acceptance, RAG, Multi-Agent, and benchmark requirements.
- `docs/ARCHITECTURE.md` — trust, data ownership, dependency, and evolution boundaries requiring verification.
