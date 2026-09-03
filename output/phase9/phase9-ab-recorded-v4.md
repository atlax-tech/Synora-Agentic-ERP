# Phase 9 P9.5 同模型 A/B 与 Adoption Cards

状态：`BLOCKED`；arm：`recorded`；模型：`recorded/recorded-phase9`。
code HEAD：`570f11f6e03abff5d2985923826a450796d93c47`；case SHA：`05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`。
deterministic fingerprint：`a2f5689b267ecea9cfdeda9adb5a9f4df27b641de5744d18af28482c284dfe01`。

两个 arm 按相同 P9-01→P9-12 顺序、同一源计划投影 digest 和同一模型角色执行；没有选择性重跑。
本地没有金额价格，token/延迟仅作成本代理；失败响应和完整 Prompt/候选原文不写入报告。

| arm | task | valid | fallback | recovery | p50/p95 ms | total token | calls | security |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `single_agent` | 12/12 | 6/12 | 6/12 | 11/12 | 0/0 | 1789 | 12 | 0/0/0/0 |
| `planner_reviewer` | 12/12 | 7/12 | 5/12 | 12/12 | 0/1 | 3606 | 23 | 0/0/0/0 |

推荐门槛：task ≥7/12、valid ≥11/12、recovery ≥10/12、p95 ≤7833 ms、总 token ≤9653，安全项 100%，并至少改善一个获批目标。

## Adoption Cards

| role | decision | net benefit | thresholds | security | evidence |
|---|---|---|---|---|---|
| `procurement_planner` | `REJECT` | `True` | `False` | `True` | recorded A/B only |
| `policy_risk_reviewer` | `REJECT` | `True` | `False` | `True` | recorded A/B only |
| `erp_coach` | `RETAIN` | `False` | `False` | `True` | Phase 8 independent read-only Coach |
| `reconciliation_agent` | `REJECT` | `False` | `False` | `True` | recorded A/B only |

### 决策理由

- `procurement_planner`: No approved real net-benefit evidence; keep the Planner experiment LAB_ONLY.
- `policy_risk_reviewer`: Reviewer does not receive authorization from an ACCEPT; the A/B has not proved approved net benefit.
- `erp_coach`: No child-Agent A/B was run; retain the independently verified Phase 8 read-only Coach entry.
- `reconciliation_agent`: P9-12 remains an exception path; no evidence shows an Agent superior to deterministic reconciliation.

未达标角色保留实验和拒绝理由；Reviewer 的 ACCEPT 仍不是安全授权。只有 Reviewer 卡达到门槛，才允许后续提交 `/enhance` 采用变更。
