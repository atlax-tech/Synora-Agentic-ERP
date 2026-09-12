# Phase 12 缺口与预算账

状态：`IN_PROGRESS / LAB_ONLY`。本清单是新收口周期的唯一缺口索引；旧报告和旧实验结果不因本清单而变成有效证据。

## 发现依据

- 起始 HEAD：`ffb5b81`。
- 当前实现 HEAD：`895dfcf`。
- 现有仓库门禁曾通过，但只证明当前代码和已生成 artifact 可运行。
- 现有活动数据集 digest：`5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb`。
- 现有活动数据集包含 60 组、120 案例，分组数量为 train/dev/test＝36/12/12，案例数量为 72/24/24。
- 现有 live baseline 文件为 24 条记录；其中 24 次调用、21 条失败或未知、16 条 usage 已知、8 条 usage 未知。无法由旧记录恢复的调用只按保守上界计入，不反推不存在的请求。
- 现有 deterministic replay、训练和报告 artifact 保留为历史证据；因输入设计和方法实现存在缺口，不直接作为新收口周期的最终证据。

## 缺口表

| 编号 | 阻断 | 证据 | 关闭条件 |
|---|---|---|---|
| G1 | 合成案例主要由编号和场景字段区分，去掉标签后任务输入近似相同 | `labs/self_improvement/data.py` 的 `_synthetic_case` 与 `model_input_text` | 新数据含可观察采购事实，规范化近重复检测通过，oracle 不进入输入 |
| G2 | deterministic replay 的 Reflection、Best-of-3 和候选策略不是 Provider 结果 | `labs/self_improvement/cli.py`、`replay.py` | 五种方法共用 live Provider 入口，调用记录、候选文本和 verifier 结果齐全 |
| G3 | live CLI 只允许 baseline | `labs/self_improvement/cli.py` 的 `_cmd_evaluate` | live 支持五种方法，方法预算和失败分母可核对 |
| G4 | 旧 test 已被查看并参与修复，不能继续作为新 held-out | `output/phase12-invalid-*` 与旧报告 | 新数据版本使用未用过的 test 组并重新冻结 |
| G5 | RL 只保存训练期间统计，缺少固定 dev checkpoint 选择和独立任务评测 | `labs/self_improvement/training.py` | 每 25 episode dev 评测，固定选择规则，权重独立 held-out 结果齐全 |
| G6 | artifact PASS 目前是文件完整性，不是阶段出口判定 | `labs/self_improvement/cli.py` 的 `_cmd_verify` | 增加完整 trial、批次、模型、候选、训练和审查状态检查 |
| G7 | 真实响应错误尚未按共享解析/提示根因闭环 | `output/phase12/evaluation-live-baseline-test.jsonl` | 最小 live 诊断可复现，失败分类稳定，修复后新批次不重放旧请求 |
| G8 | 第二轮独立审查为 `CHANGES_REQUIRED` | 阶段日志第 118 轮及审查记录 | 新周期最终审查 `PASS`；否则保留具体未关闭项 |
| G9 | Harness 写同步尚未授权 | `.agents/skills/harness-update/SKILL.md` | 仅在 Review PASS 后生成 proposal，再取得独立文件级授权 |

## 预算账规则

- 原计划 1,200 次是估算基线；replay、训练 episode 和 Provider 调用分开统计。
- 已确认写入活动 live record 的调用：24 次。旧批次中无法确认是否发出的中断请求不从证据中删除，按记录状态或保守上界保留。
- 新批次必须有唯一 `batch_id`，请求前原子 reservation，结果或 UNKNOWN 终态随后写入；重启不得重置累计账。
- 本轮采用弹性预算：先按完整矩阵计算必需调用数，按批次增加；每批报告实际调用、失败、未知 usage、累计值和剩余预测。
- 结构错误优先本地复现和修复共享解析/提示，不自动重试；传输、认证和协议故障沿用连续三次停止规则。
- 不为追求指标而重复运行同一批次；新根因修复必须使用新 batch/experiment ID。

## 审查与文档状态

- 第一轮和第二轮审查前的旧报告已归档，不能与新数据拼接。
- 新收口周期允许最多两轮独立只读审查；开发阶段不启动审查。
- Assignment、学习笔记、面试问答和 c2c 按用户要求不启用。
- PLAN、SPEC、ROADMAP、README 和 `.harness/` 暂不写阶段完成事实；最终只有 Review PASS 后才进入文档与 Harness 收口。

## 每个缺口的验收记录

后续提交只引用本文件中的 G 编号，并在阶段日志记录：根因、改动、失败复现、受影响旧 artifact、新批次 ID、测试退出码和剩余风险。没有对应证据的“已解决”不得从表中删除。
