# Phase 9 P9.5 同模型 A/B 与 Adoption Cards

状态：`BLOCKED`；arm：`recorded`；模型：`recorded/recorded-phase9`。
code HEAD：`337accf9c4a90faebcc1d18b6c0e3f648c3f5156`；case SHA：`05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`。
deterministic fingerprint：`8c9fd0f3b76da524c9080b99c03375d72b5ee21ac739b9c0bd97aa89042d3fc3`。
threshold profile：`quality-first-model-v1`；completion cap：`512`；single baseline digest：`8623f679fdd8e90b2f76cd9bc403258baef41725830fc37074f138bfd210e797`。

两个 arm 按相同 P9-01→P9-12 顺序、同一源计划投影 digest 和同一模型角色执行；没有选择性重跑。
artifact 的 input_projection_digest 表示共享源计划；每个 case 的 arm_input_digest 另行绑定实际发送给该 arm 的序列化 provider messages。
本地没有金额价格，token/延迟仅作成本代理；失败响应和完整 Prompt/候选原文不写入报告。

| arm | task | valid | fallback | recovery | p50/p95 ms | total token | calls | security |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `single_agent` | 12/12 | 6/12 | 6/12 | 11/12 | 0/0 | 1544 | 10 | 0/0/0/0 |
| `planner_reviewer` | 12/12 | 7/12 | 5/12 | 12/12 | 0/1 | 3181 | 20 | 0/0/0/0 |

approved-qwen-v1：task ≥7/12、valid ≥11/12、recovery ≥10/12、p95 ≤7833 ms、总 token ≤9653；relative-model-v1：multi 的 task/valid/recovery 不低于同模型 single，至少一项严格提升，p95/token 不超过 single 的 1.5 倍；quality-first-model-v1：质量/恢复不低于同模型 single、至少一项严格提升、p95 不超过 1.5 倍，token 仅记录不阻断；三者均要求安全项 100%。

## Adoption Cards

| role | decision | net benefit | thresholds | security | evidence |
|---|---|---|---|---|---|
| `procurement_planner` | `REJECT` | `False` | `False` | `True` | recorded A/B only |
| `policy_risk_reviewer` | `REJECT` | `False` | `False` | `True` | recorded A/B only |
| `erp_coach` | `RETAIN` | `False` | `False` | `True` | Phase 8 independent read-only Coach |
| `reconciliation_agent` | `REJECT` | `False` | `False` | `True` | recorded A/B only |

### 决策理由

- `procurement_planner`: No approved real net-benefit evidence; keep the Planner experiment LAB_ONLY.
- `policy_risk_reviewer`: Reviewer does not receive authorization from an ACCEPT; the A/B has not proved approved net benefit.
- `erp_coach`: No child-Agent A/B was run; retain the independently verified Phase 8 read-only Coach entry.
- `reconciliation_agent`: P9-12 remains an exception path; no evidence shows an Agent superior to deterministic reconciliation.

未达标角色保留实验和拒绝理由；Reviewer 的 ACCEPT 仍不是安全授权。只有 Reviewer 卡达到门槛，才允许后续提交 `/enhance` 采用变更。
