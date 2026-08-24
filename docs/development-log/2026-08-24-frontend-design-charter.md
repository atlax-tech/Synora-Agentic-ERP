# 2026-08-24 Frontend Design Charter Correction

## 完成内容

- 将 `docs/DESIGN.md` 从错误的后端工作流设计改为前端设计宪章。
- 明确 DESIGN 只负责信息架构、交互状态、风险呈现、可解释性、视觉系统边界、可访问性和前端验收。
- 将产品范围、状态机、业务流程、RAG 和 Multi-Agent 的权威归属指回 `docs/PRD.md`、`docs/ARCHITECTURE.md` 和后续 `docs/SPEC.md`，不在前端文档中重复维护。
- 修正 unresolved index 中指向旧 DESIGN 章节的引用，并登记尚未验证的 Frappe 前端基线。
- 更新 `AGENTS.md`，明确 DESIGN 是 Frontend Design Constitution。
- 移除正式 Harness 对未提交临时评审文件的依赖；批准结论继续保留在 PRD、架构、测试、验收和路线图中。

## 验证结果

- 原 DESIGN 中的流程、状态、安全、RAG 和 Multi-Agent 要求均已在 PRD 或架构文档中找到权威归属，没有删除已批准需求。
- 新 DESIGN 覆盖正常、空、加载、权限拒绝、过期、失败和对账状态。
- 最终视觉 token、浏览器、viewport、可访问性等级和中英文术语没有被自由补全，均保留为未决事项。
- Harness 健康审计发现临时来源会导致新克隆仓库缺少证据，已将正式来源改为可提交的权威文档；临时文件本身保持未提交。
- Manifest schema 校验通过，Harness drift 为 0。
- 结构与引用检查通过：93 个显式或已存在的内联引用，0 个断链，扫描未截断。
- Harness 机器健康评分为 87/100；结合已读取的 PRD、架构、开发、测试、验收和路线图进行语义复核后为 95/100，无阻塞或高优先级问题。
- 剩余扣分来自尚无业务实现、业务构建测试命令尚未产生，以及非 Codex 客户端兼容性未验证；没有将这些未完成项伪装为通过。

## 人工验收步骤

1. 打开 `docs/DESIGN.md`，确认 Purpose and Boundary 明确排除后端工作流、API、RAG 和 Multi-Agent 架构定义。
2. 检查 State and Feedback Contract，确认企业流程中的失败和结果不确定状态不是普通提示消息。
3. 检查 `.harness/unresolved.json`，确认 Multi-Agent 与 RAG 引用已指向架构文档，前端细节仍是未决项。
