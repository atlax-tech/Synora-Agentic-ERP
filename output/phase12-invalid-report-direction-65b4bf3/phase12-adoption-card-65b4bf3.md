# Phase 12 Adoption Card

状态: `BLOCKED`. 本卡为离线实验, 不授予业务 Runtime 或工具权限.

## Problem

历史失败需要先审核, 隔离和复现, 再判断 Prompt/Skill 指导, 后训练或 Agentic RL 是否值得保留.

## Minimal Lab

固定六类只读采购场景, 60 个组, 120 个案例, 按组划分 train/dev/test; 所有结果经同一 verifier, 训练权重使用有限 JSON.

## Evidence

- 数据集: `phase12-synthetic-v2`, digest `a4278cc7ef8df0a8129e0449b8ffc00c386f2ea3b033a9e239203fb14c9626fe`.
- 方法汇总: `{"baseline": {"calls": 0.0, "count": 72.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 0.0, "usage_not_applicable_records": 72.0, "verifier_rate": 1.0}, "best-of-3": {"calls": 216.0, "count": 72.0, "failed_records": 33.0, "known_usage_records": 49.0, "mean_elapsed_ms": 43900.07848035182, "mean_score": 0.2222222222222222, "p95_elapsed_ms": 72374.11658302881, "safety_rate": 0.9722222222222222, "unknown_usage_records": 23.0, "usage_not_applicable_records": 0.0, "verifier_rate": 0.5416666666666666}, "dpo-policy": {"calls": 0.0, "count": 144.0, "failed_records": 24.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 0.6666666666666666, "p95_elapsed_ms": 0.0, "safety_rate": 0.8888888888888888, "unknown_usage_records": 144.0, "usage_not_applicable_records": 0.0, "verifier_rate": 0.8333333333333334}, "live-baseline": {"calls": 144.0, "count": 144.0, "failed_records": 88.0, "known_usage_records": 122.0, "mean_elapsed_ms": 13367.621577198508, "mean_score": -0.125, "p95_elapsed_ms": 28277.773791982327, "safety_rate": 0.9861111111111112, "unknown_usage_records": 22.0, "usage_not_applicable_records": 0.0, "verifier_rate": 0.3888888888888889}, "prompt-candidate": {"calls": 144.0, "count": 144.0, "failed_records": 91.0, "known_usage_records": 128.0, "mean_elapsed_ms": 15523.715937237284, "mean_score": -0.1111111111111111, "p95_elapsed_ms": 29184.83029102208, "safety_rate": 0.9791666666666666, "unknown_usage_records": 16.0, "usage_not_applicable_records": 0.0, "verifier_rate": 0.3680555555555556}, "reflection": {"calls": 82.0, "count": 72.0, "failed_records": 54.0, "known_usage_records": 44.0, "mean_elapsed_ms": 12569.420045666144, "mean_score": -0.4166666666666667, "p95_elapsed_ms": 28004.607666982338, "safety_rate": 1.0, "unknown_usage_records": 28.0, "usage_not_applicable_records": 0.0, "verifier_rate": 0.25}, "reinforce-policy": {"calls": 0.0, "count": 144.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 144.0, "usage_not_applicable_records": 0.0, "verifier_rate": 1.0}, "sft-policy": {"calls": 0.0, "count": 144.0, "failed_records": 0.0, "known_usage_records": 0.0, "mean_elapsed_ms": 0.0, "mean_score": 1.0, "p95_elapsed_ms": 0.0, "safety_rate": 1.0, "unknown_usage_records": 144.0, "usage_not_applicable_records": 0.0, "verifier_rate": 1.0}, "skill-candidate": {"calls": 144.0, "count": 144.0, "failed_records": 85.0, "known_usage_records": 111.0, "mean_elapsed_ms": 13758.36956647941, "mean_score": -0.16666666666666666, "p95_elapsed_ms": 29649.15045897942, "safety_rate": 0.9861111111111112, "unknown_usage_records": 33.0, "usage_not_applicable_records": 0.0, "verifier_rate": 0.4097222222222222}}`.
- held-out bootstrap: `[{"conclusion": "INCONCLUSIVE", "delta": 0.013888888888888893, "groups": 12, "lower_95": -0.05555555555555555, "method_a": "prompt-candidate", "method_b": "baseline", "samples": 2000, "upper_95": 0.08333333333333336}, {"conclusion": "INCONCLUSIVE", "delta": 0.06944444444444446, "groups": 12, "lower_95": -0.06944444444444443, "method_a": "skill-candidate", "method_b": "baseline", "samples": 2000, "upper_95": 0.2638888888888889}]`.
- RAG 只引用 Phase 8 的独立 FTS5/vector 对照, 不与本阶段数据混排.

## Decision

每条 held-out 比较的结论描述 method_a 相对 method_b 的差异; Prompt 候选区间跨 0 时保留基线, Skill 候选由基线相对其的正差值证明候选退化, 因而拒绝候选. SFT, DPO, REINFORCE 仅保留为本地策略实验, 不能加载到业务主线.

## Known Negative

故意错误奖励可以让重复调用获得更高分, 但任务 verifier 仍失败; 这是预设奖励黑客负例, 不是生产效果结论.

## Rollback

候选选择和回滚写不可变 LAB receipt 并更新 active-version 指针; 回滚必须恢复父版本内容哈希, 未获独立授权前不改变业务 Registry.

## Limitations

Provider 可用性, usage 缺失, 小样本 bootstrap 和最终 Harness 同步授权会限制采用结论; 本卡不声称生产部署, 客户采用或通用模型提升.
