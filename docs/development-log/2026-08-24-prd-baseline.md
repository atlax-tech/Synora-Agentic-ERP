# 2026-08-24 PRD Baseline

## 完成内容

- 使用项目级 `prd-writer` Skill，把已批准的概念方向展开为落地 PRD。
- `docs/PRD.md` 补齐了用户、场景、主流程与异常分支、功能优先级、关键页面线框图、核心功能、状态机、数据字段、文案、非功能需求、测试验收、Benchmark 和待确认项。
- 明确保留 PO Submit、Receipt、Invoice、Payment 的后续需求；第一阶段 MR/PO Draft 是交付拆分，不是范围删除。
- 增加 Multi-Agent 的引入条件、候选角色、收益风险和 A/B 评测门禁，以及从 FTS5 到 Vector/Hybrid/Rerank 的完整 RAG 演进要求。
- 更新 `AGENTS.md`，把 `docs/PRD.md` 设为产品需求入口，并禁止实现 Agent 为降低复杂度删除已批准需求。

## 验证结果

- 完整读取 `prd-writer/SKILL.md` 的两阶段方法、三视角、落地版结构和“不足信息标待补充”的规则。
- 逐项检查 PRD：包含产品概述、目标用户/场景、Mermaid 动线、功能树、ASCII 线框图、核心功能细节、状态、数据规范、文案、非功能需求和待确认问题。
- PRD fingerprint：`59588ef56623be91b8f854d414a1bb62435bcb150e676db2b3a0cb468214824e`，已加入 Harness source index。

## 限制与未验证项

- Skill 直接下载因本机 Python CA 校验失败，已按 Skill Installer 的 fallback 使用 Git 安装；安装产生的嵌套 Git 元数据已移到临时目录，不进入项目。
- 角色权限矩阵、审批阈值、性能/并发/保留期、模型、LangGraph checkpoint 和完整 ERP baseline 尚未验证，PRD 中均标为待确认。
- 本轮没有创建业务代码或假设上述未决项的答案。

## 人工验收步骤

1. 打开 `docs/PRD.md`，确认 F-009 至 F-012 仍包含 PO Submit、Receipt、Invoice 和 Payment。
2. 检查第 5、6、8 节，确认正常、无权限、状态漂移、重复执行和对账状态都有要求。
3. 检查第 12 节，确认实现前必须解决的问题没有被 Skill 自由补全。
