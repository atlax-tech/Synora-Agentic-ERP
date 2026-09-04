# Phase 9 阶段报告草稿

状态：`COMPLETED / PASS / READY FOR THE NEXT PHASE`（独立对抗审查已 `PASS`）

实现 HEAD：`8b7ff1b1dc51449b51f0335ed63ae2c34bc5772e`
证据提交：`9f2f6ca6217ecacbc8acc87aaa7686869c70dc02`（仅新增证据/日志，不改变实现逻辑）
Frappe SHA：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`
ERPNext SHA：`11e0ba0a1c45f217e2e73e885f699102d06da325`

## 本阶段完成结果

P9.5 已按用户在 2026-09-04 的质量优先决定收口：GLM `assist/glm-5.3-flash` 的第一份满足质量优先规则的真实同模型 A/B 是 `phase9-ab-real-glm-v12.json`，Planner 与 Reviewer 均为 `ADOPT`。质量规则只要求 multi 不劣于同模型 single、至少一项质量严格提升、p95 受控和安全全通过；token 仍记录在 artifact 中，不再作为采用否决条件。GLM v13 是随后仅绑定 Adoption Card 文案提交的随机重跑，结果 `BLOCKED`，作为失败 artifact 保留，不覆盖 v12。前三候选历史证据已保留，`qwen3.8:27b` 从未调用。

v12 指标如下：

| arm | task | valid | recovery | p95 | total tokens | security |
|---|---:|---:|---:|---:|---:|---|
| single_agent | 7/12 | 8/12 | 7/12 | 16388 ms | 5700 | 0/0/0/0 |
| planner_reviewer | 8/12 | 9/12 | 8/12 | 9598 ms | 11051 | 0/0/0/0 |

当前实现与 v12 评测 HEAD 的源代码差异只有 Adoption Card reason 文案；没有改变运行器、阈值计算、Provider、合同或安全边界。Coach 保持 Phase 8 独立入口 `RETAIN`，Reconciliation 保持异常触发的 `REJECT`。

P9.6 MCP、P9.7 真实 localhost A2A、P9.8 固定 ANP descriptor 均在实现 HEAD `8b7ff1b` 正式通过。ANP 结论仍为 `LAB_ONLY / NOT ADOPTED`；A2A 仅用于本阶段有界 Task 生命周期；协议与业务 Runtime 不共享 ERP capability 或凭证。

P9.9 在真实 Frappe→Runtime→GLM 环境通过：Buyer 使用真实 GLM Planner→Reviewer 得到 `ACCEPTED`，Viewer 得到 `RUN_REJECTED`，System Manager 仅能看到脱敏摘要；revision、scope mismatch、deterministic fallback、invalid output、timeout、cancellation 受控场景全部 fail closed；ERP MR/PO/Bin 等锚点前后 digest 相同，业务写计数为 `0`。

## 证据

权威 manifest：`output/phase9/phase9-final-manifest-8b7ff1b.json`。

- P9.5：v12 采用证据、v13 后续失败证据、qwen primary 与 grok backup 历史失败证据；均保留原文件，不覆盖。
- P9.6–P9.8：`phase9-protocol-acceptance-8b7ff1b.json` 及 MCP/A2A/ANP 子 artifact，均为 `PASS`、绑定实现 HEAD。
- P9.9：`phase9-real-acceptance-8b7ff1b.json`，含 server-derived scope、UUID5 身份绑定、真实 provider、角色权限、故障恢复和零 ERP 写入。
- 浏览器：`phase9-buyer-8b7ff1b.png`、`phase9-viewer-8b7ff1b.png`、`phase9-system-manager-8b7ff1b.png`。

## P9.10 L3 命令与退出码

以下命令按计划顺序执行，退出码均为 `0`，除 Harness drift 明确为待文档同步的预期状态：

1. `UV_CACHE_DIR=/private/tmp/synora-phase9-uv-cache make format-check` — 0（372 files）。
2. `UV_CACHE_DIR=/private/tmp/synora-phase9-uv-cache make lint` — 0。
3. `UV_CACHE_DIR=/private/tmp/synora-phase9-uv-cache make type` — 0（116 files）。
4. `UV_CACHE_DIR=/private/tmp/synora-phase9-uv-cache make unit` — 0（843 passed，55 warnings）。
5. `UV_CACHE_DIR=/private/tmp/synora-phase9-uv-cache make integration` — 0（Frappe app-test 210 tests，OK）。
6. Phase 9 focused suite — 0（44 passed，54 warnings）。
7. artifact schema/HEAD/zero-write checker — 0。
8. `UV_CACHE_DIR=/private/tmp/synora-phase9-uv-cache uv lock --check --python 3.14` — 0。
9. Python 3.14 `compileall` + protocol import smoke — 0。
10. `git diff --check` — 0。
11. fixed Frappe/ERPNext SHA and dirty count — 0（dirty0/dirty0）。
12. ponytail-audit — 0；实现源自已审计 HEAD，当前提交无源代码/依赖变化，结论 `Lean already. Ship.`。
13. ponytail-debt — 0（1 marker，0 no-trigger）。
14. Harness validate manifest — 0；structure — 0；references — 0（592 checked, broken 0）；health score — 0。`detect_drift.py` 退出 `1`，仅报告待阶段文档/Harness fingerprint 同步的既有 drift，未修改 `.harness/`。

## Rubric（独立审查后冻结）

| 维度 | 分数 | 依据 |
|---|---:|---|
| D1 需求与业务正确性 | 4 | 真实 Buyer→Frappe→Runtime→GLM 链路、确定性计划和 0 ERP business writes |
| D2 身份、权限与范围 | 4 | server-derived scope、UUID5、Buyer/Viewer/System Manager 证据 |
| D3 状态、并发、幂等与恢复 | 4 | A2A 终态/cancel race、P9.9 revision/timeout/cancel/fallback |
| D4 Agent 信任与成本 | 3 | 同模型质量优先 A/B、固定 cap/role/profile/trace；token 作为审计数据，远程结果有波动 |
| D5 安全与数据保护 | 4 | MCP/A2A/ANP 注入、secret、scope、隔离和零 ERP 写入 |
| D6 UI、可访问性与双语 | 3 | 三角色页面截图和中英文界面；未声称完整无障碍审计 |
| D7 测试、真实集成与复现 | 4 | 843 unit、210 Frappe、44 focused、真实 HTTP/loopback/GLM artifacts |
| D8 治理、追踪与非虚构 | 4 | immutable artifact、失败保留、命令退出码、固定上游 SHA、无 27B |
| D9 简洁性与可运维性 | 3 | 复用现有 contracts/DocType，协议和验收运行器有界 |

合计 `33/36`，平均 `3.67`；D1/D2/D3/D5/D7/D8 均 ≥3；当前已知 P0/P1 为 `0`。

## 风险登记

| 风险 | likelihood | impact | 等级 | owner | 下一门禁与复验 |
|---|---:|---:|---|---|---|
| 远程 GLM 同模型结果存在配对质量波动 | 3 | 3 | P2 | Runtime/Eval | 后续 provider acceptance；固定 case/cap/profile 重跑并保留 artifact，不降低质量门槛 |
| MCP/A2A/ANP 为有界实验，ANP 未采用 | 2 | 2 | P3 | Protocol Lab | 仅开放网络发现需求进入下一门禁；重跑 protocol acceptance |
| 完整无障碍审计未覆盖所有页面 | 2 | 2 | P3 | UI | 后续 UI 里程碑；运行键盘/aria/空失败态审计 |

P0/P1 风险为 `0`；P2 有 owner、下一门禁和复验命令。

## 独立对抗审查

- 唯一最终独立对抗审查在实现与证据冻结后执行，审查输入包含原始计划、权威文档、`1229bab..HEAD` diff、P9.5–P9.9 证据、三角色截图、L3 命令、Rubric 与风险表。
- 审查结果：`PASS`。审查确认质量优先 GLM v12 采用、协议和真实业务验收、权限/隔离、零 ERP 写入、L3 门禁和无 27B 事实均有绑定证据。

## 未运行与授权边界

- 权威文档、两份 README 与 `.harness/` 已按批准的 `P9-HARNESS-CLOSE-20260904-v2` 同步，最终 drift 复验通过。
- 本阶段不创建 Assignment、不写学习笔记、不调用 `codex-with-chatgpt`，不修改 `.env*`、Frappe/ERPNext 上游或数据库事务事实。
- Phase 10 尚未开始。

## 独立对抗审查输入

审查输入固定包括原始 Phase 9 计划、权威文档、`1229bab..HEAD` 最终 diff、P9.5 v12/v13/qwen/Grok artifacts、P9.6–P9.9 协议/权限/零写入证据、三角色截图、L3 命令输出、Rubric、风险表和本报告草稿。审查只能返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。
