# Roadmap

Status: `CONFIRMED` staged delivery plan. Staging does not remove complete product requirements.

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

## Phase 3 — Read-only Procurement Agent

- Implement goal understanding, constrained context acquisition, deterministic shortage calculations, and explainable planning.
- Establish the single-Agent and FTS5 evaluation baselines.

Exit: normal, ambiguous, unauthorized, tool-failure, and malicious-content cases are repeatable without writes.

## Phase 4 — Proposal, Approval, and First Writes

- Add versioned ProposedAction, policy, approval, idempotency, state revalidation, receipts, and reconciliation.
- Enable MR Draft, then PO Draft. Review PO submission separately.

Exit: unauthorized or duplicated writes are prevented; real ERP integration scenarios pass.

## Phase 5 — Complete P2P Lifecycle Delivery

- Implement PO submission, Receipt, Invoice, and later Payment-related controlled operations as separate milestones.
- Cover partial receipt/billing, cancellation, accounting controls, state drift, and human takeover.

Exit: full P2P requirements have implementation and acceptance evidence; no stage is silently dropped.

## Phase 6 — Contextual ERP Coach and RAG Evolution

- Add verified ERP knowledge and simulated company SOP.
- Establish FTS5 baseline, then evaluate embeddings, vector retrieval, hybrid search, reranking, and compression.

Exit: groundedness, refusal, permission filtering, version isolation, and prompt-injection evaluations pass.

## Phase 7 — Multi-Agent Evaluation and Conditional Adoption

- Evaluate Planner, Policy Reviewer, ERP Coach, and Reconciliation role separation.
- Compare quality, latency, cost, safety, observability, and complexity against the single-Agent baseline.

Exit: adopt only roles with measured net benefit and no governance regression.

## Phase 8 — Hardening and Interview Evidence

- Run failure injection, recovery drills, adversarial release review, and manual-versus-Agent workflow benchmarks.
- Maintain feature dossiers and evidence-backed bilingual project presentation.

Exit: every public claim is reproducible and the system can be explained from business goal through failure recovery.

## Sources

- `docs/PRD.md` — approved priorities, complete P2P scope, acceptance requirements, and unresolved decisions.
- `docs/ARCHITECTURE.md` — target component boundaries and evidence-gated RAG/Multi-Agent evolution.
