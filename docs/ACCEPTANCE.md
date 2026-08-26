# Acceptance

Status: `CONFIRMED` product-level acceptance direction; milestone-specific thresholds remain partially `UNRESOLVED`.

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

## Deferred P2P Acceptance

Receipt, Invoice, Payment, and PO submission are staged requirements, not removed scope. Each requires its own permission, accounting, state-transition, approval, idempotency, recovery, and evaluation contract before write access is enabled.

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

## Sources

- `docs/项目方向纠偏.md` — approved learning evidence and same-repository business/lab boundary.
- `docs/PRD.md` — approved complete scope, non-functional requirements, acceptance summary, and benchmark boundaries.
- `docs/ROADMAP.md` — staged delivery order and milestone exit conditions.
