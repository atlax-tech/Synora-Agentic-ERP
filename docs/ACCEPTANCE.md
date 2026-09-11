# Acceptance

Status: `CONFIRMED` product-level acceptance direction. Phase 8 is
`COMPLETED / PASS / READY FOR THE NEXT PHASE` as of 2026-09-03. Phase 9's
implementation, real acceptance, L3 evidence, independent review, and
README/Harness synchronization are complete as of 2026-09-04; its status is
`COMPLETED / PASS / READY FOR THE NEXT PHASE`. Phase 10's fixed isolated
development ERP implementation, real acceptance, L3 evidence, independent
review, and protected documentation synchronization are complete as of
2026-09-11; its status is `COMPLETED / PASS / READY FOR THE NEXT PHASE`.

## Product Acceptance Principles

A feature is not complete because a model produced a plausible answer. Completion requires:

- traceability to an approved requirement;
- implementation within architecture and permission boundaries;
- deterministic validation of all business mutations;
- normal, error, edge, recovery, and security evidence;
- verified final ERPNext state;
- updated development history and affected documentation;
- honest reporting of unrun or unresolved checks.

## Agent Learning Acceptance

For Phase 4–13, a topic is not complete because a framework demo runs. Acceptance requires:

- a plain-language principle explanation and a bounded user Assignment;
- a runnable minimal lab using an ERP/procurement task or an explicit domain-mismatch explanation;
- named open-source source comparison without overstating reading depth or execution evidence;
- a Synora business adaptation or a documented reason not to adopt it;
- success and failure traces, same-task evaluation, and explicit stop/failure reasons;
- an Adoption Card and interview questions; unanswered learner work stays `待练习`;
- clear separation between experimental evidence and deployed business behavior.

## First Governed-Write Acceptance

- A procurement goal produces an explainable, typed MR Draft or PO Draft proposal.
- Invalid, unauthorized, stale, duplicate, or unsupported proposals fail closed.
- No high-risk write executes without the required authenticated approval.
- Execution revalidates current ERP state and enforces idempotency.
- A successful write is read back and captured in an Execution Receipt.
- Ambiguous success enters reconciliation instead of blind retry.
- The run is auditable from request through receipt.

## Phase 10 P2P Acceptance

PO submission, Receipt, Invoice, and Payment Entry are accepted on the fixed
isolated `dev.localhost` development ERP after each action's permission,
accounting, state-transition, approval, idempotency, recovery, read-back, and
reconciliation evidence passed. Other ERP environments, stricter Workflow
mappings, and production deployment remain evidence-gated and fail closed when
their rules are missing, conflicting, stale, or unverifiable.

## Retrieval Acceptance

- Answers cite versioned sources.
- Unsupported claims are refused or marked unknown.
- Retrieval respects source and user authorization boundaries.
- Prompt injection in retrieved text cannot select tools, change policy, or authorize writes.
- Vector or hybrid retrieval is introduced only with a measured improvement over the FTS5 baseline.

## Release Acceptance

- Required checks pass and actual outputs are recorded.
- Independent adversarial review returns `PASS`.
- Upstream is unchanged.
- No documentation or README claim exceeds current evidence.
- Manual acceptance steps are repeatable.
- Required phase learning evidence is runnable and no lab or planned capability is presented as production adoption.

## Phase 8 final acceptance (2026-09-03)

- The final immutable Coach instance is bound to source HEAD
  `562fc42671004e12c3f3b6ee9266d0385e03b04a` and manifest
  `output/phase8/phase8-manifest-562fc42.json` (SHA-256
  `1c3e88b23aa410afb2eb70f21de1f67df518a3071de58ae1e4a014246a884e39`).
- The fixed order `G1,G2,G3,G4,C1,C2,C3,S1,S2,S3,U1,U2` passed once per case:
  `12/12`; grounding `4/4`, citation `3/3` (positive `2/2` and safe refusal
  `1/1`), refusal/security `3/3`, usefulness `2/2`. Ten eligible cases have
  real Provider usage; S1/S2 are security bypasses.
- The result records no mock substitution, ERP business write, Provider tool,
  Secret leak, selective rerun, or repository/ERP anchor drift.
- `output/playwright/phase8-role-acceptance-562fc42.json` is a sanitized
  binding artifact for Buyer, System Manager, and Viewer observations. It
  retains the historical browser capture HEAD and explicitly does not claim a
  fresh capture; current backend Coach, permission, and zero-write evidence
  are separately bound.
- Independent read-only review completed with `PASS` before this status was
  written. Phase 9 final acceptance is recorded below; Phase 10 acceptance is
  recorded in the following section.

## Phase 9 final acceptance (2026-09-04)

- The quality-first GLM v12 same-model A/B is the adoption evidence for Planner and Reviewer. Multi quality is non-regressing with strict valid/recovery improvement, controlled p95, complete safety checks, and a real Reviewer handoff; token totals remain audit data. qwen3:8b and Grok failures are retained and qwen3.8:27b was not called.
- MCP, A2A, and ANP protocol acceptance passed with stdio/loopback lifecycle and fail-closed checks. These remain `LAB_ONLY`; ANP is `NOT ADOPTED` because open-network discovery is not required by Phase 9.
- The real Buyer → Frappe → Runtime → GLM path passed with Viewer denial, System Manager redaction, revision/scope/fallback/invalid/timeout/cancellation handling, unchanged MR/PO/Bin anchors, and zero ERP business writes. The three role screenshots are bound to implementation HEAD `8b7ff1b`.
- L3 gates and the independent adversarial review passed. The authoritative report and manifest are `output/phase9/phase9-stage-report-draft-8b7ff1b.md` and `output/phase9/phase9-final-manifest-8b7ff1b.json`; their evidence commit is `a87f254`.
- At the time of the Phase 9 freeze, README and Harness updates were a separately approved synchronization step; the later R10.6 protected synchronization is recorded in the Phase 10 final manifest. No production deployment, customer adoption, or unmeasured general model-quality claim is made.

## Phase 10 final acceptance (2026-09-11)

- The fixed isolated development ERP completed the governed PO Submit → partial Receipt → partial Invoice → Payment Entry path. Each side-effecting Action carried an independent approval, current-state and permission recheck, reservation, idempotency key, ERP-native controller, `SUCCEEDED` Receipt, and successful read-back; unknown outcomes entered reconciliation instead of blind retry.
- The R10.4 process-fault matrix passed `28/28` cases across seven Action types and four fault locations. The real-login browser artifact covers role separation, unauthorized Viewer denial, cancellation/recovery, accounting/GL balance, and a retained Runtime `503 UNAVAILABLE` limitation.
- The final R10.5 gates were `make format-check`, `make lint`, `make type`, `make unit` (`856 passed`), and `make integration` (`248` Frappe tests) with exit code `0`; the two-round independent adversarial review returned `PASS`.
- The final report and machine manifest are `output/phase10/phase10-stage-report-final-80abbba.md` and `output/phase10/phase10-final-manifest-final-80abbba.json`. This acceptance is development-ERP evidence only; it does not claim production deployment, bank transfer, customer adoption, or general performance improvement.

## Sources

- `docs/项目方向纠偏.md` — approved learning evidence and same-repository business/lab boundary.
- `docs/PRD.md` — approved complete scope, non-functional requirements, acceptance summary, and benchmark boundaries.
- `docs/ROADMAP.md` — staged delivery order and milestone exit conditions.
