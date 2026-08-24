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

## Approval and Workflow Authority

- ERPNext Workflow, roles, permissions, and document controllers remain the authoritative enterprise policy inputs. Synora may enforce stricter policy but never weaken configured ERP rules.
- The controlled test baseline allows the initiator to explicitly confirm MR Draft and PO Draft actions. This is a confirmation boundary, not permission for silent execution.
- PO Submit, Purchase Receipt, Purchase Invoice, and Payment-related mutations require an authenticated approver who is different from the initiator.
- Approval is not execution. The gateway rechecks the approver, initiator, current document state, policy, and idempotency immediately before mutation.
- Missing, conflicting, stale, or unverifiable Workflow configuration fails closed. The exact Frappe Role and Workflow mapping remains unresolved until the ERP baseline is running.

## Technology Selection and Adoption Gates

The entries below describe the approved target or a conditional candidate. They do not claim that dependencies are installed, version-compatible, or runtime-verified.

| Layer | Selection | Status and adoption gate |
| --- | --- | --- |
| ERP system of record | ERPNext v16, Frappe v16, MariaDB, Redis | `CONFIRMED` family; exact Frappe/ERPNext commits freeze only after the manual P2P baseline passes |
| ERP extension | Root-installable Frappe Custom App using documented REST, whitelisted methods, hooks, and extension points | `CONFIRMED` approach; choose each hook/API from pinned-source and runtime evidence, never override upstream by convenience |
| Agent Runtime | Python sidecar with FastAPI, Pydantic v2, and HTTPX | `CONFIRMED` target boundary; exact versions require a Frappe Python compatibility check and lockfile |
| Structured contracts | Versioned Pydantic models and discriminated unions | `CONFIRMED` safety boundary; unknown actions and fields fail closed |
| Stateful Agent workflow | Deterministic services by default; LangGraph only for multi-step interruption, approval, resume, and reconciliation | `CONDITIONAL`; adopt only after a checkpoint/resume spike proves the required behavior |
| Model access | Provider interface; local Ollama/OpenAI-compatible runtime by default, optional remote compatible providers | `CONDITIONAL`; concrete models are selected by the same evaluation set, while CI uses deterministic recorded or mock responses |
| Retrieval | Curated versioned Markdown, metadata filtering, SQLite FTS5/BM25 baseline | `CONFIRMED` first stage; embeddings, vector search, hybrid retrieval, and reranking require measured evaluation benefit |
| Agent checkpoint | SQLite for development or a verified single-instance workflow only | `CONDITIONAL`; never treat checkpoint state as an ERP fact or claim production scalability before a storage decision |
| Frontend | Synora AI Operations inside ERPNext Desk using verified Frappe components | `CONFIRMED` product form; detailed component/token baseline remains a frontend design decision |
| Python engineering | `uv` lock, Ruff, mypy, pytest | `CONFIRMED` target toolchain; commands become verified only after scaffolding and successful execution |
| Observability | Structured logs plus run/action/tool/receipt correlation identifiers | `CONFIRMED` baseline; external observability SaaS is not required and must justify data, cost, and security impact |
| Development environment | Bench first; reproducible `frappe_docker` custom/layered image after the app skeleton is stable | `CONFIRMED` staged approach; disposable demo images are not an enterprise integration environment |

The baseline explicitly excludes default Multi-Agent orchestration, arbitrary MCP/HTTP tools, direct SQL, an unmeasured vector database, Kafka, Kubernetes, and paid tracing platforms. These are not permanently banned, but each requires a demonstrated product or operational need, an architecture decision, failure analysis, and evaluation evidence before adoption.

## Multi-Agent Evolution Boundary

The first implementation uses one Agent with deterministic workflow control. Stable role, typed state, event, handoff, tool, policy, and audit contracts must permit later role separation without replacing the ERP Gateway.

Candidate roles are Procurement Planner, Policy/Compliance Reviewer, ERP Coach, and Reconciliation Agent. Multi-Agent implementation requires evaluation evidence for context isolation, independent review, permission separation, or useful parallelism. Free-form agent swarms are prohibited.

## RAG Evolution Boundary

The initial retrieval implementation uses curated, versioned sources with SQLite FTS5/BM25 and metadata filtering. The target evolution path permits local embeddings, vector retrieval, hybrid search, reranking, context compression, grounded generation, permission filtering, and regression feedback only when retrieval evaluation justifies each step.

## Open Architecture Decisions

- Complete Frappe/ERPNext commit pair. — **RESOLVED 2026-08-24**：`docs/decisions/ADR-0002-frozen-baseline-pair.md` 固定 Frappe `6a329d0` (16.31.0) + ERPNext `11e0ba0` (16.32.3)。
- Exact user-bound authorization mechanism between Frappe and the Agent Runtime.
- Workflow engine spike results and LangGraph checkpoint adapter.
- Local model and optional provider set selected by evaluation.
- Production storage and scaling path beyond the first single-instance implementation.
- Third-party license boundary for GPL and CC BY-NC materials.
- Exact ERPNext Workflow, Role, permission, and multi-level approval mapping implementing the confirmed approval baseline.

## Sources

- `docs/PRD.md` — approved product boundaries, complete P2P scope, safety requirements, staged delivery, and unresolved decisions this architecture must preserve.
- `docs/development-log/2026-08-24-harness-bootstrap.md` — evidence that this target architecture was incorporated during the approved Harness bootstrap.
