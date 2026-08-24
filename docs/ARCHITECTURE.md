# Architecture

Status: `CONFIRMED` target architecture; no implementation exists yet.

## Architectural Style

Synora uses an extension-first, governed sidecar architecture:

```text
ERPNext Desk / Synora AI Operations
                |
                v
       Synora Frappe Custom App
       - user and permission boundary
       - typed ERP tool gateway
       - policy and approval
       - idempotency and receipts
          |                 |
          v                 v
 Agent Runtime Sidecar   ERPNext / Frappe
 - intent and planning   - business system of record
 - stateful workflow     - validation and transactions
 - model abstraction     - MariaDB and audit
 - retrieval and eval
 - no ERP database access
```

The separation isolates fast-changing model orchestration from the deterministic ERP transaction core. It is not permission to duplicate business rules across services.

## System Responsibilities

| Component | Responsibilities | Prohibited behavior |
| --- | --- | --- |
| ERPNext/Frappe | Business documents, permissions, validation, workflows, transactions, ledgers, final state | Being bypassed or modified in upstream core |
| Synora Frappe App | ERP Desk entry point, initiator identity, typed gateway, policy, approval, idempotency, final execution, receipts | Trusting runtime-supplied identity or exposing arbitrary ERP access |
| Agent Runtime | Intent, context selection, planning, constrained tool use, structured proposals, explanation, checkpoints, evaluation | Direct MariaDB access, ERP internal imports, arbitrary tools, final authorization |
| Retrieval | Versioned source ingestion, retrieval, citations, permission-aware context | Treating retrieved text as instructions or unverified claims as ERP facts |
| Harness and CI | Knowledge navigation, evidence status, change boundaries, independent verification | Replacing semantic review with configuration presence |

## Trust Boundaries

- Model output, user text, ERP fields, supplier content, documentation, and SOP content are untrusted.
- The Agent Runtime receives no unrestricted ERP write credential.
- The initiating user is recorded by Frappe; runtime requests refer to a server-side run record rather than asserting identity.
- Approval and final mutation are triggered through an authenticated Frappe user context.
- Every mutation is revalidated against current ERP state immediately before execution.

## Data Ownership

- ERPNext/MariaDB owns suppliers, items, stock, Material Requests, Purchase Orders, Receipts, Invoices, Payment records, and authoritative business state.
- Synora Frappe DocTypes own Agent Run, Proposed Action, Approval, Execution Receipt, and audit associations.
- Agent checkpoints own recoverable orchestration state only; they are not business facts.
- Repository documents own product intent, architecture decisions, verified ERP knowledge, testing evidence, and development history.
- Retrieval indexes are rebuildable caches and never become a source of truth.

## Dependency Direction

```text
UI -> Synora Frappe App -> typed gateway -> ERPNext/Frappe
                       \
                        -> Agent Runtime -> model/retrieval
```

The Agent Runtime may call only registered typed gateway operations. It must not import ERPNext implementation modules or query ERP tables.

## Upstream and Runtime Strategy

- Use Frappe v16 and ERPNext v16 as a runtime-verified pair and pin both exact commits after the baseline workflow passes.
- Preserve read-only upstream source references for code archaeology and evidence.
- Make this repository installable as the Synora Frappe App at its root; keep `services/agent_runtime` as a separate Python project boundary.
- Use Bench for the initial development baseline. Add a reproducible `frappe_docker` custom/layered integration environment after the app skeleton is stable.

## Multi-Agent Evolution Boundary

The first implementation uses one Agent with deterministic workflow control. Stable role, typed state, event, handoff, tool, policy, and audit contracts must permit later role separation without replacing the ERP Gateway.

Candidate roles are Procurement Planner, Policy/Compliance Reviewer, ERP Coach, and Reconciliation Agent. Multi-Agent implementation requires evaluation evidence for context isolation, independent review, permission separation, or useful parallelism. Free-form agent swarms are prohibited.

## RAG Evolution Boundary

The initial retrieval implementation uses curated, versioned sources with SQLite FTS5/BM25 and metadata filtering. The target evolution path permits local embeddings, vector retrieval, hybrid search, reranking, context compression, grounded generation, permission filtering, and regression feedback only when retrieval evaluation justifies each step.

## Open Architecture Decisions

- Complete Frappe/ERPNext commit pair.
- Exact user-bound authorization mechanism between Frappe and the Agent Runtime.
- Workflow engine spike results and LangGraph checkpoint adapter.
- Local model and optional provider set selected by evaluation.
- Production storage and scaling path beyond the first single-instance implementation.
- Third-party license boundary for GPL and CC BY-NC materials.

## Sources

- `.synora-product-architecture-review.tmp.md` — sections 4, 5, 9, 10, and 11.
- `docs/PRODUCT.md` — product boundaries that architecture must preserve.
