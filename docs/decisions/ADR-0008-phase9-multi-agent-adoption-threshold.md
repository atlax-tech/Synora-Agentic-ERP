# ADR-0008：Phase 9 多 Agent 采用量化门槛

- 状态：`APPROVED`
- 日期：2026-09-03
- 决策者：用户
- 范围：Phase 9 P9.2，约束后续多 Agent 对照与采用评测

## 背景

P9.1 已冻结 12 个固定采购用例，并完成同一 `primary/qwen3:8b` 的真实单 Agent 基线。基线绑定代码 HEAD `e23a152bf17667b5fb31eabe394a9bf8edf2f094` 和 case-spec SHA `05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`。任务正确率为 `6/12`，有效解释为 `11/12`，恢复成功为 `10/12`，p95 延迟为 `5222 ms`，总 token 为 `6435`；有限安全项均为零，Trace 完整率为 `12/12`。完整结果见 `output/phase9/phase9-single-agent-baseline-real-v2.json`。

本地 Ollama 没有可验证的付费单价，报告中的 `estimated_cost_microusd=0` 表示未计价。若现在编造金额上限，会把未知信息伪装成成本事实。

## 决策

用户批准推荐档位，并明确接受本地无金额价格，暂以 token/延迟作为成本代理。后续候选多 Agent arm 必须同时满足：

| 指标 | 推荐门槛 |
| --- | --- |
| 任务正确率 | `≥7/12`，至少比基线多 1 案 |
| 有效解释 | `≥11/12`，不得低于基线 |
| 恢复成功 | `≥10/12`，不得低于基线 |
| p95 延迟 | `≤7833 ms`，即基线 1.5 倍取整 |
| 总 token | `≤9653`，即基线 1.5 倍取整 |
| 净收益 | 至少一个用户批准的目标指标改善，且其他质量/恢复底线不退化 |
| 安全 | 有限安全项 `100%`；未授权工具、ERP 业务写入、跨范围泄漏、Secret 泄漏均为零 |

确定性数字、风险、权限、策略和最终行为仍由现有校验、Gateway、Policy 与 Frappe 决定；Reviewer 的 `ACCEPT` 不构成授权。没有任何角色达到净收益时，Phase 9 必须记录为 `BLOCKED`，不得放宽本 ADR。

## 成本代理边界

在没有带价格 provider manifest 前，只比较 prompt/completion/reasoning token、模型调用数、p50/p95 延迟和总墙钟时间。不得把 token 或延迟换算成金额，也不得把 `0` 的未计价字段解释为零成本。若以后引入有价格 provider，必须新建完整 manifest，并重新执行同模型 A/B。

## 影响与风险

推荐档允许候选方案在质量提升的前提下使用至多 1.5 倍基线 token 和 p95 延迟。12 案单机单模型分布仍是方向性证据，不代表 provider 通用性；P9.5 必须保留每个 arm 的完整 manifest、失败 artifact 和代码 HEAD。安全项没有成本换取例外，任何 P0/P1 风险未关闭都阻断阶段出口。

## 被否决的方案

- 宽松档：允许 2 倍 token/延迟，当前不采用，因为用户选择了推荐档。
- 严格档：要求质量与恢复更高且成本不增，当前不采用，因为尚无足够基线支持该更窄的探索空间。
- 立即编造金额上限：否决，因本地 provider 没有可验证价格。

## 证据与复核

- 决策包：`output/phase9/phase9-threshold-decision-pack.json`、`output/phase9/phase9-threshold-decision-pack.md`
- 基线报告：`output/phase9/phase9-single-agent-baseline-real-v2.json`
- P9.3/P9.5 若修改 case、模型、Prompt、schema 或代码，必须生成新 manifest 并整套重跑；不得覆盖本 ADR 的已批准门槛。
