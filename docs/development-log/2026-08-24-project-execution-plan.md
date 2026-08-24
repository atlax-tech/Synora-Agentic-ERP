# 2026-08-24 Project Execution Plan

## 完成内容

- 新增 `docs/PLAN.md`，把 Roadmap 的 Phase 0 至 Phase 8 展开为 Coding Agent 可以机械执行的阶段计划。
- 在根 `AGENTS.md` 增加 PLAN 入口和固定阅读顺序，让“开始完成阶段 X”“开始完成下个阶段”“继续工作”具有唯一解释。
- 明确每个代码增量必须经过 Execute、独立 Test、独立 Review、修正重跑和小步提交闭环，并要求审查 Agent 主动使用对抗场景挑战实现。
- 为每个阶段写明必读内容、工作顺序、Skill 调用、停止条件、上下阶段衔接和出口证据。
- 将现有 Harness 未决项映射到最迟解决阶段，并区分可以由运行证据收敛的技术事实与必须交给用户批准的产品、安全、权限、阈值和许可证决定。
- 将 PLAN 登记为 Harness 管理的确认计划来源，同步 manifest 和 source index；没有修改 PRD、架构、设计、SPEC、Roadmap、README 或 unresolved index。

## 为什么这样改

原有文档已经说明“产品是什么”和“各阶段交付什么”，但还缺少一份把用户短指令转换成严格开发动作的执行协议。新增 PLAN 后，Coding Agent 不需要用户重复粘贴项目背景，也不能自己选择开发顺序、跳过上阶段证据或把自测当作独立审核。

PLAN 只负责工作顺序和门禁。产品范围、架构、设计、工程契约、测试和验收仍由原有权威文档决定，避免出现第二份 PRD 或重复项目地图。

## 验证结果

- `git diff --check`：通过，无空白错误。
- `python3 .agents/skills/harness-update/scripts/validate_manifest.py .`：通过；manifest schema 有效，0 个错误和警告。
- `python3 .agents/skills/harness-update/scripts/validate_harness_structure.py .`：通过；结构有效，检查 165 个引用，0 个断链。
- `python3 .agents/skills/harness-update/scripts/detect_drift.py .`：通过；manifest 有效，`has_drift=false`。
- `python3 .agents/skills/harness-check/scripts/check_references.py .`：通过；检查 165 个引用，0 个断链，扫描未截断。
- `python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .`：通过；使用项目已验证命令再次确认结构有效。
- 内容覆盖检查确认 Phase 0 至 Phase 8、F-001 至 F-015、10 个 Harness unresolved ID、三种阶段命令和三角色 verdict 均出现在 PLAN 中。
- 第一轮独立 Test 返回 `PASS`：确认阶段、需求、未决项、角色隔离、Skill 路由、停止条件、Harness 状态和禁止文件边界全部满足要求。
- 第一轮独立对抗 Review 返回 `CHANGES_REQUIRED`：发现同 key/同 digest 的幂等重放被错误描述为拒绝，以及 RAG “明确必要性”可能绕过可测收益门禁。
- 已按 SPEC §11.1 明确同 key/同 digest 返回已有已验证结果、不同 digest 才冲突；已删除 RAG 的替代准入条件，只允许同一评测集上的可测净收益且无治理回退。
- 修正后的独立 Test 再次返回 `PASS`：逐条确认幂等 replay 语义、RAG 准入门、Harness 哈希、165 个引用、0 drift、禁止文件和用户临时文件边界。
- 修正后的独立对抗 Review 返回 `PASS`，确认两项权威冲突已消除，可以进入提交前最终校验。

## 限制与未验证项

- 当前仍没有业务代码、ERPNext Runtime、依赖清单或产品构建测试命令；PLAN 没有把这些目标写成已实现事实。
- Frappe/ERPNext commit pair、Runtime 用户绑定授权、具体 Workflow/Role、模型、向量和 Multi-Agent 阈值、前端基线及许可证边界继续保持未决，并由 PLAN 指定阶段解决。
- 本次只完成 Phase 0 的执行计划入口，不开始 Phase 1，也不执行发布、Tag、部署或 README 声明更新。

## 人工验收步骤

1. 打开 `AGENTS.md`，确认项目地图首先指向 `docs/PLAN.md`，并定义三种阶段指令。
2. 打开 `docs/PLAN.md`，确认包含证据驱动的阶段选择、Execute/Test/Review 闭环、Skill 表、未决项路由和 Phase 0 至 Phase 8。
3. 对照 `docs/ROADMAP.md` 和 `docs/SPEC.md`，确认完整 P2P、RAG 与 Multi-Agent 条件准入均未删除或提前声明完成。
4. 运行 Harness 结构、引用和 drift 命令，确认 manifest 有效、0 个断链且 0 drift。
5. 确认 `.synora-product-architecture-review.tmp.md` 仍是未跟踪的用户文件，没有进入本次 diff 或提交。
