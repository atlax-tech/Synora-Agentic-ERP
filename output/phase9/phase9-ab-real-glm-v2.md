# Phase 9 P9.5 同模型 A/B 与 Adoption Cards

状态：`PASS`；arm：`real`；模型：`assist/glm-5.3-flash`。
code HEAD：`5a36f752f5dd4c0a2fe64550052a03a0b5bf77fc`；case SHA：`05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`。
deterministic fingerprint：`a6e71aa25326c6c5b87e8084f6fc38c92f9fd98dbf1174371a5fe9d064179a72`。
threshold profile：`relative-model-v1`；completion cap：`512`；single baseline digest：`7450d6625c9ee976224abfc22ea76de32e2c3a14e622faa95e0cb6f305e38a09`。

两个 arm 按相同 P9-01→P9-12 顺序、同一源计划投影 digest 和同一模型角色执行；没有选择性重跑。
artifact 的 input_projection_digest 表示共享源计划；每个 case 的 arm_input_digest 另行绑定实际发送给该 arm 的序列化 provider messages。
本地没有金额价格，token/延迟仅作成本代理；失败响应和完整 Prompt/候选原文不写入报告。

| arm | task | valid | fallback | recovery | p50/p95 ms | total token | calls | security |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `single_agent` | 8/12 | 9/12 | 3/12 | 8/12 | 3826/8596 | 5692 | 10 | 0/0/0/0 |
| `planner_reviewer` | 8/12 | 10/12 | 2/12 | 8/12 | 2579/6679 | 6550 | 11 | 0/0/0/0 |

approved-qwen-v1：task ≥7/12、valid ≥11/12、recovery ≥10/12、p95 ≤7833 ms、总 token ≤9653；relative-model-v1：multi 的 task/valid/recovery 不低于同模型 single，至少一项严格提升，p95/token 不超过 single 的 1.5 倍；两者均要求安全项 100%。

## Adoption Cards

| role | decision | net benefit | thresholds | security | evidence |
|---|---|---|---|---|---|
| `procurement_planner` | `ADOPT` | `True` | `True` | `True` | real same-model A/B |
| `policy_risk_reviewer` | `ADOPT` | `True` | `True` | `True` | real same-model A/B |
| `erp_coach` | `RETAIN` | `False` | `False` | `True` | Phase 8 independent read-only Coach |
| `reconciliation_agent` | `REJECT` | `False` | `False` | `True` | real same-model A/B |

### 决策理由

- `procurement_planner`: Planner arm meets the approved recommended thresholds and improves at least one measured target.
- `policy_risk_reviewer`: Reviewer arm is eligible for /enhance only after the same-model A/B clears every threshold.
- `erp_coach`: No child-Agent A/B was run; retain the independently verified Phase 8 read-only Coach entry.
- `reconciliation_agent`: P9-12 remains an exception path; no evidence shows an Agent superior to deterministic reconciliation.

未达标角色保留实验和拒绝理由；Reviewer 的 ACCEPT 仍不是安全授权。只有 Reviewer 卡达到门槛，才允许后续提交 `/enhance` 采用变更。
