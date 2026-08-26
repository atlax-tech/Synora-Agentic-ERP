# Roadmap

Status: `CONFIRMED` staged learning and delivery plan. Synora is an Agent-development learning repository and a real ERP practice vehicle. The business application layer and teaching lab live in one repository and one development line; staging does not remove complete business requirements.

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

## Phase 8 — Memory, RAG, and Contextual ERP Coach

- Implement scoped working, episodic, semantic, and procedural memory with candidate review, expiry, correction, and deletion.
- Compare FTS5/BM25, vector, hybrid, and reranking on a fixed dataset.
- Add a cited Contextual ERP Coach that re-queries live ERP facts rather than trusting memory.

Exit: authorized experience and SOP knowledge can be recalled with provenance; stale, cross-user, and injected memory or retrieval content fails safely.

## Phase 9 — Multi-Agent, MCP, and A2A

- Compare Supervisor, peer-to-peer, hierarchical, managed-agent-as-tool, and explicit graph-node patterns.
- Evaluate Planner -> Policy/Risk Reviewer first; start Reconciliation Agent only for exception paths.
- Implement local MCP and minimal A2A protocol exercises with typed handoffs, isolated tools, and bounded delegation.

Exit: adopted roles show measured net benefit over the single-Agent baseline; rejected roles retain runnable experiments and evidence explaining the decision.

## Phase 10 — Complete P2P Operating Agent

- Extend governed execution through PO Submit, Receipt, Invoice, and later Payment-related controls as separate milestones.
- Cover partial receipt/billing, cancellation, accounting controls, state drift, and human takeover.
- Use the execution, workflow, context, memory, and multi-Agent capabilities proven in earlier phases.

Exit: complete P2P requirements have real implementation and acceptance evidence; no stage is silently dropped.

## Phase 11 — Web/GUI Agents and Multimodal Observation

- Build bounded Web and GUI Agent experiments covering DOM/visual observation, action selection, login state, accessibility, and asynchronous pages.
- Evaluate whether UI operation is justified when stable typed APIs are unavailable; do not bypass ERP permissions or write gates.

Exit: experiments are runnable and explain when API tools, browser tools, or visual actions are appropriate; business-mainline adoption remains evidence-gated.

## Phase 12 — Self-improvement, Post-training, and Agentic RL

- Turn reviewed failure trajectories into versioned Prompt/Skill candidates with offline evaluation and rollback.
- Run small offline experiments for reranking, SFT/DPO concepts, reward design, and Agentic RL prerequisites.
- Prevent online self-modification of production prompts, policies, permissions, or tools.

Exit: improvement claims come from held-out evaluations; changes remain reviewable and reversible; training methods are explained with their data and reward risks.

## Phase 13 — AI Infra, Hardening, and Capstone

- Evaluate model routing, caching, concurrency, rate limiting, graceful degradation, local inference, and deployment choices from measured needs.
- Run failure injection, recovery drills, adversarial release review, manual-versus-Agent workflow benchmarks, and full-system evaluation.
- Produce a reproducible capstone and interview dossier linking business goals, architecture, traces, failures, trade-offs, and recovery.

Exit: every public claim is reproducible; the complete system and its rejected alternatives can be explained and demonstrated from task intake through failure recovery.

## Cross-phase learning contract

Every Phase 4–13 increment follows: principle -> minimal lab -> source comparison -> Synora business adaptation -> tests and trace -> adoption/rejection record -> interview questions. Each phase also requires a bounded user Assignment and the existing Rubric, risk, and independent adversarial-review exit gates.

## Sources

- `docs/项目方向纠偏.md` — approved learning position, same-repository two-layer structure, knowledge priorities, and Phase 4–13 direction.
- `docs/PRD.md` — approved users, complete P2P scope, learning evidence, and acceptance requirements.
- `docs/ARCHITECTURE.md` — target component boundaries and business/lab isolation.
