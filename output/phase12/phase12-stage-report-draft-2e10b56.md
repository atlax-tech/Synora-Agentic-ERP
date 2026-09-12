# Phase 12 阶段报告

状态: `DRAFT / LAB_ONLY`. 独立对抗审查和 Harness 文件级同步尚未闭合, 不能写阶段 PASS.

## 业务问题与数据流

历史失败证据先经过来源, 完整性, 脱敏和相关性审核; 合成采购场景再按组冻结为 train/dev/test. 模型或小策略只看到允许的任务输入和观察状态, 输出经安全门禁与确定性 verifier, oracle 只留在评分侧. 结果, 调用预算, 训练权重和选择回执都写入 `output/phase12/`.

数据集 `phase12-synthetic-v1` / digest `5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb`, split `{'dev': 24, 'test': 24, 'train': 72}`, groups `{'dev': 12, 'test': 12, 'train': 36}`.
证据记录代码版本 `['13872f1', '7b5e435']`; 修复重跑可能保留多个版本。

## 方法结果

| method | count | verifier | safety | score | calls | unknown usage | usage n/a | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 144 | 1.000 | 1.000 | 1.000 | 0 | 0 | 144 | 0.0 |
| best-of-3 | 72 | 1.000 | 1.000 | 1.000 | 216 | 0 | 72 | 0.0 |
| live-baseline | 24 | 0.125 | 0.542 | -0.750 | 24 | 8 | 0 | 7928.1 |
| prompt-candidate | 144 | 1.000 | 1.000 | 1.000 | 0 | 0 | 144 | 0.0 |
| reflection | 72 | 1.000 | 1.000 | 1.000 | 144 | 0 | 72 | 0.0 |
| skill-candidate | 144 | 1.000 | 1.000 | 1.000 | 0 | 0 | 144 | 0.0 |

失败, UNKNOWN, 未知 usage 和部分生成均保留在分母; 重复运行不是新增独立样本.

## Held-out 规则

按场景组 bootstrap 2,000 次: `[{"conclusion": "INCONCLUSIVE", "delta": 0.0, "groups": 12, "lower_95": 0.0, "method_a": "baseline", "method_b": "prompt-candidate", "samples": 2000, "upper_95": 0.0}, {"conclusion": "INCONCLUSIVE", "delta": 0.0, "groups": 12, "lower_95": 0.0, "method_a": "baseline", "method_b": "skill-candidate", "samples": 2000, "upper_95": 0.0}]`. 区间下界大于 0 才能称固定实验有改善证据; 跨 0 为 `INCONCLUSIVE`, 退化为 `REJECTED`.

## 训练与奖励

训练 artifacts: `[{"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-dpo-seed-17", "code_version": "2e10b56", "config": {"beta": 0.1, "epochs": 100, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "99f3e59b0078ccce47bf716d3d0e95992f9a86076a56ea84ed1086577f6557f7", "method": "dpo", "metrics": {"dev_preference_pairs": 20.0, "final_dpo_loss": 0.43033909797668457, "preference_pairs": 60.0}, "reference_weight_sha256": "99f3e59b0078ccce47bf716d3d0e95992f9a86076a56ea84ed1086577f6557f7", "schema_version": "1", "seed": 17, "weight_path": "output/phase12/weights-dpo-17.json", "weight_sha256": "ea6478c08fd3d59f955e2aa4141a8063af7f15ed5c7ebfb6ee7923ba97344024"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-dpo-seed-29", "code_version": "2e10b56", "config": {"beta": 0.1, "epochs": 100, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "354214e30b25b71c4d3c529d74487a288b46708cd03eef5e37360941ab18714e", "method": "dpo", "metrics": {"dev_preference_pairs": 20.0, "final_dpo_loss": 0.4226168096065521, "preference_pairs": 60.0}, "reference_weight_sha256": "354214e30b25b71c4d3c529d74487a288b46708cd03eef5e37360941ab18714e", "schema_version": "1", "seed": 29, "weight_path": "output/phase12/weights-dpo-29.json", "weight_sha256": "6802b1749676491dcf6c7f0f50d928d0c2ccb6cddbf6f518fb8c50d55bcee10f"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-dpo-seed-43", "code_version": "2e10b56", "config": {"beta": 0.1, "epochs": 100, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "2d99457114b35ba68b8b1551d773ddfd547a253f233e14ce8250f98e1e976635", "method": "dpo", "metrics": {"dev_preference_pairs": 20.0, "final_dpo_loss": 0.42966800928115845, "preference_pairs": 60.0}, "reference_weight_sha256": "2d99457114b35ba68b8b1551d773ddfd547a253f233e14ce8250f98e1e976635", "schema_version": "1", "seed": 43, "weight_path": "output/phase12/weights-dpo-43.json", "weight_sha256": "e8b8b22418a7678a3e3e37035e16791b2b0630cf16fdeabf2d11102629d754d1"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-reinforce-seed-17", "code_version": "2e10b56", "config": {"episodes": 300, "gamma": 0.95, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "99f3e59b0078ccce47bf716d3d0e95992f9a86076a56ea84ed1086577f6557f7", "method": "reinforce", "metrics": {"average_steps": 1.5633333333333332, "episodes": 300.0, "invalid_actions": 0.0, "mean_return": 0.36206666666666665, "success_rate": 0.7}, "reference_weight_sha256": null, "schema_version": "1", "seed": 17, "weight_path": "output/phase12/weights-reinforce-17.json", "weight_sha256": "939785d3e25ad87e4103e5f14999986dba72a73d94b08bfc9b08253276ef887b"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-reinforce-seed-29", "code_version": "2e10b56", "config": {"episodes": 300, "gamma": 0.95, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "354214e30b25b71c4d3c529d74487a288b46708cd03eef5e37360941ab18714e", "method": "reinforce", "metrics": {"average_steps": 1.5066666666666666, "episodes": 300.0, "invalid_actions": 0.0, "mean_return": 0.43520000000000003, "success_rate": 0.7333333333333333}, "reference_weight_sha256": null, "schema_version": "1", "seed": 29, "weight_path": "output/phase12/weights-reinforce-29.json", "weight_sha256": "c495a6a9c3a35b64edba13343ff1cda5c7bcf83803370c03d9676cd7f30d8131"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-reinforce-seed-43", "code_version": "2e10b56", "config": {"episodes": 300, "gamma": 0.95, "learning_rate": 0.001}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "2d99457114b35ba68b8b1551d773ddfd547a253f233e14ce8250f98e1e976635", "method": "reinforce", "metrics": {"average_steps": 1.5766666666666667, "episodes": 300.0, "invalid_actions": 0.0, "mean_return": 0.4498, "success_rate": 0.7433333333333333}, "reference_weight_sha256": null, "schema_version": "1", "seed": 43, "weight_path": "output/phase12/weights-reinforce-43.json", "weight_sha256": "7083def8ab19a49506fec8dcfe4d09bdb034125715861bc3e03aec64382ef199"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-sft-seed-17", "code_version": "2e10b56", "config": {"epochs": 100, "learning_rate": 0.01}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "4d0f65a462aaeb58ab8a9b678a99105e8f9ad23635ef885fb6bf20fa2bb53cc1", "method": "sft", "metrics": {"dev_examples": 36.0, "final_dev_loss": 0.14365726709365845, "train_examples": 108.0}, "reference_weight_sha256": null, "schema_version": "1", "seed": 17, "weight_path": "output/phase12/weights-sft-17.json", "weight_sha256": "99f3e59b0078ccce47bf716d3d0e95992f9a86076a56ea84ed1086577f6557f7"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-sft-seed-29", "code_version": "2e10b56", "config": {"epochs": 100, "learning_rate": 0.01}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "50365dba6b7402a847a44376b15979bff5a85e0fafd12db0458db3ef68944be1", "method": "sft", "metrics": {"dev_examples": 36.0, "final_dev_loss": 0.1300159990787506, "train_examples": 108.0}, "reference_weight_sha256": null, "schema_version": "1", "seed": 29, "weight_path": "output/phase12/weights-sft-29.json", "weight_sha256": "354214e30b25b71c4d3c529d74487a288b46708cd03eef5e37360941ab18714e"}, {"action_version": "phase12-actions-v1", "artifact_id": "phase12-train-sft-seed-43", "code_version": "2e10b56", "config": {"epochs": 100, "learning_rate": 0.01}, "dataset_digest": "5abe51454c4408e2235209323c6dd8d9d0ffec5b9235f6ac1cb3d933beeb19eb", "dataset_id": "phase12-synthetic-v1", "feature_version": "phase12-features-v1", "initial_weight_sha256": "e0ec7a0b38869414175e00bda0205437756757b906191bcaa845a170ecb50b4d", "method": "sft", "metrics": {"dev_examples": 36.0, "final_dev_loss": 0.1384718120098114, "train_examples": 108.0}, "reference_weight_sha256": null, "schema_version": "1", "seed": 43, "weight_path": "output/phase12/weights-sft-43.json", "weight_sha256": "2d99457114b35ba68b8b1551d773ddfd547a253f233e14ce8250f98e1e976635"}]`. SFT, 标准 DPO 和 REINFORCE 均需真实参数更新及新进程读回; 它们仍是本地小模型实验.
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
