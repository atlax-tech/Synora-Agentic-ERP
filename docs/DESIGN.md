# Frontend Design Constitution

Status: `CONFIRMED` frontend responsibility and interaction principles. Detailed visual tokens, component mapping, browser matrix, and accessibility target remain `UNRESOLVED`.

## Purpose and Boundary

This document governs how Synora presents its product behavior inside ERPNext Desk. It is the frontend design constitution for information hierarchy, interaction states, risk communication, accessibility, and visual consistency.

It does not define product scope, backend workflow, API contracts, Agent orchestration, RAG architecture, or Multi-Agent adoption. Those authorities are `docs/PRD.md`, `docs/ARCHITECTURE.md`, and `docs/SPEC.md` when the specification exists.

## Experience Principles

1. **Operate inside the ERP context.** Synora should feel like a governed ERPNext capability, not a disconnected chat application.
2. **Show evidence before confidence.** Facts, calculations, sources, recommendations, risks, and unknowns must be visually distinguishable.
3. **Make workflow state visible.** Users must always understand the current Run stage, what the system is doing, what is blocked, and what can happen next.
4. **Use progressive disclosure.** Lead with the business conclusion and required decision; keep source detail, traces, and technical evidence available without overwhelming the primary task.
5. **Make consequences explicit.** Approval controls must name the real ERP document and business effect. Generic confirmation copy is prohibited.
6. **Fail safely and visibly.** Permission denial, stale state, execution failure, and reconciliation are product states, not generic toast errors.
7. **Preserve user control.** AI suggestions never masquerade as completed ERP actions. Proposal, approval, execution, and verified outcome must remain visually distinct.

## Information Architecture

The confirmed first desktop experience lives in ERPNext Desk and provides these product areas:

| Area | User purpose | Primary content |
| --- | --- | --- |
| New Run | Describe a procurement goal and authorized scope | Goal, company/warehouse scope, time window, validation feedback |
| Runs | Follow current and historical work | Status, progress, proposals, failures, receipts |
| Approvals | Review governed business mutations | Consequences, evidence, risks, state snapshot, approve/decline/request changes |
| Audit | Investigate what happened | Correlated run, tool, policy, approval, execution, and reconciliation evidence |

These labels are information-architecture identifiers, not a finalized bilingual terminology set.

## Primary Desktop Surface

The canonical page hierarchy is the wireframe in `docs/PRD.md`, section 4.1. The visual emphasis must follow this order:

1. business goal and current Run state;
2. analysis conclusion and proposed actions;
3. evidence, calculations, risks, unknowns, and ERP state snapshot;
4. the next authorized user action;
5. trace and technical details.

The result must not collapse into a single chat transcript. Conversation may help collect a goal or missing condition, but governed actions require stable, inspectable UI regions.

## State and Feedback Contract

| State | Frontend obligation |
| --- | --- |
| Empty | Explain what Synora can do and provide one clear starting action |
| Input invalid | Preserve user input and identify the exact missing or invalid condition |
| Analyzing | Show the current bounded step without inventing percentage progress |
| Proposed | Separate deterministic findings from AI explanation and display all material unknowns |
| Awaiting approval | Freeze the reviewed proposal, show snapshot time and expiry conditions, and present explicit consequences |
| Executing | Prevent duplicate actions and show that ERP confirmation is still pending |
| Succeeded | Link the verified ERP document and show the Execution Receipt summary |
| Failed | Classify the failure, preserve correlation evidence, and offer only safe recovery actions |
| Reconciliation required | State that the result is uncertain, prohibit blind retry, and show reconciliation progress |
| Permission denied | Reveal no unauthorized business data and provide an actionable escalation direction |

Loading, empty, partial, stale, error, and permission states are required design work, not implementation afterthoughts.

## High-Risk Action Design

- Place approve, decline, and request-changes actions next to the exact proposal being reviewed.
- Show target DocType, document count, supplier, quantity, amount when available, and the execution consequence before approval.
- Visually separate approval from execution; approval does not imply that ERP execution has succeeded.
- Require a fresh-state warning when the proposal has expired or ERP data changed.
- Disable repeated submission while an action is executing.
- Never use color, iconography, or optimistic wording as the only indication of risk or completion.

## Evidence and Explainability

- Label authoritative ERP facts, deterministic calculations, retrieved sources, Agent recommendations, warnings, and unknowns as different information types.
- Every material recommendation must expose its supporting source or calculation path.
- Long evidence and traces may be collapsed, but their existence and status must remain visible.
- Conflicting or missing sources must be displayed as uncertainty, not blended into a confident answer.
- Audit views must minimize sensitive data and follow the viewer's ERP permissions.

## Visual System Boundary

Synora should initially reuse the Frappe/ERPNext visual language and proven interaction patterns so that it remains coherent with the host product. A separate bespoke design system is not justified before the exact Frappe v16 component and token baseline is inspected.

Until that baseline is verified:

- do not invent final brand colors, typography, spacing tokens, shadows, or component APIs;
- do not claim pixel-level compatibility with a Frappe release;
- record any necessary custom component and the reason the host component is insufficient;
- keep risk, status, evidence, and action semantics independent from a specific color palette.

## Accessibility and Responsive Boundary

- The first delivery target is desktop web inside ERPNext Desk; mobile and offline experiences are outside the current scope.
- Interactive elements require keyboard operation, visible focus, semantic labels, and understandable disabled states.
- Status, risk, and validation information cannot rely on color alone.
- Motion must not be required to understand progress or state transitions.
- Exact viewport support, browser matrix, contrast target, and accessibility conformance level remain unresolved until the Frappe v16 baseline is verified.

## Content and Localization

- Use concise enterprise language that distinguishes facts, suggestions, risks, actions, and unknowns.
- Buttons start with clear verbs and name the actual action when risk is material.
- Errors state the cause category, safe next step, and correlation identifier when available.
- Chinese and English terminology must use one approved glossary; mixed synonyms are prohibited after the glossary is fixed.
- Do not use anthropomorphic or celebratory language to conceal uncertainty or business risk.

## Prohibited Patterns

- Chat-only presentation for structured proposals, approvals, receipts, or audit evidence.
- Hidden mutation consequences behind a generic “Confirm” action.
- Success UI before ERP state has been read back and verified.
- Fabricated progress percentages, unsupported confidence scores, or ungrounded explanations.
- Silent retries after an uncertain write result.
- Dense technical traces as the default view for procurement users.
- Mock-only UI paths presented as completed enterprise behavior.

## Frontend Acceptance Checks

- A user can distinguish proposal, approval, execution, and verified completion without reading implementation details.
- Normal, empty, loading, permission-denied, stale, failed, and reconciliation states are all represented.
- High-risk actions communicate their ERP consequence before authorization.
- Evidence and unknowns remain accessible from the primary decision surface.
- Keyboard and non-color state cues are included in component acceptance criteria.
- The frontend does not expose actions that bypass the typed gateway, policy, approval, or receipt boundaries.

## Open Design Decisions

- Exact Frappe v16 components, tokens, layout primitives, and extension constraints.
- Approved Chinese/English product terminology and status glossary.
- Supported desktop viewport and browser matrix.
- Accessibility conformance target and verification tooling.
- Detailed approval interaction for self-approval, multi-level approval, expiry, and changes requested.

## Sources

- `docs/PRD.md` — product form, users, workflow, states, content rules, and the canonical desktop wireframe.
- `docs/ARCHITECTURE.md` — system, trust, authorization, and execution boundaries the frontend must preserve.
