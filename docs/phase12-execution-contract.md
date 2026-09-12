# Phase 12 离线实验契约

状态：`IN_PROGRESS / LAB_ONLY`。本契约冻结 Phase 12 的实验边界；它不授权业务 Runtime、ERP/Frappe 或生产配置发生变化。

## 目标和边界

本阶段验证失败轨迹驱动的 Prompt/Skill 候选、Reflection、Best-of-N、SFT、DPO 和 Agentic RL 前置实验。所有输入默认为合成或已经审核脱敏的数据，所有候选和模型只存在 `output/phase12/` 的实验空间。业务 Prompt、Skill、policy、permission、tools、ERP 数据库和 `.env*` 不会被自动改写。

## 固定数据和预算

- 六类采购调查场景，每类十个场景组；每组两个变体，共 120 个案例。
- 先按场景组划分 train/dev/test：72/24/24；同组和近重复不得跨 split。
- Prompt/Skill、oracle、奖励和 split 标签只能在评分侧使用，不能进入模型输入或候选排序。
- 真实 Provider 调用累计上限 1,200 次；单次输入最多 4,000 字符、输出最多 512 tokens；并发为 1；不自动重试。
- 训练 seed 固定为 17、29、43；CPU 两层 MLP，隐藏层 32；每 episode 最多 8 步。

## 版本和证据

每个数据集、候选、实验、训练权重和选择回执都记录 schema/code/data digest。已有 artifact 不覆盖；损坏、泄漏或评分器变更会使下游结果失效并创建新的批次 ID。哈希用于复现，不是签名或授权。

## 提交和协作

用户已授权 Agent 全程编码，本阶段不创建 Assignment、不生成学习笔记、不启用 codex-with-chatgpt；阶段内只更新 Phase 12 开发日志。每个原子步骤单独验证并提交，阶段末才进行一次独立对抗审查。所有文档中的训练结果、模型质量、成本和采用结论必须以已运行 artifact 为准。

## 当前基线

- 起始代码 HEAD：`ffb5b81`。
- Phase 11 出口：`COMPLETED / PASS / READY FOR NEXT PHASE`，报告位于 `output/phase11/phase11-stage-report-final-98ee158.md`。
- `PromptRegistry`、Skill registry、typed read-only contracts 和既有 evaluator 是本阶段可复用的公开边界。
- PyTorch 2.13.0 已在项目虚拟环境中完成 CPU autograd 探测；缺少可选训练依赖时必须明确失败，不能以固定回放替代真实更新。

## 出口判定

出口必须同时具备：数据审核与分组隔离、可运行的比较实验、真实参数更新及新进程读回、至少一个无收益或奖励副作用结果、可验证回滚、held-out 评测、风险和九维 Rubric、全量检查以及独立审查 `PASS`。未满足任一项不得把 Phase 12 标为完成。
