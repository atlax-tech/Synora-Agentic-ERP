# Phase 9 Adoption Card: erp_coach (v4)

- decision: `RETAIN`
- evidence arm: Phase 8 independent read-only Coach
- manifest code HEAD: `abbb18a9e0edcd2a0016e75704da80ff4dae0402`
- case SHA: `05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`
- model: `primary/qwen3:8b`

## Observed metrics

| arm | task | valid | recovery | p50/p95 ms | total token | calls | security |
|---|---:|---:|---:|---:|---:|---|---|
| single_agent | 8/12 | 11/12 | 8/12 | 7914/16803 | 6043 | 12 | 0/0/0/0 |
| planner_reviewer | 7/12 | 6/12 | 7/12 | 35066/55604 | 11429 | 20 | 0/0/0/0 |

- thresholds_met: `False`
- net_benefit: `False`
- security_passed: `True`
- approved thresholds: task ≥7/12; valid ≥11/12; recovery ≥10/12; p95 ≤7833 ms; total token ≤9653; security 100%.
- cost basis: local provider has no monetary price; token and latency are the approved temporary proxies.

## Decision reason

No child-Agent A/B was run; retain the independently verified Phase 8 read-only Coach entry.

Reviewer ACCEPT remains a review result, never ERP authorization; all final facts, risk and allowed behavior remain deterministic and policy controlled.
