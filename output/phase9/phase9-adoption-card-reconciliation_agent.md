# Phase 9 Adoption Card: reconciliation_agent

- decision: `REJECT`
- evidence arm: real same-model A/B
- manifest code HEAD: `e6472f0a9ba642c75cd76eb0bf1b68b2b5b7beb8`
- case SHA: `05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`
- model: `primary/qwen3:8b`

## Observed metrics

| arm | task | valid | recovery | p50/p95 ms | total token | calls |
|---|---:|---:|---:|---:|---:|---:|
| single_agent | 8/12 | 11/12 | 8/12 | 3611/10281 | 6043 | 12 |
| planner_reviewer | 7/12 | 6/12 | 7/12 | 19547/22433 | 11429 | 20 |

## Gate

- thresholds_met: `False`
- net_benefit: `False`
- security_passed: `True`
- approved thresholds: task ≥7/12; valid ≥11/12; recovery ≥10/12; p95 ≤7833 ms; total token ≤9653; security 100%.
- cost basis: local provider has no monetary price; token and latency are the approved temporary proxies.

## Decision reason

P9-12 remains an exception path; no evidence shows an Agent superior to deterministic reconciliation.

Reviewer ACCEPT remains a review result, never ERP authorization; all final facts, risk and allowed behavior remain deterministic and policy controlled.
