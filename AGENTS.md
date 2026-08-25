# Synora Agentic ERP

Synora 是构建在 ERPNext 上的受治理 Agentic Enterprise Operations 产品。ERPNext/Frappe 是只读上游和事务事实源；不得修改上游核心、直写 ERP 数据库，或绕过权限、校验、Workflow 与审计。

## 阅读顺序

1. `docs/PLAN.md`：当前阶段、工作方式与停止条件；
2. 当前任务直接涉及的 `docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/DESIGN.md`、`docs/SPEC.md`；
3. 对应阶段的 `docs/development-log/`、ADR、源码和测试。

只读完整仓库不是默认动作。普通增量只加载当前业务链路所需上下文；ERP 行为不明确时再查固定上游源码和官方测试。

## 不可简化的边界

- 模型输出、检索内容、ERP 字段和用户输入都不可信。
- 业务写入必须经过 typed validation、policy、当前状态重检、明确授权、幂等和执行回执。
- 不得为实现方便删除已批准需求；延期项写入 Roadmap/SPEC。
- Mock 只能是明确允许的 test double，不能替代真实 ERP 集成完成度。
- 不得声称生产部署、客户采用或未被证据支持的收益。

## 精简工作流

- 编码、修复、重构、代码审查和依赖选择前读取 `.agents/skills/ponytail/SKILL.md`，默认 `full`。
- 普通代码、页面和文案：执行 Agent 自测 + `ponytail-review`；不强制子 Agent。
- 身份、权限、金额、状态机、ERP 写入、幂等、审计和安全边界：增加一个独立 Test 或 Review；风险同时涉及多个边界时才两者都用。
- 阶段出口、发布、Tag、产品版本、依赖基线或固定 ERP/Frappe 版本变更：运行完整验证和独立对抗审查。
- 先跑最相关检查，提交前再运行该增量必要的较宽检查；失败后只重跑受影响检查，阶段出口才全量运行。
- Harness 只在权威事实、命令、阶段状态或管理文件实际变化时同步；README 只在公开事实变化或阶段出口更新。
- 每个 commit 前只在当前阶段的一份日志顶部新增一轮记录，格式见 `docs/development-log/README.md`；不再为小修复创建独立日志文件。
- 使用小而完整的 Conventional Commit；不混入用户文件，不推送，不改写历史。

## 实习生协作

默认把用户视为 Agent 开发实习生。适合学习的小任务应先作为练习交给用户，说明业务背景、代码入口、完成标准和测试设计；除非任务超出其能力、安全风险过高或用户明确要求接手，否则 Agent 只提示，不代写。

必要时可在练习入口添加清晰的 `TODO(learning)` 提示，但不得把安全门禁、生产缺陷或阶段关键路径整体交给用户。用户的疑问、Agent 的解释和最终结论记录在当前阶段日志的“大白话讲解”或“面试追问”部分。

## 变更说明

每次交付先用大白话说明：解决什么业务问题、用户能看到什么、数据怎样流动、最重要的三个文件、怎样手工验证。随后再报告实际命令、退出码、限制和未运行检查。

验证入口：

```bash
make format-check
make lint
make type
make unit
make integration
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
```

只运行与当前增量和风险相称的命令；不得把未运行命令写成通过。
