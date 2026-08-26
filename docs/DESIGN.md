# Frontend Design Constitution

Status: `CONFIRMED` frontend responsibility and interaction principles. Component baseline, browser matrix, viewport, accessibility target, and bilingual glossary are resolved by the P3.1 decision pack (user-approved 2026-08-25) and recorded below.

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
| Learning/Eval | Compare Agent patterns without presenting labs as production behavior | Golden task, implementation/version, Action/Observation trace, stop reason, metrics, adoption/rejection result |

These labels are information-architecture identifiers, not a finalized bilingual terminology set.

## Primary Desktop Surface

The canonical page hierarchy is the wireframe in `docs/PRD.md`, section 4.1. The visual emphasis must follow this order:

1. business goal and current Run state;
2. analysis conclusion and proposed actions;
3. evidence, calculations, risks, unknowns, and ERP state snapshot;
4. the next authorized user action;
5. trace and technical details.

The result must not collapse into a single chat transcript. Conversation may help collect a goal or missing condition, but governed actions require stable, inspectable UI regions.

The procurement user surface stays business-first. Teaching and evaluation details use a separate developer-facing view or clearly labeled secondary panel; experimental output must carry `LAB` or equivalent evidence status and must never look like a completed ERP action.

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
- Phase 4 and later traces distinguish plan, action, tool arguments, observation summary, error, retry/replan, final answer, and stop reason; hidden chain-of-thought is neither required nor displayed.
- Same-task comparison views identify execution mode, prompt/model/tool/skill versions, latency, tokens/cost when available, and the resulting Adoption Card decision.
- Conflicting or missing sources must be displayed as uncertainty, not blended into a confident answer.
- Audit views must minimize sensitive data and follow the viewer's ERP permissions.

## Visual System Boundary

Synora reuses the Frappe/ERPNext visual language and proven interaction patterns so that it remains coherent with the host product.

Confirmed baseline (P3.1 decision pack, fixed Frappe `6a329d0`): Desk's existing component system — Vue 3.3.0 + Bootstrap 4.6.2, `frappe.ui.form`, Control, Dialog, List/Form/Report views, `@headlessui/vue`, `@vueuse/core`. `frappe-ui` is not installed in the pinned baseline and is **not** introduced.

Until detailed visual tokens are produced during Phase 3 implementation:

- do not invent final brand colors, typography, spacing tokens, shadows, or component APIs;
- do not claim pixel-level compatibility with a Frappe release;
- record any necessary custom component and the reason the host component is insufficient;
- keep risk, status, evidence, and action semantics independent from a specific color palette.

## Accessibility and Responsive Boundary

- The first delivery target is desktop web inside ERPNext Desk; mobile and offline experiences are outside the current scope.
- Interactive elements require keyboard operation, visible focus, semantic labels, and understandable disabled states.
- Status, risk, and validation information cannot rely on color alone.
- Motion must not be required to understand progress or state transitions.
- Confirmed targets (P3.1 decision pack, user-approved 2026-08-25): browser matrix = Chrome/Edge/Firefox/Safari, latest 2 major versions each (desktop); minimum supported viewport width = 1280px with adaptive layout; accessibility conformance = WCAG 2.1 AA (keyboard, non-color status, visible focus).

## Content and Localization

- Use concise enterprise language that distinguishes facts, suggestions, risks, actions, and unknowns.
- Buttons start with clear verbs and name the actual action when risk is material.
- Errors state the cause category, safe next step, and correlation identifier when available.
- Chinese and English terminology must use the approved glossary below; mixed synonyms are prohibited. Status copy, error-code copy, and state-machine labels follow the same single glossary (see `docs/SPEC.md` §8.1 for Run status copy).
- Do not use anthropomorphic or celebratory language to conceal uncertainty or business risk.

### Approved Bilingual Glossary (P3.1 decision pack, user-approved 2026-08-25)

| English | 中文 | 备注 |
| --- | --- | --- |
| Agent Run | 智能体运行 | 有身份/范围/状态的服务端记录 |
| Goal | 目标 | 用户自然语言业务目标 |
| Proposed Action | 提议动作 | 版本化写入提议 |
| Approval Decision | 审批决定 | 独立审批人的决策记录 |
| Execution Receipt | 执行回执 | 幂等+对账的验证结果 |
| Reconciliation | 对账 | 不确定结果的人工接管路径 |
| Shortage Risk | 缺货风险 | 确定性计算 |
| Duplicate Purchase Risk | 重复采购风险 | 确定性计算 |
| Scope | 范围 | 公司/仓库授权范围 |
| Draft / Submit | 草稿 / 提交 | MR/PO 生命周期 |

## Prohibited Patterns

- Chat-only presentation for structured proposals, approvals, receipts, or audit evidence.
- Hidden mutation consequences behind a generic “Confirm” action.
- Success UI before ERP state has been read back and verified.
- Fabricated progress percentages, unsupported confidence scores, or ungrounded explanations.
- Silent retries after an uncertain write result.
- Dense technical traces as the default view for procurement users.
- Mock-only UI paths presented as completed enterprise behavior.
- Teaching-lab output presented without an explicit experimental label or mixed into authoritative ERP state.

## Frontend Acceptance Checks

- A user can distinguish proposal, approval, execution, and verified completion without reading implementation details.
- Normal, empty, loading, permission-denied, stale, failed, and reconciliation states are all represented.
- High-risk actions communicate their ERP consequence before authorization.
- Evidence and unknowns remain accessible from the primary decision surface.
- Keyboard and non-color state cues are included in component acceptance criteria.
- The frontend does not expose actions that bypass the typed gateway, policy, approval, or receipt boundaries.
- Developer-facing Trace and comparison views preserve permission filtering and make experimental versus business evidence unmistakable.

## Open Design Decisions

- Detailed visual tokens (final colors, typography, spacing, shadows) and component APIs for custom Synora components; the host-component baseline itself is resolved in §Visual System Boundary.
- Detailed approval interaction for configured multi-level approval, expiry, and changes requested; the Draft versus Submit responsibility baseline is defined in the PRD and architecture.

Resolved by the P3.1 decision pack (user-approved 2026-08-25): component baseline (Desk existing components), browser matrix (latest 2 major versions of Chrome/Edge/Firefox/Safari, desktop), minimum viewport 1280px, accessibility WCAG 2.1 AA, and the approved bilingual glossary (§Content and Localization).

## Sources

- `docs/项目方向纠偏.md` — approved business/teaching separation and Trace/evaluation learning path.
- `docs/PRD.md` — product form, users, workflow, states, content rules, and the canonical desktop wireframe.
- `docs/ARCHITECTURE.md` — system, trust, authorization, and execution boundaries the frontend must preserve.
