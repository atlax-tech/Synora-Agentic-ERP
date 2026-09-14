# Roadmap

Status: `CONFIRMED` staged learning and delivery plan. Synora is an Agent-development learning repository and a real ERP practice vehicle. The business application layer and teaching lab live in one repository and one development line; staging does not remove complete business requirements. Phase 8 is `COMPLETED / PASS / READY FOR THE NEXT PHASE` (2026-09-03). Phase 9 is `COMPLETED / PASS / READY FOR THE NEXT PHASE` as of 2026-09-04. Phase 10 is `COMPLETED / PASS / READY FOR THE NEXT PHASE` as of 2026-09-11 for the fixed isolated development ERP path; later phases remain staged.

## Phase 0 — Governance Bootstrap

- Install and validate project-level Harness and required Skills.
- Establish AGENTS, PRD, architecture/design, development, test, acceptance, README, and specification authorities.
- Establish development logging, small commits, independent review, and release gates.

Exit: files are mutually consistent; declared commands and Skill discovery are verified or explicitly unresolved.

## Phase 1 — ERP Baseline and Business Archaeology

- Run an unmodified Frappe/ERPNext v16 pair.
- Create deterministic test master data.
- Manually complete MR -> PO -> Receipt -> Invoice and observe Payment status.
- Pin exact Frappe and ERPNext commits after runtime verification.
- Build evidence-backed P2P source and invariant maps.

Exit: baseline is repeatable; no Synora mutation code exists; core business objects and transitions are explainable.

## Phase 2 — Typed Read-only ERP Gateway

- Implement projected stock, open demand, open MR/PO, supplier, and item queries.
- Enforce authorization, typed responses, source snapshots, and structured errors.

Exit: contract, integration, permission, and architecture tests pass; runtime has no ERP database or internal-import path.

## Phase 3 — Read-only Procurement Agent Baseline

- Implement goal input, constrained context acquisition, deterministic shortage calculations, explainable planning, BYOK enhancement, and FTS5 retrieval.
- Preserve Phase 3 as the deterministic ERP, permission, typed-tool, and evaluation baseline for later Agent comparisons.

Exit: normal, ambiguous, unauthorized, tool-failure, and malicious-content cases are repeatable without writes.

## Phase 4 — Agent Execution Kernel and Native Tool Calling

- Build and compare Direct, bounded ReAct, Plan-and-Solve, Reflection, a small multi-step executor, and provider-native tool calling.
- Let the model dynamically choose only existing authorized read tools; keep quantities and thresholds deterministic.
- Record Action/Observation traces and bounded stop reasons for steps, repetition, no progress, tokens, cost, and wall time.

Exit: one real task selects a second tool from the first observation; loop attacks stop with an explicit reason; implementations are compared on the same golden tasks.

## Phase 5 — Durable Workflow and Plan-and-Execute

- Add typed plans, dependencies, replanning, clarification, checkpoint, interrupt, resume, cancel, expiry, and crash recovery.
- Compare fixed workflows, inner ReAct, Plan-and-Execute, low-code orchestration, and explicit state graphs.
- Keep checkpoint state separate from Frappe business facts and authorization.

Exit: a Run resumes from the last safe point without repeating completed tools; cancellation and expiry stop further work; workflow choices have measured quality/cost evidence.

## Phase 6 — First Governed ERP Actions

- Resolve `approval-workflow-mapping` before enabling writes.
- Add ProposedAction, PolicyDecision, ApprovalDecision, ExecutionReceipt, idempotency reservation, read-back, and reconciliation.
- Enable MR Draft, then PO Draft, through deterministic execution after current-state revalidation and human authorization.

Exit: Buyer goal -> Agent investigation -> proposal -> human decision -> one verified ERP Draft -> receipt works over the real integration path; unsafe or ambiguous writes fail safely.

## Phase 7 — Prompt, Context Engineering, and Skills

- Version prompts and implement Gather/Select/Structure/Compress context assembly with explicit token budgets.
- Build procurement Skills with provenance, progressive disclosure, allowed tools, and regression evidence.
- Compare Prompt, Tool, Skill, Workflow, and MCP responsibilities on the same tasks.

Exit: Prompt/Context/Skill versions are reproducible; compression preserves safety; Skills cannot expand the active capability allowlist.

## Phase 8 — Memory, RAG, and Contextual ERP Coach — COMPLETED / PASS

- Implement scoped working, episodic, semantic, and procedural memory with candidate review, expiry, correction, and deletion.
- Compare FTS5/BM25, vector, hybrid, and reranking on a fixed dataset.
- Add a cited Contextual ERP Coach that re-queries live ERP facts rather than trusting memory.

Exit: authorized experience and SOP knowledge can be recalled with provenance; stale, cross-user, and injected memory or retrieval content fails safely. Final evidence is recorded in `docs/PLAN.md`, `docs/TESTING.md`, `docs/ACCEPTANCE.md`, and `output/phase8/`; Phase 9 and Phase 10 closure evidence are recorded in their respective sections and output directories.

## Phase 9 — Multi-Agent, MCP, and A2A

- The runnable pattern comparison, typed Planner -> Policy/Risk Reviewer handoff, bounded exception path, local MCP, localhost A2A, and fixed ANP descriptor lab are complete. The protocol labs remain `LAB_ONLY`; ANP is `NOT ADOPTED` because this phase has no open-network discovery requirement.
- GLM `assist/glm-5.3-flash` is the first quality-first same-model A/B candidate with Planner and Reviewer `ADOPT`. The quality-first profile requires multi-agent quality non-regression, at least one strict quality improvement, controlled p95 latency, and 100% safety; token usage remains recorded evidence and is not an adoption veto. qwen3:8b and Grok failure artifacts are retained, and qwen3.8:27b was not called.
- The real Frappe → Runtime → GLM path, Buyer/Viewer/System Manager permissions, controlled recovery cases, browser evidence, and ERP zero-write checks are complete. The bound report and manifest are `output/phase9/phase9-stage-report-draft-8b7ff1b.md` and `output/phase9/phase9-final-manifest-8b7ff1b.json`; independent adversarial review returned `PASS`.

Exit: `COMPLETED / PASS / READY FOR THE NEXT PHASE`; adopted roles show measured quality benefit over the same-model single-Agent baseline, and rejected roles retain runnable experiments and evidence explaining the decision.

## Phase 10 — Complete P2P Operating Agent — COMPLETED / PASS

- Extend governed execution through PO Submit, Receipt, Invoice, and Payment Entry on the fixed isolated development ERP.
- Cover partial receipt/billing, cancellation, accounting controls, state drift, process recovery, reconciliation, and human takeover.
- Use the execution, workflow, context, memory, and multi-Agent capabilities proven in earlier phases; keep unknown outcomes fail-closed.
- Bind the implementation, real ERP/browser/fault evidence, full validation, independent review, and protected documentation sync to `output/phase10/phase10-stage-report-final-80abbba.md` and `output/phase10/phase10-final-manifest-final-80abbba.json`.

Exit: `COMPLETED / PASS / READY FOR THE NEXT PHASE`; complete P2P requirements have real implementation and acceptance evidence in the fixed isolated development ERP, no stage is silently dropped, and no production or customer-adoption claim is made.

## Phase 11 — Web/GUI Agents and Multimodal Observation

- Build bounded Web and GUI Agent experiments covering DOM/visual observation, action selection, login state, accessibility, and asynchronous pages.
- Evaluate whether UI operation is justified when stable typed APIs are unavailable; do not bypass ERP permissions or write gates.

Exit: experiments are runnable and explain when API tools, browser tools, or visual actions are appropriate; business-mainline adoption remains evidence-gated.

## Phase 12 — Self-improvement, Post-training, and Agentic RL

Status (2026-09-14): `CONDITIONAL_PASS`, execution closed by explicit user decision. Further independent review is waived; strict PASS is not claimed. Remaining limitations and immutable evidence are indexed in [phase12-gap-ledger.md](phase12-gap-ledger.md). The original objectives below are retained as scope history, not a claim that every original exit condition passed.

- Turn reviewed failure trajectories into versioned Prompt/Skill candidates with offline evaluation and rollback.
- Run small offline experiments for reranking, SFT/DPO concepts, reward design, and Agentic RL prerequisites.
- Prevent online self-modification of production prompts, policies, permissions, or tools.

Exit: improvement claims come from held-out evaluations; changes remain reviewable and reversible; training methods are explained with their data and reward risks.

## Phase 13 — AI Infra, Hardening, and Capstone

Status (2026-09-14): `NOT_STARTED / DEFERRED_PENDING_USER_DECISION`. No implementation is authorized by the Phase 12 closure. The scope below is the original work to assess, not a verified list of missing code: existing evidence should be reused before any new implementation.

- Evaluate model routing, caching, concurrency, rate limiting, graceful degradation, local inference, and deployment choices from measured needs.
- Run failure injection, recovery drills, adversarial release review, manual-versus-Agent workflow benchmarks, and full-system evaluation.
- Produce a reproducible capstone and interview dossier linking business goals, architecture, traces, failures, trade-offs, and recovery.

Approved scope checklist (PLAN §22 and 项目方向纠偏 §Phase 13):

- Async/streaming/session isolation, connection pools, concurrency, rate limits and backpressure; define targets from measurements, not invented service promises.
- Provider/model routing, fallback, circuit breakers, caching and token/cost budgets.
- Compare local inference with BYOK; evaluate vLLM/SGLang/Ollama only where useful, not as three mandatory deployments.
- Traces, metrics, logs and dashboards covering token usage, cost and latency.
- Shadow/canary/A-B experiments, soak/load tests, fault injection, recovery and security exercises.
- Reproducible Docker/process supervision/health checks; Kubernetes design or bounded local experiments, never unsupported production-cluster claims.
- Human-versus-Agent workflow benchmarks (navigation, inputs, approvals and completion time), system evaluation and requirement-to-code-to-test traceability.
- Reuse and assemble evidence for ReAct, loop failures, checkpoint recovery, stale-approval rejection, idempotency/reconciliation, memory-poisoning rejection, multi-agent conflicts, model/Prompt A-B and a rejected advanced technique.
- Original exit scope: Rubric, risk assessment, adversarial release review and real browser acceptance, followed by reproducible capstone/README/project narrative. Assignment and interview exercises remain suspended under the current user instruction; README remains user-owned.
- Cross-phase release dependency: third-party attribution/NOTICE and distribution boundaries remain unresolved; stopping Phase 13 does not itself settle them or authorize public-release claims.

Exit: every public claim is reproducible; the complete system and its rejected alternatives can be explained and demonstrated from task intake through failure recovery.

## Cross-phase learning contract

Every Phase 4–13 increment follows: principle -> minimal lab -> source comparison -> Synora business adaptation -> tests and trace -> adoption/rejection record -> interview questions. Each phase also requires a bounded user Assignment and the existing Rubric, risk, and independent adversarial-review exit gates.

## Sources

- `docs/项目方向纠偏.md` — approved learning position, same-repository two-layer structure, knowledge priorities, and Phase 4–13 direction.
- `docs/PRD.md` — approved users, complete P2P scope, learning evidence, and acceptance requirements.
- `docs/ARCHITECTURE.md` — target component boundaries and business/lab isolation.
