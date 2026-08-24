# 2026-08-24 Harness Bootstrap

## 完成内容

- 把用户对一期范围、Multi-Agent、RAG、企业级标准和 SPEC 的条件审核写回临时评审底稿；该底稿继续保持未提交。
- 从本地 Harness Armor 0.1.2 安装 7 个项目级 Codex Skills 到 `.agents/skills/`。
- 运行只读路由和仓库扫描。扫描脚本因临时评审稿无法自动分类，人工依据已批准的实质产品内容将仓库判定为 `DOCS_ONLY`，路由到 `harness-build`。
- 创建短 `AGENTS.md`、产品/架构/设计/开发/测试/验收/路线图文档，以及 `.harness` 的 manifest、source index 和 unresolved index。
- 保留 `README.md`、`LICENSE` 和临时评审稿为 user-owned source，没有覆盖它们。

## 验证结果

- `node ../harness-armor/bin/harness-armor.js doctor --target codex-project --project-root ...`：通过；7 个 Skills、77 个安装文件、完整性检查均正常。
- `python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .`：通过；manifest 有效，66 个引用完成检查，0 个断链。
- `python3 .agents/skills/harness/scripts/detect_repository_state.py .`：返回 `MANAGED_HARNESS`，置信度 0.99，无未确定项。

## 限制与未验证项

- Harness Armor 上游声明 Codex 真实客户端自动发现仍缺少其发布证据。本次已验证项目安装、完整性和脚本实际调用，但当前会话的启动时 Skill 清单不会自动刷新；需要在新会话再验证原生自动发现。
- 当前只有文档与 Harness，没有业务代码、依赖清单、ERPNext Runtime 或产品测试命令。
- Frappe/ERPNext commit pair、用户身份传播、模型、LangGraph checkpoint 和第三方许可证边界仍记录为 `UNRESOLVED`。

## 人工验收步骤

1. 查看 `.agents/skills/`，确认存在 harness、harness-build、harness-check 等 7 个目录。
2. 运行 `python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .`，确认返回 `"valid": true`。
3. 查看 `docs/ROADMAP.md`，确认 Receipt、Invoice、Payment 和 Multi-Agent/RAG 演进没有因一期拆分被删除。
