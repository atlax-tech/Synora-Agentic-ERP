# Phase 9 P9.5 同模型 A/B 与 Adoption Cards

状态：`BLOCKED`；arm：`real`；模型：`assist/glm-5.3-flash`。
code HEAD：`144993aec4fed52389901046a9d3ce50fe743037`；case SHA：`05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`。
deterministic fingerprint：`692de366b863392c6f0489630b2320b288885bf3d2a2e6ad590a46245f0ef6a4`。
threshold profile：`relative-model-v1`；completion cap：`512`；single baseline digest：`ae22e07775cfb56b34654626cd109c6fc0782767cd4229cafa4f5ba587d120ba`。

两个 arm 按相同 P9-01→P9-12 顺序、同一源计划投影 digest 和同一模型角色执行；没有选择性重跑。
artifact 的 input_projection_digest 表示共享源计划；每个 case 的 arm_input_digest 另行绑定实际发送给该 arm 的序列化 provider messages。
本地没有金额价格，token/延迟仅作成本代理；失败响应和完整 Prompt/候选原文不写入报告。

| arm | task | valid | fallback | recovery | p50/p95 ms | total token | calls | security |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `single_agent` | 7/12 | 6/12 | 6/12 | 7/12 | 4218/6179 | 5707 | 10 | 0/0/0/0 |
| `planner_reviewer` | 8/12 | 10/12 | 2/12 | 8/12 | 5337/8999 | 12352 | 20 | 0/0/0/0 |

approved-qwen-v1：task ≥7/12、valid ≥11/12、recovery ≥10/12、p95 ≤7833 ms、总 token ≤9653；relative-model-v1：multi 的 task/valid/recovery 不低于同模型 single，至少一项严格提升，p95/token 不超过 single 的 1.5 倍；两者均要求安全项 100%。

## Adoption Cards

| role | decision | net benefit | thresholds | security | evidence |
|---|---|---|---|---|---|
| `procurement_planner` | `REJECT` | `False` | `False` | `True` | real same-model A/B |
| `policy_risk_reviewer` | `REJECT` | `False` | `False` | `True` | real same-model A/B |
| `erp_coach` | `RETAIN` | `False` | `False` | `True` | Phase 8 independent read-only Coach |
| `reconciliation_agent` | `REJECT` | `False` | `False` | `True` | real same-model A/B |

### 决策理由

- `procurement_planner`: No approved real net-benefit evidence; keep the Planner experiment LAB_ONLY.
- `policy_risk_reviewer`: Reviewer does not receive authorization from an ACCEPT; the A/B has not proved approved net benefit.
- `erp_coach`: No child-Agent A/B was run; retain the independently verified Phase 8 read-only Coach entry.
- `reconciliation_agent`: P9-12 remains an exception path; no evidence shows an Agent superior to deterministic reconciliation.

未达标角色保留实验和拒绝理由；Reviewer 的 ACCEPT 仍不是安全授权。只有 Reviewer 卡达到门槛，才允许后续提交 `/enhance` 采用变更。
