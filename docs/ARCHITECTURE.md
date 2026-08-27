# Architecture

Status: `CONFIRMED` target architecture; Phase 0–4 components are implemented. Phase 5 durable workflow is implemented conditionally but its exit is currently `BLOCKED` pending the evidence recorded in ADR-0006; Phase 6–13 entries remain planned until their exit evidence exists.

## Architectural Style

Synora uses one repository and one development line for two code purposes: a real ERP business application layer and a teaching lab for Agent patterns. It does not use separate long-lived repositories or branches for those purposes. The deployed business path uses an extension-first, governed sidecar architecture:

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
          |
          v
 Learning & Eval Plane
 - labs and golden tasks
 - trajectory assertions
 - benchmark/adoption evidence
```

The separation isolates fast-changing model orchestration from the deterministic ERP transaction core. The teaching lab compares patterns through the same public contracts or declared test doubles; it does not receive production credentials or bypass the Control Plane. Neither separation is permission to duplicate business rules across services.

## System Responsibilities

| Component | Responsibilities | Prohibited behavior |
| --- | --- | --- |
| ERPNext/Frappe | Business documents, permissions, validation, workflows, transactions, ledgers, final state | Being bypassed or modified in upstream core |
| Synora Frappe App | ERP Desk entry point, initiator identity, typed gateway, policy, approval, idempotency, final execution, receipts | Trusting runtime-supplied identity or exposing arbitrary ERP access |
| Synora Control Plane | Run/capability, policy, approval, idempotency, deterministic execution, receipts, reconciliation | Treating open-ended model decisions as final authorization |
| Agent Runtime | Routing, bounded ReAct, planning/replanning, context, memory/skill adapters, constrained tool use, model budgets, trace | Direct MariaDB access, ERP internal imports, arbitrary tools, final authorization |
| Retrieval | Versioned source ingestion, retrieval, citations, permission-aware context | Treating retrieved text as instructions or unverified claims as ERP facts |
| Learning & Eval Plane | Minimal labs, golden tasks, trajectory/security assertions, benchmarks, adoption/rejection evidence | Production credentials, bypassing Control Plane, presenting a lab as deployed behavior |
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
                        -> Learning & Eval Plane -> labs/evals through public contracts
```

The Agent Runtime may call only registered typed gateway operations. It must not import ERPNext implementation modules or query ERP tables. Labs reuse typed public contracts or declared test doubles; they do not import hidden ERP internals or bypass the Gateway.

## Agent Execution Modes

The target Runtime supports three bounded modes selected by an evaluated router:

| Mode | Use | Boundary |
| --- | --- | --- |
| Direct or deterministic workflow | Fixed answers, calculations, and state display | No exploratory loop |
| Bounded ReAct | Short tasks whose next read depends on an observation | Typed allowlist plus step, repetition, no-progress, token, cost, and time guards |
| Plan-and-Execute with inner ReAct | Dependent tasks that may clarify, pause, recover, or replan | Versioned plan state, checkpoint, explicit approval boundary, deterministic writes |

Phase 4 establishes the execution-kernel baseline. Phase 5 establishes durable workflow. ERP writes remain disabled until the Phase 6 governance gates pass.

## Upstream and Runtime Strategy

- Use the runtime-verified pair fixed by ADR-0002: Frappe `6a329d0` (16.31.0) and ERPNext `11e0ba0` (16.32.3).
- Preserve read-only upstream source references for code archaeology and evidence.
- Make this repository installable as the Synora Frappe App at its root; keep `services/agent_runtime` as a separate Python project boundary.
- Use Bench for the initial development baseline. Add a reproducible `frappe_docker` custom/layered integration environment after the app skeleton is stable.

## Approval and Workflow Authority

- ERPNext Workflow, roles, permissions, and document controllers remain the authoritative enterprise policy inputs. Synora may enforce stricter policy but never weaken configured ERP rules.
- The controlled test baseline allows the initiator to explicitly confirm MR Draft and PO Draft actions. This is a confirmation boundary, not permission for silent execution.
- PO Submit, Purchase Receipt, Purchase Invoice, and Payment-related mutations require an authenticated approver who is different from the initiator.
- Approval is not execution. The gateway rechecks the approver, initiator, current document state, policy, and idempotency immediately before mutation.
- Missing, conflicting, stale, or unverifiable Workflow configuration fails closed. The ERP baseline is running, but the exact Frappe Role, Permission Matrix, Workflow, multi-level approval, and policy mapping remains unresolved and must close before Phase 6 enables writes.

## Technology Selection and Adoption Gates

The entries below describe the approved target or a conditional candidate. They do not claim that dependencies are installed, version-compatible, or runtime-verified.

| Layer | Selection | Status and adoption gate |
| --- | --- | --- |
| ERP system of record | ERPNext v16, Frappe v16, MariaDB, Redis | `CONFIRMED`; ADR-0002 fixes Frappe `6a329d0` (16.31.0) and ERPNext `11e0ba0` (16.32.3) after the manual P2P baseline passed |
| ERP extension | Root-installable Frappe Custom App using documented REST, whitelisted methods, hooks, and extension points | `CONFIRMED` approach; choose each hook/API from pinned-source and runtime evidence, never override upstream by convenience |
| Agent Runtime | Python sidecar with FastAPI, Pydantic v2, and HTTPX | `CONFIRMED` target boundary; exact versions require a Frappe Python compatibility check and lockfile |
| Structured contracts | Versioned Pydantic models and discriminated unions | `CONFIRMED` safety boundary; unknown actions and fields fail closed |
| Stateful Agent workflow | Hand-written baseline first; LangGraph evaluated for multi-step interruption, approval, resume, and reconciliation | `CONDITIONAL`; Phase 5 implements the hand-written Plan-and-Execute boundary and same-task lab comparison; LangGraph remains `LAB_ONLY` until ADR-0006 adoption gates and exit evidence pass |
| Model access | Provider interface; local Ollama/OpenAI-compatible runtime by default, optional remote compatible providers | `CONDITIONAL`; concrete models are selected by the same evaluation set, while CI uses deterministic recorded or mock responses |
| Retrieval | Curated versioned Markdown, metadata filtering, SQLite FTS5/BM25 baseline | `CONFIRMED` first stage; embeddings, vector search, hybrid retrieval, and reranking require measured evaluation benefit |
| Agent checkpoint | SQLite for development or a verified single-instance workflow only | `CONDITIONAL`; Phase 5 verifies WAL/CAS/lease behavior for development and a single Runtime instance; never treat checkpoint state as an ERP fact or claim production scalability |
| Frontend | Synora AI Operations inside ERPNext Desk using verified Frappe components | `CONFIRMED` product form; detailed component/token baseline remains a frontend design decision |
| Python engineering | `uv` lock, Ruff, mypy, pytest | `CONFIRMED` target toolchain; commands become verified only after scaffolding and successful execution |
| Observability | Structured logs plus run/action/tool/receipt correlation identifiers | `CONFIRMED` baseline; external observability SaaS is not required and must justify data, cost, and security impact |
| Development environment | Bench first; reproducible `frappe_docker` custom/layered image after the app skeleton is stable | `CONFIRMED` staged approach; disposable demo images are not an enterprise integration environment |

The business application baseline excludes arbitrary MCP/HTTP tools, direct SQL, free-form Agent swarms, an unmeasured vector database, Kafka, Kubernetes, and paid tracing platforms. The teaching lab may implement bounded MCP/A2A, retrieval, multi-Agent, Web/GUI, training, or infrastructure experiments for learning. Moving an experiment into the business path requires an Adoption Card, an architecture decision when material, failure analysis, same-task evaluation, and explicit permission/tool boundaries.

## Multi-Agent Evolution Boundary

Phase 3 remains the single-Agent deterministic baseline. Phase 9 requires runnable multi-Agent and protocol learning experiments, while business-path adoption remains evidence-gated. Stable role, typed state, event, handoff, tool, policy, and audit contracts must permit later role separation without replacing the ERP Gateway.

Candidate roles are Procurement Planner, Policy/Compliance Reviewer, ERP Coach, and Reconciliation Agent. Multi-Agent implementation requires evaluation evidence for context isolation, independent review, permission separation, or useful parallelism. Free-form agent swarms are prohibited.

## RAG Evolution Boundary

The initial retrieval implementation uses curated, versioned sources with SQLite FTS5/BM25 and metadata filtering. The target evolution path permits local embeddings, vector retrieval, hybrid search, reranking, context compression, grounded generation, permission filtering, and regression feedback only when retrieval evaluation justifies each step.

## Open Architecture Decisions

- Complete Frappe/ERPNext commit pair. — **RESOLVED 2026-08-24**：`docs/decisions/ADR-0002-frozen-baseline-pair.md` 固定 Frappe `6a329d0` (16.31.0) + ERPNext `11e0ba0` (16.32.3)。
- Exact user-bound authorization mechanism between Frappe and the Agent Runtime. — **RESOLVED 2026-08-25**：ADR-0003 uses a server-bound Agent Run and opaque short-lived capability; Phase 2 verified the path over real HTTP.
- Workflow engine adoption and LangGraph checkpoint adapter. — **CONDITIONAL 2026-08-27**：ADR-0006 保留手写引擎为业务基线，LangGraph 仍为 `LAB_ONLY`，待安全/恢复/运维证据和阶段出口阻塞关闭后复验。
- Phase 5 external acceptance evidence for n8n import/execute/audit is recorded as `LAB_ONLY`: fixed arm64 digest imports and executes against a loopback recorded Gateway; official audit exits 0 but reports the allowed HTTP Request node as a generic risky capability, so n8n remains outside the business Runtime. Managed Harness drift remains unresolved. Independent adversarial review completed on the final diff and full evidence: round 1 `CHANGES_REQUIRED` (stale terminal checkpoint P1), round 2 `PASS` after fix. Browser full-path and Runtime restart evidence are recorded in the Phase 5 development log.
- Local model and optional provider set selected by evaluation.
- Production storage and scaling path beyond the first single-instance implementation.
- Third-party license boundary for GPL and CC BY-NC materials.
- Exact ERPNext Workflow, Role, permission, and multi-level approval mapping implementing the confirmed approval baseline.

## Sources

- `docs/项目方向纠偏.md` — approved learning position, architecture layers, execution modes, and Phase 4–13 direction.
- `docs/PRD.md` — approved product boundaries, complete P2P scope, safety requirements, staged delivery, and unresolved decisions this architecture must preserve.
- `docs/development-log/20260824-Phase-0-开发日志.md` — evidence that this target architecture was incorporated during the approved Harness bootstrap.
