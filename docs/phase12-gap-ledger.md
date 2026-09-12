# Phase 12 缺口与预算账

状态：`IN_PROGRESS / LAB_ONLY`。本清单是新收口周期的唯一缺口索引；旧报告和旧实验结果不因本清单而变成有效证据。

## 发现依据

- 起始收口基线：`ffb5b81`；本次定向核查基线 HEAD：`fd3beea`。旧基线只用于追溯，不代表当前状态；最终实现提交后须以新 HEAD 重新绑定报告。
- 当前活动计划绑定 `code_version=17c6c9c`、数据 digest `a4278cc7ef8df0a8129e0449b8ffc00c386f2ea3b033a9e239203fb14c9626fe`，包含 60 组、120 案例，train/dev/test＝36/12/12 组、72/24/24 案例。
- 当前活动目录已有 2,064 条记录，其中 live 调用 816 次、replay 648 条、本地初始化/随机/规则基线 336 条、训练权重任务评测 432 条；这些数字不能代替历史累计成本。
- 旧 `pre-R2` 归档含 24 次无 reservation 的 live 调用；R5 Skill 阻断归档含活动结果的重复副本，不能再次计入独立调用。无法恢复的历史/诊断请求继续按保守上界保留，不反推不存在的请求。
- 现有 deterministic replay、训练和报告 artifact 仍保留为证据；本清单先区分文件完整性、实验完整性和阶段出口，不把既有门禁 PASS 当作计划完成。

## 六项定向核查（2026-09-12）

以下状态是对用户指定发现的逐项核实，不是新的缺口清单。修复完成后在同一行追加证据；不改写旧 manifest、旧报告或旧实验结果。

| 编号 | 核查结果 | 依据与当前处理 |
|---|---|---|
| F1 计划必需 test 方法 | **已修复且有证据（原流程偏差已披露）** | 原 manifest 未倒签：仍保留 `selected_method=baseline` 的历史事实；`phase12-method-selection.json` 明确 `pre_registered=false`，基于 Reflection/Best-of-3 各 72 条 dev 证据选择 Reflection，严格 gate 的 effective test methods 为 `baseline`、Prompt、Skill、Reflection，test 为 288/288。 |
| F2 bootstrap 差值方向 | **已修复且有证据** | `reporting.heldout_bootstrap` 先传候选、后传基线；`phase12-bootstrap-r5-v3.json` 三个比较均标为 `method_a=candidate`、`method_b=baseline`，并由不可变 live test 原始记录重算，正负方向测试覆盖非零正/负样例。 |
| F3 Adoption Card 旧结论 | **已修复且有证据** | 当前 Adoption Card 与 summary 分开呈现 safety gate、statistical conclusion 和 adoption；Prompt/Skill 的安全失败与 `INCONCLUSIVE` 统计分别表达，Reflection 为安全通过但区间跨 0 的 `KEEP_BASELINE_INCONCLUSIVE`，不再写死 Skill 退化。 |
| F4 版本绑定分类 | **已修复且有证据** | `code_version_is_compatible` 现在对实验绑定、local evidence、控制/报告分别分类，未知 Phase 12 源文件 fail closed；报告修复允许从不可变记录重算，`evaluation.py` 变化仍拒绝，artifacts targeted test 已覆盖这些边界。 |
| F5 CLI/缺口账本陈旧 | **已修复且有证据** | `docs/phase12-cli-runbook.md` 已覆盖五种 live 方法、post-hoc 选择回执、显式 batch、replay/live/local 分流和 14 组本地基线命令；本清单已绑定当前 HEAD `fd3beea`、数据 digest 和 816 次活动 live 账，旧 24 次单列。 |
| F6 本地基线、Rubric、历史成本 | **已修复且有证据** | 活动目录含初始化/随机/规则 dev/test 共 336 条零 Provider task eval，且初始化绑定三份 SFT 初始权重；summary 的九维 Rubric 各列 evidence/limitation；call accounting 分开列出 816 活动 live、86 post-hoc、23 recovery、24 次 pre-R2、失效 Skill 95 条/72 calls 副本及无法恢复历史的保守边界。 |

六项核查均已完成方向性修复或证据补齐；F1 的历史流程偏差保留并显式标注为 post-hoc，不能伪称预注册。最终全量门禁已完成；当前剩余是独立对抗审查未完成，以及 Review PASS 后的 Harness 收口。

## 缺口表

| 编号 | 状态 | 证据 | 关闭条件或限制 |
|---|---|---|---|
| G1 | **已关闭** | `dataset-phase12-synthetic-v2.json`、`model_input_text`、`observable_inputs=true`；120 个模型可见投影唯一 | 当前仅证明固定合成输入隔离；不外推生产数据质量 |
| G2 | **已关闭（replay 仍仅作复现）** | 五种方法均有 live Provider 入口；当前 live dev/test 分别为 360/288 条，replay 单独计数 | deterministic replay 不作为真实模型质量证据 |
| G3 | **已关闭** | `labs/self_improvement/cli.py` 的 `_cmd_evaluate` 支持五种 live 方法，reservation 与失败分母可核对 | 新批次仍须显式 `--batch-id` |
| G4 | **已关闭并保留时序偏差** | 当前数据 digest 为 `a4278c…` 的 synthetic-v2；旧 pre-R2 与旧报告在 `output/phase12-invalid-*` 归档，F1 另记录 test 方法 post-hoc 补测 | 该补测不是原 manifest 的预注册证据 |
| G5 | **已关闭** | 9 份训练权重 metadata、固定 dev 选择字段及 432 条独立权重 task eval 均通过严格检查 | 本地小模型结果不代表业务模型收益 |
| G6 | **已关闭** | `verify-stage --allow-pending-review --allow-pending-harness` 的 19 项证据检查通过；`verify-artifacts` 另作文件完整性检查 | Review/Harness 仍是阶段出口条件 |
| G7 | **已关闭为可追溯实验限制** | 当前 live 记录保留失败/UNKNOWN、统一 verifier 与 reservation 终态；历史不可恢复诊断请求单独列为 `unreconciled_historical` | 无 request-level 历史证据，不能声称恢复完整历史调用上界 |
| G8 | **阻断：REVIEW_INCOMPLETE** | `output/phase12/phase12-review-final.json` 记录两次授权尝试：一次超时关闭、一次明确返回 `REVIEW_INCOMPLETE`；没有 PASS 结论 | 本轮不再启动第三次；后续新授权的独立审查必须同时读取批准计划与实现证据并返回 `PASS` |
| G9 | **阻断：待单独授权** | 当前未写 Harness sync artifact，避免无授权改写 `.harness` | 仅在 Review PASS 后生成文件级 proposal，再取得独立授权 |

## 预算账规则

- 原计划 1,200 次是估算基线；replay、训练 episode 和 Provider 调用分开统计。
- 当前活动 live 调用为 816 次：原正式矩阵 730 次，矩阵内 Skill recovery 23 次，post-hoc Reflection test 补充 86 次；reservation 816/816 且终态均为 `RECORDED`。其中活动记录状态 calls 为 `SUCCEEDED=333`、`REJECTED=299`、`FAILED=150`、`UNKNOWN=34`；未知 usage 记录 132 条。replay、本地基线和训练 episode 不计入 Provider 调用。
- 可确认的历史下界为 840 次：活动 live 816 次加旧 `pre-R2` 归档 24 次；失效 Skill 归档的 95 条 raw rows/72 calls 字段是活动结果副本或阻断恢复证据，追加独立调用计数为 0。旧批次中无法确认是否发出的中断/诊断请求不从证据中删除，但没有 request-level 证据，保留 `unreconciled_historical` 的下界 0、上界不可得，不把活动目录计数称为阶段全部成本。
- 新批次必须有唯一 `batch_id`，请求前原子 reservation，结果或 UNKNOWN 终态随后写入；重启不得重置累计账。
- 本轮采用弹性预算：先按完整矩阵计算必需调用数，按批次增加；每批报告实际调用、失败、未知 usage、累计值和剩余预测。
- 结构错误优先本地复现和修复共享解析/提示，不自动重试；传输、认证和协议故障沿用连续三次停止规则。
- 不为追求指标而重复运行同一批次；新根因修复必须使用新 batch/experiment ID。

## 审查与文档状态

- 第一轮和第二轮审查前的旧报告已归档，不能与新数据拼接。
- 新收口周期允许最多两轮独立只读审查；本轮两次尝试均未形成 PASS，开发阶段未启动审查。
- Assignment、学习笔记、面试问答和 c2c 按用户要求不启用。
- PLAN、SPEC、ROADMAP、README 和 `.harness/` 暂不写阶段完成事实；最终只有 Review PASS 后才进入文档与 Harness 收口。

## 每个缺口的验收记录

后续提交只引用本文件中的 G 编号，并在阶段日志记录：根因、改动、失败复现、受影响旧 artifact、新批次 ID、测试退出码和剩余风险。没有对应证据的“已解决”不得从表中删除。
