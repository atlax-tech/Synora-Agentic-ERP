# Product

Status: `CONFIRMED` target product definition; implementation has not started.

## Mission

Synora turns complex ERP workflows into goal-driven, governed AI operations while preserving ERPNext as the authoritative transactional system.

The product is built to production-grade enterprise standards. The absence of current production usage is a claim boundary, not permission to substitute demo behavior, mock-only integrations, or weakened controls.

## Problem

ERP systems provide reliable business rules and transactions, but users must understand modules, document relationships, operating sequences, permissions, and error states. Information needed for one business goal is often distributed across sales, inventory, purchasing, receiving, invoicing, and policy sources.

LLMs can reduce the cognitive and coordination cost of this work, but they cannot be trusted to enforce permissions, calculate authoritative quantities, preserve transaction consistency, or execute high-risk writes without deterministic controls.

## Product Principle

> AI proposes. Deterministic software validates. Human users authorize high-risk actions. ERPNext executes and records. Synora verifies and explains the result.

## Users

- Procurement and operations users who understand their goal but not every ERPNext module or document transition.
- Procurement managers and approvers who need an explainable proposal and explicit control over high-risk actions.
- ERP users who need contextual explanations for blocked or failed operations.
- Maintainers who need traceable product, implementation, test, and interview evidence.

## Primary Job

Given near-term demand, inventory, open procurement, suppliers, and policy constraints, identify procurement risk, produce an explainable plan, create governed ERP actions after approval, and prove that ERPNext reached the intended state.

## Product Capabilities

### Procurement Operations

- Interpret a natural-language procurement goal and identify missing conditions.
- Retrieve authorized context across Sales, Inventory, and Purchase domains.
- Use deterministic calculations for shortage, quantity, money, duplicate checks, and risk classification.
- Produce a typed, explainable proposed action.
- Validate policy, permissions, schema, current state, and idempotency.
- Request human approval for high-risk mutations.
- Execute through ERPNext/Frappe and produce a verifiable execution receipt.

### Contextual ERP Coach

- Explain why an operation is blocked using the current page, document state, user role, ERP error, verified ERPNext knowledge, and simulated company SOP.
- Cite sources and distinguish verified facts, inferences, and unknowns.
- Treat documents and ERP fields as untrusted content rather than executable instructions.

### Governance and Evidence

- Trace a run through goals, tool calls, proposals, approvals, mutations, receipts, and reconciliation.
- Support repeatable scenario evaluation, failure analysis, development learning, and interview review.

## Delivery Scope

The complete product direction covers the Procure-to-Pay lifecycle: Material Request, Purchase Order, Purchase Receipt, Purchase Invoice, and Payment-related status and controls.

The first controlled-write milestone implements Material Request Draft and Purchase Order Draft. Purchase Order submission is a separate high-risk milestone. Receipt, Invoice, and Payment writes are staged—not removed—and must be implemented incrementally after their accounting, permission, idempotency, recovery, and evaluation gates are satisfied.

## Non-goals

- A universal ERP chatbot or a collection of shallow domain agents.
- Reimplementing ERPNext inventory, accounting, permission, or transaction rules in the LLM.
- Direct database access, model-generated SQL, arbitrary URLs, or arbitrary tool execution.
- Modifying Frappe or ERPNext upstream core.
- Rebuilding the ERP user interface.
- Unsupported claims of production adoption, measured efficiency, or enterprise outcomes.

## Sources

- `.synora-product-architecture-review.tmp.md` — approved product and architecture review, especially sections 2, 3, 9, 10, and 11.
- `README.md` — repository-level product positioning.
