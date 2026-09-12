# Phase 12 Adoption Card

状态: `LAB_ONLY / DRAFT`. 本卡为离线实验, 不授予业务 Runtime 或工具权限.

## Problem

历史失败需要先审核, 隔离和复现, 再判断 Prompt/Skill 指导, 后训练或 Agentic RL 是否值得保留.

## Minimal Lab

固定六类只读采购场景, 60 个组, 120 个案例, 按组划分 train/dev/test; 所有结果经同一 verifier, 训练权重使用有限 JSON.

## Evidence

- 数据集: `phase12-synthetic-v1`, digest `3f5ef14b0c5beec26ead9f26419eb3a27144794fcd6e2300d44144cdc17afdce`.
- 方法汇总: `{"baseline": {"calls": 0.0, "count": 144.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 144.0, "verifier_rate": 1.0}, "best-of-3": {"calls": 432.0, "count": 144.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 144.0, "verifier_rate": 1.0}, "prompt-candidate": {"calls": 0.0, "count": 144.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 144.0, "verifier_rate": 1.0}, "reflection": {"calls": 132.0, "count": 72.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 72.0, "verifier_rate": 1.0}, "skill-candidate": {"calls": 0.0, "count": 144.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 144.0, "verifier_rate": 1.0}}`.
- held-out bootstrap: `[{"conclusion": "INCONCLUSIVE", "delta": 0.0, "groups": 12, "lower_95": 0.0, "method_a": "baseline", "method_b": "prompt-candidate", "samples": 2000, "upper_95": 0.0}, {"conclusion": "INCONCLUSIVE", "delta": 0.0, "groups": 12, "lower_95": 0.0, "method_a": "baseline", "method_b": "skill-candidate", "samples": 2000, "upper_95": 0.0}, {"conclusion": "INCONCLUSIVE", "delta": 0.0, "groups": 12, "lower_95": 0.0, "method_a": "baseline", "method_b": "best-of-3", "samples": 2000, "upper_95": 0.0}]`.
- RAG 只引用 Phase 8 的独立 FTS5/vector 对照, 不与本阶段数据混排.

## Decision

Prompt 候选和 Skill 候选必须以 held-out 证据决定; 无改善或区间跨 0 时保留基线. SFT, DPO, REINFORCE 仅保留为本地策略实验, 不能加载到业务主线.

## Known Negative

故意错误奖励可以让重复调用获得更高分, 但任务 verifier 仍失败; 这是预设奖励黑客负例, 不是生产效果结论.

## Rollback

候选选择和回滚只写不可变 LAB receipt; 回滚必须恢复父版本内容哈希, 未获独立授权前不改变业务 Registry.

## Limitations

Provider 可用性, usage 缺失, 小样本 bootstrap 和最终 Harness 同步授权会限制采用结论; 本卡不声称生产部署, 客户采用或通用模型提升.
