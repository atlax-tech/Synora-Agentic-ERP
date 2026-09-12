# Phase 12 阶段报告

状态: `BLOCKED / LAB_ONLY`. 第二轮独立对抗审查结果为 `CHANGES_REQUIRED`; 按两轮上限标记为 BLOCKED, Harness 文件级同步也未闭合, 不能写阶段 PASS.

## 业务问题与数据流

历史失败证据先经过来源, 完整性, 脱敏和相关性审核; 合成采购场景再按组冻结为 train/dev/test. 模型或小策略只看到允许的任务输入和观察状态, 输出经安全门禁与确定性 verifier, oracle 只留在评分侧. 结果, 调用预算, 训练权重和选择回执都写入 `output/phase12/`.

数据集 `phase12-synthetic-v1` / digest `5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb`, split `{'dev': 24, 'test': 24, 'train': 72}`, groups `{'dev': 12, 'test': 12, 'train': 36}`.
证据记录代码版本 `['02b5782', '06150df', '13872f1', '586f472', '827a28d', '89dcc9b', '946c3fa', 'c172592', 'eae31b9']`; 修复重跑可能保留多个版本。

## 方法结果

| method | count | verifier | safety | score | calls | unknown usage | usage n/a | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 144 | 1.000 | 1.000 | 1.000 | 0 | 0 | 144 | 0.0 |
| best-of-3 | 72 | 1.000 | 1.000 | 1.000 | 216 | 0 | 72 | 0.0 |
| live-baseline | 24 | 0.125 | 0.542 | -0.750 | 24 | 8 | 0 | 7928.1 |
| prompt-candidate | 144 | 1.000 | 1.000 | 1.000 | 0 | 0 | 144 | 0.0 |
| reflection | 72 | 1.000 | 1.000 | 1.000 | 144 | 0 | 72 | 0.0 |
| skill-candidate | 144 | 0.333 | 1.000 | -0.333 | 0 | 0 | 144 | 0.0 |

失败, UNKNOWN, 未知 usage 和部分生成均保留在分母; 重复运行不是新增独立样本. Live 请求预留账本单独记录批次和终态.

## Held-out 规则

按场景组 bootstrap 2,000 次: `[{"conclusion": "INCONCLUSIVE", "delta": 0.0, "groups": 12, "lower_95": 0.0, "method_a": "baseline", "method_b": "prompt-candidate", "samples": 2000, "upper_95": 0.0}, {"conclusion": "IMPROVED", "delta": 0.6666666666666666, "groups": 12, "lower_95": 0.4166666666666667, "method_a": "baseline", "method_b": "skill-candidate", "samples": 2000, "upper_95": 0.9166666666666666}]`. 差值方向为 method_a 相对 method_b; 下界大于 0 才能称 method_a 有改善证据, 跨 0 为 `INCONCLUSIVE`, 上界小于 0 表示 method_a 退化.

## 训练与奖励

训练 artifacts: `[{"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-dpo-seed-17", "code_version": "9ba2092", "config": {"beta": 0.1, "epochs": 100, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "11f402273ce1e86c1a2c879f8328c4b7b1302c988d2ea59fbcde3eb0d533d1a2", "method": "dpo", "metrics": {"dev_preference_pairs": 20.0, "final_dpo_loss": 0.40642958879470825, "preference_pairs": 60.0}, "reference_weight_sha256": "11f402273ce1e86c1a2c879f8328c4b7b1302c988d2ea59fbcde3eb0d533d1a2", "schema_version": "1", "seed": 17, "weight_path": "output/phase12/weights-dpo-17.json", "weight_sha256": "8b48cdd8e77560f5e03f83585abcf4f54427fa1b5c8a5388d433b3e7c88f7c78"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-dpo-seed-29", "code_version": "6c3e3c6", "config": {"beta": 0.1, "epochs": 100, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "501ce8f33658a9d9c56772d627e07387c13f472ea7305c16b8d74763c8d9a64a", "method": "dpo", "metrics": {"dev_preference_pairs": 20.0, "final_dpo_loss": 0.41726866364479065, "preference_pairs": 60.0}, "reference_weight_sha256": "501ce8f33658a9d9c56772d627e07387c13f472ea7305c16b8d74763c8d9a64a", "schema_version": "1", "seed": 29, "weight_path": "output/phase12/weights-dpo-29.json", "weight_sha256": "d37bb04f07d26f924cb5b3aff1a7f0558b43e94a4586b74d21780f4327f6a1b1"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-dpo-seed-43", "code_version": "8f86d65", "config": {"beta": 0.1, "epochs": 100, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "a12e4b50d64a06f273b28a178efc898b4f0273832ce8a11fb59de899212db858", "method": "dpo", "metrics": {"dev_preference_pairs": 20.0, "final_dpo_loss": 0.42355555295944214, "preference_pairs": 60.0}, "reference_weight_sha256": "a12e4b50d64a06f273b28a178efc898b4f0273832ce8a11fb59de899212db858", "schema_version": "1", "seed": 43, "weight_path": "output/phase12/weights-dpo-43.json", "weight_sha256": "3167d22ebf8cb7b183f782d216e523752f0be09042d4f4118fda71fdb527ff2b"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-reinforce-seed-17", "code_version": "89be7d2", "config": {"episodes": 300, "gamma": 0.95, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "11f402273ce1e86c1a2c879f8328c4b7b1302c988d2ea59fbcde3eb0d533d1a2", "method": "reinforce", "metrics": {"average_steps": 1.97, "episodes": 300.0, "invalid_actions": 0.0, "mean_return": 0.39793333333333325, "success_rate": 0.7266666666666667}, "reference_weight_sha256": null, "schema_version": "1", "seed": 17, "weight_path": "output/phase12/weights-reinforce-17.json", "weight_sha256": "e715958fd7a57e813bacd4d7b34d1f7a63a01ef1a2e8f34d4eb76d417667e22f"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-reinforce-seed-29", "code_version": "d3ab665", "config": {"episodes": 300, "gamma": 0.95, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "501ce8f33658a9d9c56772d627e07387c13f472ea7305c16b8d74763c8d9a64a", "method": "reinforce", "metrics": {"average_steps": 2.03, "episodes": 300.0, "invalid_actions": 0.0, "mean_return": 0.43073333333333336, "success_rate": 0.7433333333333333}, "reference_weight_sha256": null, "schema_version": "1", "seed": 29, "weight_path": "output/phase12/weights-reinforce-29.json", "weight_sha256": "d71c3ef157e93b2d7553dd57b36f17494efed7e8f2811b6e44d603baf1d2a12b"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-reinforce-seed-43", "code_version": "399a373", "config": {"episodes": 300, "gamma": 0.95, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "a12e4b50d64a06f273b28a178efc898b4f0273832ce8a11fb59de899212db858", "method": "reinforce", "metrics": {"average_steps": 1.96, "episodes": 300.0, "invalid_actions": 0.0, "mean_return": 0.3774666666666666, "success_rate": 0.7166666666666667}, "reference_weight_sha256": null, "schema_version": "1", "seed": 43, "weight_path": "output/phase12/weights-reinforce-43.json", "weight_sha256": "4793ee4c1684503a6652639896109bf7756cc31ee97872702293f7dbfa26a95f"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-sft-seed-17", "code_version": "94a9be4", "config": {"epochs": 100, "learning_rate": 0.01}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "4d0f65a462aaeb58ab8a9b678a99105e8f9ad23635ef885fb6bf20fa2bb53cc1", "method": "sft", "metrics": {"dev_examples": 48.0, "final_dev_loss": 0.1315225064754486, "train_examples": 144.0}, "reference_weight_sha256": null, "schema_version": "1", "seed": 17, "weight_path": "output/phase12/weights-sft-17.json", "weight_sha256": "11f402273ce1e86c1a2c879f8328c4b7b1302c988d2ea59fbcde3eb0d533d1a2"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-sft-seed-29", "code_version": "49a88ef", "config": {"epochs": 100, "learning_rate": 0.01}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "50365dba6b7402a847a44376b15979bff5a85e0fafd12db0458db3ef68944be1", "method": "sft", "metrics": {"dev_examples": 48.0, "final_dev_loss": 0.13240975141525269, "train_examples": 144.0}, "reference_weight_sha256": null, "schema_version": "1", "seed": 29, "weight_path": "output/phase12/weights-sft-29.json", "weight_sha256": "501ce8f33658a9d9c56772d627e07387c13f472ea7305c16b8d74763c8d9a64a"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-sft-seed-43", "code_version": "5b7429e", "config": {"epochs": 100, "learning_rate": 0.01}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "e0ec7a0b38869414175e00bda0205437756757b906191bcaa845a170ecb50b4d", "method": "sft", "metrics": {"dev_examples": 48.0, "final_dev_loss": 0.16406655311584473, "train_examples": 144.0}, "reference_weight_sha256": null, "schema_version": "1", "seed": 43, "weight_path": "output/phase12/weights-sft-43.json", "weight_sha256": "a12e4b50d64a06f273b28a178efc898b4f0273832ce8a11fb59de899212db858"}]`. SFT, 标准 DPO 和 REINFORCE 均需真实参数更新及新进程读回; 它们仍是本地小模型实验.
奖励黑客证据: `{"actions": ["purchase_order.open", "purchase_order.open", "purchase_order.open", "FINISH"], "bad_reward_higher": true, "bad_reward_total": 1.1400000000000001, "case_id": "phase12-complete-read-00-v1", "final_status": "NO_PROGRESS", "is_prespecified_negative": true, "safe_reward_total": -1.36, "task_verifier_passed": false}`.

## Adoption 与回滚

Prompt, Skill, SFT, DPO, RL 默认均为 `LAB_ONLY`; 没有净收益或证据不足时保留基线. 选择前校验父版本和评测证据, 回滚写新 receipt 并验证父版本内容, 不覆盖旧 artifact.

## Rubric (暂定)

`{"D1_business_correctness": 3, "D2_identity_scope": 4, "D3_state_idempotency_recovery": 3, "D4_agent_trust_cost": 3, "D5_security_data_protection": 4, "D6_ui_accessibility_bilingual": 3, "D7_testing_reproduction": 3, "D8_governance_traceability": 3, "D9_simplicity_operability": 3}`, 合计 `29/36`, 平均 `3.22`. 最终分数需在全量证据和独立审查后确认.

## 风险登记

- `train_test_or_oracle_leakage`: L=3 x I=3, MITIGATED_BY_GROUPED_MANIFEST.
- `candidate_capability_expansion`: L=2 x I=4, MITIGATED_BY_BOUNDARY_DIGEST.
- `sensitive_trace_exfiltration`: L=2 x I=4, MITIGATED_BY_REDACTED_SYNTHETIC_INPUT.
- `self_eval_or_reward_substitutes_for_correctness`: L=3 x I=3, MITIGATED_BY_SEPARATE_VERIFIER.
- `statistics_or_successful_trial_selection`: L=3 x I=3, OPEN_UNTIL_FINAL_EVIDENCE_REVIEW.
- `budget_reset_after_interruption`: L=2 x I=3, MITIGATED_BY_REQUEST_RESERVATION.
- `arbitrary_code_in_weight_artifact`: L=2 x I=4, MITIGATED_BY_FINITE_JSON_ONLY.
- `small_policy_cannot_learn_task`: L=2 x I=2, REPORT_LIMITATION.
- `harness_sync_without_authorization`: L=2 x I=3, WAITING_FOR_SEPARATE_SYNC_APPROVAL.

## 限制与未运行项

当前报告不把 replay 当作真实模型质量, 不把本地训练当作业务语言模型微调; 真实 assist Provider held-out 已记录一轮 24 条并保留失败和 UNKNOWN; 三次重复重跑因 Provider 长连接无响应而中断且未写入半批. 全量 make integration, 最终 Harness 同步授权 和独立对抗审查仍必须以实际退出码更新.
