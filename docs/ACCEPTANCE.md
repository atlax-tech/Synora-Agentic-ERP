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

## Sources

- `.synora-product-architecture-review.tmp.md` — sections 3, 6, 7, 8, and approved conditions.
