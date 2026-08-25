# Phase 2 阶段出口报告（Typed 只读 ERP Gateway）

日期：2026-08-25 ｜ 状态：Phase 2 完成，已停止，未进入 Phase 3

## 1. 完成的步骤与用户可见结果

| 步骤 | 结果 | 证据 |
| --- | --- | --- |
| P2.1 最小工程骨架 | 根目录可安装 Frappe App + 独立 `services/agent_runtime` Python 边界；format/lint/type/unit/integration/runtime 命令登记并验证 | 提交 `e10a4dd`；`2026-08-24-phase2-p2_1-engineering-skeleton.md`、`p2_1-harness-sync.md` |
| P2.2 身份授权 Spike | Frappe 登录态/Cookie/API key/OAuth/auth_hooks 取证；ADR-0003 用户批准为 `APPROVED` | 提交 `651380b`；`2026-08-25-phase2-p2_2-identity-authorization-spike.md`；`docs/security/phase2-p2_2-identity-authorization-spike.md` |
| P2.3 Gateway 契约 | Synora Agent Run 记录、5 分钟 opaque capability（仅存 SHA-256 摘要）、版本化 typed envelope、固定工具注册表、不可变调用审计；`runtime-user-authorization` 未决项 → RESOLVED | 提交 `527a0d7`；`2026-08-25-phase2-p2_3-gateway-contract.md` |
| P2.4 只读工具 | 六个真实 ERP 只读工具（Item/Supplier/projected stock/open demand/open MR/open PO），固定源码 `11e0ba0` 口径，get_list + 显式 initiator，无直接 SQL/内部 import | 提交 `032bcbd`；`2026-08-25-phase2-p2_4-read-tools.md` |
| P2.5 Runtime 边界 | typed HTTPX Gateway client：严格 Pydantic、固定路径/唯一 origin、无用户凭据、capability 脱敏、fail closed、响应强匹配、2MB 流式上限；架构测试证明 Runtime 无 ERP 数据库/内部 import 路径 | 提交 `fb32bad`；`2026-08-25-phase2-p2_5-runtime-client.md` |
| P2.6 真实验证 | 真实 Bench HTTP 端到端 11 场景全过（含暴露并修复 P2.3 的 Frappe `cmd` 注入契约缺陷、补齐 ADR-0003 第二公司 fixture）；验证命令进入 DEVELOPMENT | 提交 `733da89`；`2026-08-25-phase2-p2_6-real-http-verification.md` |

用户可见结果：Phase 2 之后，Agent 只读侧（Runtime → Gateway → ERP）已形成完整、可验证、fail-closed 的链路：发起人绑定、公司/仓库 scope、权限继承、真实 ERP 数据、确定性过滤（停用/取消/分页/版本）、全链路 capability 脱敏，且全部通过真实 HTTP 验证。`product-commands`、`runtime-user-authorization` 未决项已解决。

## 2. 提交列表与文件边界（本会话新增）

- `79ec690` chore: ignore IDE local configuration directory — `.gitignore` + 仓库卫生日志
- `fb32bad` feat: add phase 2 runtime gateway client — `services/agent_runtime/src/agent_runtime/gateway.py`、`services/agent_runtime/tests/test_gateway.py`（23 测试）、`tests/test_runtime_boundary.py`、P2.5 日志
- `733da89` feat: add phase 2 real http verification — `synora_agentic_erp/api.py`（cmd 剥离修复）、`synora_agentic_erp/tests/test_gateway_contract.py`（+1 测试）、`env/dev/p26/p26_data.py`、`env/dev/p26/p26_e2e.py`、P2.6 日志
- 本出口报告增量：`docs/DEVELOPMENT.md`（P2.6 命令登记）+ 本文档

文件边界：未修改 Frappe/ERPNext 上游；未修改 `docs/PLAN.md`；未引入 Mock/占位替代真实 ERP 完成度；P2.6 的数据准备脚本自带清理且仅影响 SYNORA-P26 命名空间。

## 3. 实际运行命令、退出码与证据位置

| 命令 | 退出码 | 关键输出 | 证据位置 |
| --- | --- | --- | --- |
| `make format-check` / `lint` / `type` / `unit` | 0 | 76 files formatted / All checks passed / mypy 18 files / 29 passed | P2.5、P2.6 日志；本轮复跑 |
| `make integration`（Bench app-test） | 0 | Ran 23 tests OK（含 cmd 剥离用例） | P2.6 日志；本轮复跑 |
| `env/dev/p26/p26_data.py`（bench console） | 0 | `P26-DATA-OK`（幂等，连跑两次不累积） | P2.6 日志；本轮复跑 |
| `env/dev/p26/p26_e2e.py`（真实 HTTP） | 0 | 11 行 `P26-*-OK` + `P26-E2E-OK` | P2.6 日志；本轮复跑 |
| 上游双 HEAD + porcelain | 0 | frappe `6a329d0`、erpnext `11e0ba0`，无输出 | P2.6 日志；本轮 |
| `validate_harness_structure.py .` | 0 | `valid: true` | 本轮 |
| `detect_drift.py .` | 0 | `has_drift: false` | 本轮 |

## 4. 独立 Test / Review / 对抗审查结论

- P2.5：独立 Test 三轮（FAIL → PASS → PASS）与 Review 三轮（CHANGES_REQUIRED → CHANGES_REQUIRED → PASS）。门禁暴露并修复：mypy `union-attr`、ruff 未格式化、开发日志失实；按 Review 补 4 个契约测试（29→unit 25 基线）；确认 `except A, B:` 为 PEP 758 + ruff 强制的合法写法。
- P2.6：独立 Test 两轮（PASS → PASS）与 Review 两轮（CHANGES_REQUIRED → PASS）。门禁暴露并修复：Frappe RPC `cmd` 注入导致真实 HTTP 下 `INVALID_INPUT`（服务端契约缺陷，补 `payload.pop("cmd")` + 回归测试）；数据清理声明不实、非幂等、跨公司 fixture 缺失（已按 Review 补齐第二公司 fixture + 自带幂等清理）。
- 对抗审查：每轮独立 Review 均提出对抗场景并有文件级证据；P2.5 的「scope/state_version/snapshot 无法被客户端校验」残余风险已在 P2.6 通过真实 issue_run 对照验证闭环。

## 5. ERP 上游保持干净

Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99` 与 ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325` 均与 ADR-0002 冻结一致，`git status --porcelain` 无输出；未修改上游任何文件。

## 6. 未运行检查、限制、未决项与被拒绝的技术

- 未运行：无。所有登记命令（format/lint/type/unit/integration/runtime 健康检查、真实 HTTP E2E、Harness 检查）均已实际运行并记录退出码。
- 限制：开发 site 为单语言（en）、单会计年度环境。跨公司已验证两层：数据隔离（run 限定公司 A 时不含公司 B 数据，`CROSS_COMPANY-OK`）与权限拒绝（同一用户有公司 A 权限、无公司 B 权限时，`issue_run(company=B)` 在发行阶段被 `SCOPE_DENIED` 拒绝，`AONLY_COMPANY_B_DENIED-OK`）。工具超时为 **post-hoc 耗时分类**（不中断执行，见出口修正增量），ERP 永久卡住由 Runtime HTTP deadline 兜底。
- 未决项：`approval-workflow-mapping` 按 PLAN 在 Phase 4 启用写入前完成（P2 仅取证不启用）；`frontend-design-baseline`、Goal 限制、`model-selection`、`workflow-engine-spike` 等属 Phase 3 未决项，未在本阶段触碰。
- 被拒绝/延期：未引入 LangGraph（P3.6 spike 前不采用）；未引入任何 ORM 直连/任意 URL/任意工具路径；Runtime 保持零 ERP 依赖；未采用进程隔离的执行截止（post-hoc 分类 + Runtime 兜底在只读阶段可接受，写操作阶段再引入）。
- **Ponytail 复杂度审计**（阶段出口，只读）：`Lean already`——本阶段新增代码（gateway.py 403 行、p26 脚本 531 行、api.py 增量）无过度设计、无死代码、无 TODO/FIXME；`# noqa: BLE001` 为 E2E 记录型捕获（合理）、`# type: ignore[untyped-decorator]` 为 Frappe 装饰器既有模式。
- **Ponytail 延期项（明确标注，不自动删除）**：
  - P3 `p26_data.py:38-41` MR 清理按 owner+docstatus 而非命名空间前缀，可能误删未来 BUYER 的 cancelled Purchase MR——测试环境可接受，后续如需精确化按 item 前缀过滤。
  - P3 `p26_e2e.py` CROSS_COMPANY 带 warehouse scope，company 过滤被仓库先行排除——出口修正增量已另补 `AONLY` 场景（warehouse=None 的 run）单独验证公司范围权限，该建议视为闭环。
  - P3 验证命令已在本出口报告增量登记 `docs/DEVELOPMENT.md`（已闭环）。
  - P3 安全事件日志落在 Frappe Error Log（无专用安全 Doctype），后续如需独立安全事件存储/告警属运维演进项。

## 7. 出口修正增量（超时语义诚实化 + Harness 语义一致化 + 三项建议）

用户复核后指出两个阻塞问题与三项建议，作为 Phase 2 出口修正增量处理（详见 `2026-08-25-phase2-exit-corrections.md`）：

1. **Gateway 超时语义诚实化**：确认 `timeout_ms` 为 post-hoc 耗时分类（不中断执行），注释/错误消息/SPEC §9 诚实化，测试更名并补 retryable 断言；真正执行截止留待 Phase 4 进程隔离。
2. **Harness 语义状态一致化**：`unresolved.json` 的 `runtime-user-authorization` → RESOLVED；`source-index.json` 补 11 条 Phase 2 证据（7→18）；README 双语更新为 Phase 0-2 完成状态；身份 Spike 状态 → `PHASE 2 COMPLETE`。
3. **建议项**：① aonly 用户跨公司权限拒绝测试（`issue_run(company=B)` 发行阶段 `SCOPE_DENIED`）；② 未解析 Run 的失败记录脱敏安全事件日志（`_log_security_event` + SPEC §14 策略 + 测试）；③ 服务端内部异常保留诊断日志（frappe.log_error 记录真实异常，响应保持脱敏）。

验证：宿主机 29 passed；Bench 集成 24 tests OK；真实 HTTP E2E **13/13**；Harness 无漂移。

## 8. 可重复人工验收步骤

```bash
# A. 单元/静态：make format-check && make lint && make type && make unit（29 passed）
# B. 服务端集成：make integration（Ran 24 tests OK）
# C. 真实 HTTP 端到端：
#   1) bench console 跑 env/dev/p26/p26_data.py（期望 P26-DATA-OK，重复安全）
#   2) 确认 bench web 在 127.0.0.1:8000
#   3) SYNORA_P2P_USER_PWD=<pwd> uv run --python 3.14 python env/dev/p26/p26_e2e.py
#      期望 13 行 P26-*-OK 与 P26-E2E-OK
# D. 上游干净：git -C apps/frappe status --porcelain 与 git -C apps/erpnext 均无输出
# E. Harness：python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
#    与 python3 .agents/skills/harness-check/scripts/detect_drift.py . 均无阻塞
```

## 9. 下一阶段：Phase 3（只读采购 Agent）尚未开始

Phase 3 需在用户明确指令下按 `docs/PLAN.md` §12 启动（P3.1 产品与前端决策包需用户批准）。Phase 2 出口证据已全部就位，但 PLAN §3「阶段出口通过后提交阶段报告并停止，不得自动进入 Phase X+1」——本报告即停止点，等待用户指令。
