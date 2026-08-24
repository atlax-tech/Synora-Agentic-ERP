# Design

Status: `CONFIRMED` target design constraints; detailed API and schema design remains unresolved.

## Primary Workflow

```text
Goal submitted in ERPNext Desk
  -> Frappe creates Agent Run with initiator and scope
  -> runtime uses allowlisted read tools
  -> deterministic services calculate shortages and constraints
  -> runtime returns versioned ProposedAction
  -> gateway evaluates schema, permissions, policy, duplicates, and snapshot
  -> authorized user approves or declines
  -> gateway reloads current ERP state
  -> ERPNext executes the mutation
  -> gateway reads the resulting document and stores Execution Receipt
  -> runtime resumes with verified outcome or reconciliation state
```

## Run State Model

```text
CREATED -> ANALYZING -> PROPOSED -> AWAITING_APPROVAL
                                      |-> DECLINED
                                      |-> EXPIRED
                                      `-> EXECUTING -> SUCCEEDED
                                                      |-> FAILED
                                                      `-> RECONCILIATION_REQUIRED
```

State transitions are controlled by deterministic code. The model may recommend an action but may not select or force a persisted state transition.

## Proposed Action Contract

Every proposed mutation must carry:

- schema and action version;
- run and action identifiers;
- typed business payload;
- business rationale and evidence references;
- risk classification;
- ERP state snapshot or version reference;
- stable idempotency key;
- required approval class;
- expiration and revalidation policy.

Exact field names and schemas belong in `docs/SPEC.md` after the PRD and Harness are aligned.

## Mutation Safety

- Fail closed on invalid or unknown model output.
- Check authorization, policy, object existence, company/warehouse scope, quantities, money, duplicates, and required upstream documents.
- Re-read state after approval to prevent time-of-check/time-of-use errors.
- Enforce idempotency at the execution boundary.
- If ERP execution may have succeeded without an acknowledgement, reconcile by idempotency key and target document before any retry.
- Return a structured receipt containing the target DocType/name, verified fields, outcome, and audit references.

## Retrieval Design Progression

1. Curate and version ERPNext documentation, verified repository knowledge, and simulated SOP sources.
2. Normalize metadata and chunk boundaries.
3. Establish an FTS5/BM25 retrieval baseline.
4. Measure recall, ranking, groundedness, refusal, latency, and version isolation.
5. Add local embeddings/vector indexing only when the baseline exposes a measured gap.
6. Evaluate hybrid retrieval and reranking against the same dataset.
7. Preserve citations, permission filtering, prompt-injection defenses, and rebuildability at every stage.

## Multi-Agent Introduction Gate

Multi-Agent design is considered only when at least one condition is demonstrated:

- one context cannot reliably contain planning, policy review, and reconciliation;
- roles require different permissions, models, tools, or acceptance criteria;
- independent evidence-based review reduces correlated errors;
- bounded parallel work produces material latency improvement.

Preferred implementation is explicit LangGraph subgraphs or supervisor-controlled typed handoffs over shared workflow state. Every role uses the same gateway, policy, approval, idempotency, and audit services. Controls include maximum steps, timeouts, role tool allowlists, loop detection, full tracing, and A/B evaluation against the single-Agent baseline.

## Sources

- `.synora-product-architecture-review.tmp.md` — sections 3.3, 4.3, 4.4, 7, and 11.
- `docs/ARCHITECTURE.md` — component and trust boundaries.
