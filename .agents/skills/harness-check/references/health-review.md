# Health review rubric

## Scoring

Use the weights from the shared health dimensions file. A dimension earns
points only for observed evidence. Deduct with a path/locator and reasoning.
Mark an unassessed semantic dimension explicitly; do not replace it with a
file-count heuristic.

## Severity

- `BLOCKING`: permits unsafe writes, contradicts requirements, exposes secrets,
  claims false verification, or prevents reliable project understanding.
- `HIGH`: meaningful drift, broken traceability, misleading architecture,
  invalid commands, or missing ownership that can cause bad changes.
- `IMPROVEMENT`: clarity, context efficiency, or portability issue with a safe
  current path.

## Metamorphic checks

Adding duplicated documents must not increase health. Rewording without adding
evidence must not erase a deduction. A custom filename may receive full credit
when its authority and links are clear.

The machine report exposes `layout` and `role_evidence`. Treat these as
role-equivalent structural evidence, not semantic proof. For references,
`coverage.status` distinguishes explicit Markdown/HTML links, existing inline
repository paths, and zero detected coverage. Missing inline-code tokens are
intentionally unassessed because they may be commands, package names, API
routes, or future design paths; do not turn `valid: true` into a claim that
those tokens exist.

## Overall score

Sum dimension points out of 100. Keep machine score and semantic review visible
when their coverage differs. The overall score never overrides a blocking issue.
