# 2026-08-24 Architecture and Technology Baseline

## 完成内容

- 在现有 `docs/ARCHITECTURE.md` 中补充审批权威边界和技术选型状态表，没有新建重复的技术选型文档。
- 区分已经确认的目标、需要 Spike 或评测的条件式候选，以及尚未验证的具体版本。
- 明确第一版不默认引入 Multi-Agent、任意工具、向量数据库、Kafka、Kubernetes 或付费追踪平台，但保留证据驱动的后续采用门槛。
- 将用户确认的 Draft/Submit 审批基线同步到架构，并登记具体 ERPNext Workflow/Role 映射仍未解决。
- `docs/DESIGN.md` 只同步前端审批交互的已决/未决边界，没有重新混入后端设计。

## 验证结果

- 技术表中的版本、模型、LangGraph、checkpoint 和工具命令均没有被描述成已经安装或运行验证。
- FTS5 优先、完整 RAG 演进和条件式 Multi-Agent 接口继续保留。
- 完整 P2P 范围和 PO Submit 之后的独立审批要求没有降低。

## 人工验收步骤

1. 检查 `docs/ARCHITECTURE.md` 的 Technology Selection and Adoption Gates，确认每项都有状态或采用门槛。
2. 检查 Approval and Workflow Authority，确认 Draft 与 Submit/后续写操作的职责边界。
3. 确认项目中没有新增独立技术选型文档，`docs/DESIGN.md` 仍是前端设计宪章。
