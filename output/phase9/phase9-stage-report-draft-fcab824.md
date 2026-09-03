# Phase 9 阶段报告草稿

状态：`PENDING INDEPENDENT REVIEW`

代码 HEAD：`d31ea1f3a15a51915f0969f8f05a9494ee598c58`
Frappe SHA：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`
ERPNext SHA：`11e0ba0a1c45f217e2e73e885f699102d06da325`

## 本阶段完成结果

Phase 9 的 P9.5–P9.10 执行材料已在当前工作树形成。业务主线采用 `assist/glm-5.3-flash` 的 Planner→Reviewer；`qwen3:8b` 和 `grok-4.5` 的失败证据保留，GLM 的重复运行失败证据也保留。前三个候选未同时达标时不调用 `qwen3.8:27b`，而是保留失败并停止模型搜索，避免用更慢模型掩盖预算、采样和远程波动问题。P9.5 采用证据来自同模型真实双臂 GLM v3，代码逻辑在后续采样试验后恢复到该采用配置；v4–v6 是新 HEAD 上的真实重跑，因配对质量相对门槛失败而保留为 BLOCKED。

P9.6 MCP、P9.7 真实 localhost A2A、P9.8 固定 ANP descriptor 均有正式协议验收 artifact，状态为 `PASS`。A2A 适合本阶段有界 Task 生命周期；ANP 明确保留为 `LAB_ONLY / NOT ADOPTED`，因为本阶段没有开放网络发现需求。

P9.9 使用真实 Buyer→Frappe→Runtime→GLM 链路完成一次成功验收：Planner 与 Reviewer 各调用一次，发生一次 handoff，无 revision，Reviewer 返回 `ACCEPTED`。Viewer 被拒绝，System Manager 只能看到脱敏摘要；受控 test double 只用于故障路径。业务 ERP 锚点前后 digest 一致，ERP 业务写计数为 `0`。

## P9.5 采用证据

权威采用 artifact：`output/phase9/phase9-ab-real-glm-v3.json` 和对应 decision package。该 artifact 绑定真实 `assist/glm-5.3-flash`、固定 12 案、`relative-model-v1`、completion cap `512`、同一输入投影和双臂固定顺序；code HEAD 为 `b1ef96064da19234f84356c14594707165fa86fb`。后续提交仅增加回归护栏、开发日志和采样试验记录，未改变该编排业务逻辑。

| arm | task | valid | recovery | p95 | total tokens | security |
|---|---:|---:|---:|---:|---:|---|
| `single_agent` | 7/12 | 7/12 | 7/12 | 7371 ms | 5755 | 0/0/0/0 |
| `planner_reviewer` | 8/12 | 10/12 | 8/12 | 3690 ms | 5885 | 0/0/0/0 |

Planner 与 Reviewer Adoption Card 均为 `ADOPT`，`net_benefit=true`、`thresholds_met=true`、`security=true`。Coach 为 Phase 8 独立入口 `RETAIN`；Reconciliation 为异常路径 `REJECT`。GLM v4–v6 真实重跑均为 `BLOCKED`，原因是同一模型相对门槛中的配对 task/recovery 未保持不低于 single；没有覆盖或修改历史 artifact。

## P9.6–P9.8 协议证据

- `output/phase9/phase9-mcp-acceptance-d31ea1f.json`：official in-memory/stdio client，唯一工具 discovery/call，未知工具、未知字段、NaN/Infinity、超长输入、注入文本、取消和异常退出均拒绝；resources/templates/prompts 为 0，stdout 仅协议消息，敏感环境已清理，ERP 写入为 0。
- `output/phase9/phase9-a2a-acceptance-d31ea1f.json`：官方 SDK 经真实 TCP `127.0.0.1` 完成正常完成、未知 task、context mismatch、malformed/oversized payload、重复/终态取消、cancel/completed 竞争、handler exception、timeout、timeout 后服务端完成和非法状态转换；每项结果均为 `true`，取消后无 completed，进程退出码为 -15，ERP 写入为 `0`。
- `output/phase9/phase9-anp-acceptance-d31ea1f.json`：四个固定 descriptor，权限最小且唯一候选才路由；无候选、多候选冲突、恶意 descriptor、未知版本、权限扩大、开放网络端点、未知字段和循环路由均 fail closed；每项结果均为 `true`，`adoption=LAB_ONLY`。
- 合并摘要：`output/phase9/phase9-protocol-acceptance-d31ea1f.json`。

## P9.9 真实业务与恢复证据

`output/phase9/phase9-real-acceptance-fcab824.json` 为 `PASS`，`output/phase9/phase9-final-manifest-d31ea1f.json` 绑定当前 HEAD、上游 SHA、命令退出码和所列 artifact SHA-256；验收包含：

- server-derived `OrchestrationScope`，客户端未提供 identity fields，UUID5 绑定 task/run/correlation；
- Buyer 正常链路使用真实 GLM `planner_reviewer`，Run `efa39d1c-e9ee-405b-8a61-d7996d128200`，状态 `SUCCEEDED`，停止原因 `ACCEPTED`；
- Viewer `RUN_REJECTED`；System Manager safe keys 仅为 `company/evidence/findings/generated_at/goal/horizon_days/summary/warehouse`，无 Prompt、provider credential 或 hidden reasoning；
- revision accept、scope mismatch、deterministic anomaly、invalid output、timeout、cancellation 的受控恢复；取消为终态且无后续 completed；
- MCP/A2A/ANP 与业务 Runtime 隔离；MR、MR Item、PO、PO Item、Bin、Stock Entry、Purchase Receipt、Purchase Invoice 锚点 before/after digest 相同，ERP business writes `0`。

浏览器证据：

- `output/playwright/phase9-buyer-fcab824.png`（Buyer 运行详情，显示 GLM、planner_reviewer、2 calls、handoff 1、ACCEPTED）；
- `output/playwright/phase9-viewer-fcab824.png`（Viewer 权限拒绝）；
- `output/playwright/phase9-system-manager-fcab824.png`（System Manager 运行详情与脱敏展示）。


## 最终 artifact 与截图 SHA-256 清单

manifest：`output/phase9/phase9-final-manifest-d31ea1f.json`；SHA-256：`154631a58877650e6923beea711a57d75c985dd7844de4bfc9623ffaccbcf50f`。

- `output/phase9/phase9-ab-real-glm-v3.json`：`0ea8729d3ca9bbf1d9cfef4c9d323f7bd573fd24ca7fa2487ef1c55a71ff6e90`
- `output/phase9/phase9-ab-real-glm-v3.md`：`821aaf6ca4d92d32791cb7069dd18091afe9cc2d2783762c3d9c2726bae3fb63`
- `output/phase9/phase9-ab-real-glm-v4.json`：`cd5a283c61d321b3d96021f867d9dd8ecc436aa2ccf376f7422cbdec03319592`
- `output/phase9/phase9-ab-real-glm-v4.md`：`9e763d5ff414234d613ccc82c92de0114f648398000ce82f034ca635b0a06a85`
- `output/phase9/phase9-ab-real-glm-v5.json`：`68e5d03e5d969ec8b61f915c1266f9409c6e85d51a45f768bccf01b09bdca867`
- `output/phase9/phase9-ab-real-glm-v5.md`：`327bc65881375112ce136d339d8180ca07683ec96bb606a5fd720df6436babe4`
- `output/phase9/phase9-ab-real-glm-v6.json`：`0d252e93494d2b4c228d09d987e84328d722db209e97ebf77a5a17feb7bcd8ca`
- `output/phase9/phase9-ab-real-glm-v6.md`：`be95ee92cfcb5420e15111b18978413afec20043cb36ade43ef91968cc8105b3`
- `output/phase9/phase9-ab-real-grok-v1.json`：`7653e4d944dd71ac375e37396436abcbc6def55ec6938654c0afc57f2f8743b5`
- `output/phase9/phase9-ab-real-grok-v1.md`：`773e35738d302f932ce71aa60a234648a00541a721c8f043a4e338bee68a7109`
- `output/phase9/phase9-ab-real-v10.json`：`bce371b3718cc8b7a317f79c539de1b8c90402ecb72cdae25534d84677ae1712`
- `output/phase9/phase9-ab-real-v10.md`：`4181b3945c8b2b6c942617ca8eb5535b7024d98387f7304f33607801b78b5f5f`
- `output/phase9/phase9-single-agent-baseline-fcab824.json`：`254d384bc24cd28f718c41c4cb498445c5f8769960960e211e6741abb2e2f5cb`
- `output/phase9/phase9-single-agent-baseline-fcab824.md`：`3f6be443804ce4147f396b9a93c018d70317b93115db53af3c76372518a07f97`
- `output/phase9/phase9-ab-recorded-fcab824.json`：`4a36f4f1c29e12f1e2d35345259ce64a658f34dfae1a5ec9addcd0c205bdee9d`
- `output/phase9/phase9-ab-recorded-fcab824.md`：`04113b4c5afc9f2eafac178bfbf975b1ab45887aef813aa5f5bb21fde23f8cb8`
- `output/phase9/phase9-pattern-comparison-fcab824.json`：`a13c85cf93889e47799212421150035afa049eab03d8eab494ce82d731b78e19`
- `output/phase9/phase9-pattern-comparison-fcab824.md`：`96945b2a8bbcebde77345f1306a9c2228354c1a988c18178e9ad97d9be6e958b`
- `output/phase9/phase9-protocol-acceptance-d31ea1f.json`：`85f8fd1d2faae3db8046a7780dbd51311bb69b19a53dfdb772f4b1198a076c1b`
- `output/phase9/phase9-mcp-acceptance-d31ea1f.json`：`b8c3ed70ee2e62213718c51148763ab363f970068856c12e1c254d7c1d455361`
- `output/phase9/phase9-a2a-acceptance-d31ea1f.json`：`2e7b4b08c71a3794ea7f4d86a8e2ad4900a7a1c9de10083252409f1661a2f333`
- `output/phase9/phase9-anp-acceptance-d31ea1f.json`：`b4e2dd2ff3dbe7e35859944b3d2848b73b83687b1bc30977fee252fb6780cf70`
- `output/phase9/phase9-real-acceptance-fcab824.json`：`f38b9e2b54ebde0cea073d6757f3dd6e78391277a4eaca4cdfad5de95a102548`
- `output/playwright/phase9-buyer-fcab824.png`：`3ca447e4cd511a267c54cf71e762a15c9d9d4e1bd3d55af40bd145fe2cc891ad`
- `output/playwright/phase9-viewer-fcab824.png`：`12d72d329e4b5282f8abd11d89ac9f8a1a2b7e60cbf5d6bc08fda95b916387cd`
- `output/playwright/phase9-system-manager-fcab824.png`：`b71cd6d2cddf87cd08c45ab63efff32af517902dae15d85b9f30c8331ae7af17`

## L3 出口检查

以下命令已在 Phase 9 日志第 41 轮记录，均退出 `0`，除特别说明外无未处理失败：

- `make format-check`（348 files formatted）；
- `make lint`；`make type`（116 files）；`make unit`（840 passed，43 warnings）；
- `UV_CACHE_DIR=/private/tmp/synora-phase9-uv-cache make integration`（Frappe app-test 210 tests，OK）；
- Phase 9 baseline/A-B/pattern/MCP/A2A/ANP focused suite（41 passed，42 warnings）；
- `uv lock --check --python 3.14`、Python 3.14 `compileall`、protocol import smoke；
- `git diff --check`；Frappe/ERPNext fixed SHA 与 dirty count 校验；
- Harness manifest、structure、reference 只读检查；drift 检查报告待最终文档/Harness 同步的预期 drift；
- `ponytail-review`：`Lean already. Ship.`；`ponytail:` debt 为一条既有固定上限标记，无升级触发器。

一次误用不存在的 Harness structure 路径退出 `2`，随后改用 `.agents/skills/harness-build/scripts/validate_harness_structure.py` 并通过；该失败已记录，未伪装为通过。

## Rubric（独立审查前草稿）

| 维度 | 分数 | 依据 |
|---|---:|---|
| D1 需求与业务正确性 | 4 | 真实 Buyer 链路、确定性计划、GLM 审核与 0 ERP business writes |
| D2 身份、权限与范围 | 4 | server-derived scope、Buyer/Viewer/System Manager 三角色证据 |
| D3 状态、并发、幂等与恢复 | 4 | A2A 终态/cancel、P9.9 revision/timeout/cancel/fallback |
| D4 Agent 信任与成本 | 3 | 同模型 A/B、cap/role/profile/trace；远程 GLM 重跑存在可复现波动 |
| D5 安全与数据保护 | 4 | MCP/A2A/ANP 注入、secret、scope、ERP 写入和隔离证据 |
| D6 UI、可访问性与双语 | 3 | Buyer/Viewer/System Manager 页面截图和双语摘要；未声称完整无障碍审计 |
| D7 测试、真实集成与复现 | 4 | 840 unit、210 Frappe、41 focused、真实 HTTP/loopback/GLM artifacts |
| D8 治理、追踪与非虚构 | 4 | immutable artifact、失败保留、日志、固定上游 SHA、无 27B 选择性补救 |
| D9 简洁性与可运维性 | 3 | 复用现有 contracts/DocType，运行器和证据摘要有界 |

合计 `33/36`，平均 `3.67`；门槛维度 D1/D2/D3/D5/D7/D8 均 ≥3；无已知 P0/P1。

## 风险登记

| 风险 | likelihood | impact | 等级 | owner | 下一门禁与复验 |
|---|---:|---:|---|---|---|
| 远程 GLM 同模型重跑存在配对质量波动 | 3 | 3 | P2 | Runtime/Eval | 后续 provider acceptance；固定 case/cap/profile 重跑并保留每次 artifact，不改门槛 |
| MCP/A2A/ANP 仍为协议实验，ANP 未采用 | 2 | 2 | P3 | Protocol Lab | 仅在有开放网络发现需求时进入下一门禁；重跑 `phase9_protocol_acceptance.py` |
| 完整无障碍审计未覆盖全部页面 | 2 | 2 | P3 | UI | 下一 UI 里程碑；运行页面键盘/aria/空失败状态审计 |

P0/P1 风险均为 `0`。P2 有 owner、下一门禁和复验命令；P3 进入 backlog。

## Assignment、问答与未运行项

按用户批准的 Phase 9 执行计划，本阶段不创建 Assignment、不要求用户写代码、不写学习笔记。阶段问答保留为待练习：

1. 为什么同模型 A/B 必须比较相同输入投影和固定顺序？（待练习）
2. 为什么 Viewer 的权限拒绝必须在 Provider 调用前完成？（待练习）
3. 为什么取消终态不能再发布 completed？（待练习）
4. 为什么 ANP 保留 `LAB_ONLY` 而 A2A 可以用于本阶段有界任务？（待练习）
5. 为什么真实 GLM 成功链路不能由 controlled test double 替代？（待练习）

最终文档/Harness/README 同步、独立审查以及审查后的最终证据提交尚未运行；本草稿没有宣称阶段最终 PASS，也没有宣称生产部署、客户采用或未验证收益。Phase 10 尚未开始。

## 独立对抗审查

第一轮审查已返回 `CHANGES_REQUIRED`，修复项已在第 44 轮日志记录并换到当前 HEAD；第二轮审查已返回 `CHANGES_REQUIRED`，三项 A2A 修复已在 `d31ea1f` 完成并重新生成协议 artifact；第三轮为最后一次独立对抗审查，将重新接收原始计划、权威文档、最终 diff、P9.5 原始 A/B、P9.6–P9.9 artifacts、浏览器截图、L3 输出和本报告草稿，结论只能为 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。
