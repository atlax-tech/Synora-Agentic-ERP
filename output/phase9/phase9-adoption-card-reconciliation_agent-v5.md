# Phase 9 Adoption Card: reconciliation_agent (v5)

- decision: `REJECT`
- evidence arm: real same-model A/B
- manifest code HEAD: `aa099df9d9c838404981161bf72cdc92501f391a`
- case SHA: `05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`
- model: `primary/qwen3:8b`

## Observed metrics

| arm | task | valid | recovery | p50/p95 ms | total token | calls | security |
|---|---:|---:|---:|---:|---:|---:|---|
| `single_agent` | 8/12 | 11/12 | 8/12 | 3427/7119 | 6043 | 12 | 0/0/0/0 |
| `planner_reviewer` | 7/12 | 6/12 | 7/12 | 18846/21507 | 11429 | 20 | 0/0/0/0 |

- thresholds_met: `False`
- net_benefit: `False`
- security_passed: `True`
- approved thresholds: task ≥7/12; valid ≥11/12; recovery ≥10/12; p95 ≤7833 ms; total token ≤9653; security 100%.
- cost basis: local provider has no monetary price; token and latency are the approved temporary proxies.

## Decision reason

P9-12 remains an exception path; no evidence shows an Agent superior to deterministic reconciliation.

Reviewer ACCEPT remains a review result, never ERP authorization; all final facts, risk and allowed behavior remain deterministic and policy controlled.
