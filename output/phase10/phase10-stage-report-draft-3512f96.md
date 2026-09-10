# Phase 10 阶段报告草稿

状态：`COMPLETED / PASS / PENDING PROTECTED FILE SYNC`

实现 HEAD：`3512f9635d2dd04febce610f9ce9a2c00077f012`

固定 Frappe SHA：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`

固定 ERPNext SHA：`11e0ba0a1c45f217e2e73e885f699102d06da325`

最终独立对抗审查：`PASS`（第二轮只读审查；第一轮的出口材料一致性问题已修复）。

## 交付结果

Phase 10 完成了可以在固定开发 ERP 复跑的受治理采购闭环：一个持久 Run 可以从已确认的 PO Draft 提交 PO，再按来源行部分或完整收货、部分或完整开票、部分或完整付款，并依据 ERP 当前事实完成回读、对账和收口。每个有副作用的动作都有 typed payload、独立审批、payload digest、reservation、幂等键和 Receipt；Runtime、模型、检索和实验事件不能直接持有 ERP 写工具。

用户在 Runs 页面可以看到 PO、Purchase Receipt、Purchase Invoice、Payment Entry 的来源与目标、计划步骤、前置依赖、reservation、Receipt、剩余处理和阻塞原因。成功 Run 显示 `链路已完成`；调用方丢失响应或 Receipt 未落地时显示 `需要对账`，停止下游调度并提供恢复/人工接管入口。

本阶段没有连接银行、执行真实资金转账、修改固定上游、重置开发站点或声称生产部署、客户采用和收益。真实验收单据保留在隔离 `dev.localhost`，用于复核业务事实。

## 主要实现与提交

| 步骤 | Conventional Commit | 结果 |
|---|---|---|
| P10.1 | `cd148ab` `feat(p2p): add independently approved purchase order submission` | `SUBMIT_PO`/`CANCEL_PO`、独立审批、状态重检、幂等和 PO 回读 |
| P10.2 | `141106b` `feat(p2p): add governed partial purchase receipts` | 来源行级部分收货、库存/剩余量回读和恢复 |
| P10.3 | `324c8d6` `feat(p2p): add governed partial purchase invoices` | 来源收货部分开票、税/科目由 ERP 计算、应付余额回读 |
| P10.4 | `ef260a8` `feat(p2p): add governed payment entry settlement` | Payment Entry 草稿/提交、分配校验、部分付款与 outstanding 对账 |
| P10.5 | `f411c4e` `feat(p2p): add governed cancellation and recovery` | PO/PR/PI/Payment Entry 独立取消、依赖阻塞和人工恢复 |
| P10.6 | `717ef65` `feat(p2p): orchestrate durable procure-to-pay runs` | 持久 PlanStep、跨单据依赖、重启恢复、Run 收口和运营界面 |
| P10.7 | `cebdff9` `test(p2p): compare orchestration and recovery strategies` | LAB_ONLY 单 Agent/固定 Workflow/multi-agent 对照与 Adoption Card |
| P10.8 | `15e1656` `test(p2p): verify real ERP lifecycle and fault recovery` | 真实 ERP 全链路、响应丢失、已提交但 Receipt 缺失故障验收 |
| P10.9 gate fix | `3512f96` `test(phase10): cover governed cancellation transitions` | 取消合法边界测试和阶段出口格式/单元门禁修复 |

阶段收口报告、final manifest、Harness/README 同步提案和本开发日志将在收口提交中一起保存；用户现有 `docs/PLAN.md` 修改不属于本阶段提交。

## 数据流与关键文件

数据流为：用户目标 → Runtime 调查/计划 → Frappe 生成并验证候选 Action → 独立有权用户审批 → 执行前身份/权限/Workflow/当前状态重检 → ERP 原生 controller → 权限内回读 → Receipt → 下一 PlanStep。事件或 checkpoint 只能唤醒调查，不能携带可直接执行的授权。

最重要的三个入口是：

1. [`synora_agentic_erp/governance/contracts.py`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/synora_agentic_erp/governance/contracts.py)：typed action、版本化参数和审批摘要。
2. [`synora_agentic_erp/governance/p2p_execution.py`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/synora_agentic_erp/governance/p2p_execution.py)：PO/PR/PI/Payment Entry 受治理写入、reservation、回读、失败和不确定结果冻结。
3. [`synora_agentic_erp/governance/p2p_orchestration.py`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/synora_agentic_erp/governance/p2p_orchestration.py)：PlanStep 依赖投影、Run resume/finalize/cancel、重新调查和收口条件。

用户直接感知的页面入口是 [`synora_agentic_erp/synora_agentic_erp/page/runs/runs.js`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/synora_agentic_erp/synora_agentic_erp/page/runs/runs.js)。

## P10.8 真实 ERP 验收

固定隔离站点使用不同的发起人和审批人身份。真实主路径结果：

- 1 个 PO Submit、4 个 Purchase Receipt 动作、4 个 Purchase Invoice 动作、6 个 Payment Entry 动作，共 15 个成功 Action；每个均有独立审批、reservation 和 `SUCCEEDED / ERP_SUCCESS` Receipt。
- PO `PUR-ORD-2026-02030` 的 `per_received=100.0`、`per_billed=100.0`。
- 收货单 `MAT-PRE-2026-00358`、`MAT-PRE-2026-00359`；两张发票 `ACC-PINV-2026-00221`、`ACC-PINV-2026-00222` 均为 `Paid`。
- 三笔付款 `ACC-PAY-2026-00076`、`ACC-PAY-2026-00077`、`ACC-PAY-2026-00078`，Payment Entry GL 借贷记录存在；第一张发票先付 5 再付 5，第二张付 10。
- Run `a1d259b2-fc1…` 只有在 15 份成功 Receipt 均存在时收口为 `SUCCEEDED`。

故障验收：

- ERP 已提交但调用方响应丢失：同一幂等键回读原 Receipt，不创建第二张 PO，结果为 `UNCERTAIN_RESULT` 后受控恢复。
- 原生 submit 已提交但 Receipt 未落地：Run 持久冻结为 `RECONCILIATION_REQUIRED`，Receipt 标为 `RECONCILIATION_REQUIRED / UNCERTAIN_RESULT`；resume 只读调查，不执行后续依赖。

机器记录见 [`phase10-p2p-e2e-recorded-v1.json`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/phase10/phase10-p2p-e2e-recorded-v1.json) 和 [`phase10-p2p-e2e-recorded-v1.md`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/phase10/phase10-p2p-e2e-recorded-v1.md)。

## 浏览器验收

使用真实登录态、只读 Administrator/System Manager 会话打开 `http://127.0.0.1:8000/desk/runs`：

- 成功 Run 页面显示 15 行 `已完成`/`SUCCEEDED`、来源/目标单据、前置关系和 Receipt。
- 未知结果 Run 页面显示 `状态: 需要对账`、`RECONCILIATION_REQUIRED / UNCERTAIN_RESULT` 和人工恢复文案。
- 控制台为 `0 errors / 3 warnings`；3 条 warning 是上游 icon preload 提示。
- 脱敏截图：[`phase10-p2p-e2e-a1d259b2.png`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/playwright/phase10-p2p-e2e-a1d259b2.png)（SHA-256 `9c92f55324ba2ea01f7fcf9b80797c27101bdd9a1814c338dfcdde2ceb02e0c8`）；[`phase10-p2p-unknown-9ce932fa.png`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/playwright/phase10-p2p-unknown-9ce932fa.png)（SHA-256 `4314b9a56a18ea0ab240c1d01433fd2cd741fcae6549ed8ccd40b28d4b2fd73c`）。

浏览器 sidecar：[`phase10-p2p-e2e-browser-acceptance.json`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/playwright/phase10-p2p-e2e-browser-acceptance.json)。未记录密码、Cookie、local storage、Token 或 Provider raw response。

## P10.7 实验对照

实验严格标记 `LAB_ONLY`，没有 Frappe、ERP 凭证或生产写入。固定同一数据集和事件矩阵，事件中的伪造 `authorization_hint` 只作为不可信唤醒数据。数据集 digest 为 `b9a844f21aa3ca498306dfd96ca9c1098b86fe9757db078eb0519f6827b4540a`。

| 策略 | 结果 | 安全违规 | 延迟 | tokens | 恢复率 | 运维复杂度 |
|---|---|---:|---:|---:|---:|---:|
| single_agent | FAILED 基线 | 4 | 100 ms | 320 | 0 | 1 |
| fixed_workflow | SUCCEEDED | 0 | 71 ms | 0 | 1.0 | 2 |
| multi_agent | SUCCEEDED | 0 | 141 ms | 640 | 1.0 | 4 |

同数据质量和安全/恢复结果下，multi-agent 没有净收益，因此 Adoption Card 保留 `KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE`，不把实验模式接入业务主线。证据见 [`phase10-p2p-orchestration-recorded-v1.json`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/phase10/phase10-p2p-orchestration-recorded-v1.json)。

## 阶段出口命令与结果

以下命令在实现 HEAD `3512f96` 执行，未把未运行命令写成通过：

| 命令 | 退出码 | 实际结果 |
|---|---:|---|
| `UV_CACHE_DIR=/private/tmp/synora-phase10-uv-cache make format-check` | 0 | Ruff 385 files formatted |
| `UV_CACHE_DIR=/private/tmp/synora-phase10-uv-cache make lint` | 0 | 所有检查通过 |
| `UV_CACHE_DIR=/private/tmp/synora-phase10-uv-cache make type` | 0 | mypy 120 source files，无错误 |
| `UV_CACHE_DIR=/private/tmp/synora-phase10-uv-cache make unit` | 0 | 853 passed，55 个 FastAPI/A2A 上游弃用 warning |
| `UV_CACHE_DIR=/private/tmp/synora-phase10-uv-cache make integration` | 0 | Frappe app-test 239 tests，`OK` |
| `UV_CACHE_DIR=/private/tmp/synora-phase10-uv-cache uv run --python 3.14 pytest tests/test_phase10_orchestration_lab.py -q` | 0 | 3 passed |
| `UV_CACHE_DIR=/private/tmp/synora-phase10-uv-cache uv run --python 3.14 python services/agent_runtime/scripts/phase10_orchestration.py` | 0 | `PASS / KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE` |
| `python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .` | 0 | valid，manifest valid，references broken 0 / checked 673 |
| `python3 .agents/skills/harness-check/scripts/validate_manifest.py .` | 0 | valid |
| `python3 .agents/skills/harness-check/scripts/check_references.py .` | 0 | broken 0 / checked 673 |
| `python3 .agents/skills/harness-check/scripts/score_harness_health.py .` | 0 | 79/100，grade C；只读健康评分 |
| `python3 .agents/skills/harness-check/scripts/detect_drift.py .` | 1（预期） | 报告 4 个待受保护文件同步的 drift：`docs/PLAN.md`、`agent/state_machine.py`、`api.py`、`runs.js` |
| ponytail-audit | 0 | `Lean already. Ship.` |
| ponytail-debt | 0 | 1 个既有 marker，0 个 no-trigger |

完整 P10.8 真实模块和 P10.1–P10.6 定向测试均通过；具体计数与机器标记在开发日志和 manifest 中保留。

## Rubric

| 维度 | 分数 | 依据 |
|---|---:|---|
| D1 需求与业务正确性 | 4 | 真实 PO→PR→PI→Payment Entry 主路径、部分处理、取消/恢复和 Run 证据 |
| D2 身份、权限与范围 | 4 | 发起人与审批人分离、对象权限/公司范围/Workflow 执行前重检 |
| D3 状态、并发、幂等与恢复 | 4 | 独立 reservation/Receipt、重启、响应丢失和未知结果冻结 |
| D4 Agent 信任与成本 | 3 | Runtime/事件无写工具，LAB_ONLY 对照记录 token/复杂度；未声称生产成本 |
| D5 安全与数据保护 | 4 | 自批/越权/跨公司/撤权/摘要篡改拒绝，错误和 artifact 脱敏 |
| D6 UI、可访问性与双语 | 3 | Run 页面双语、aria-live/键盘按钮、成功/加载/空/失败/权限文案；未声称完整无障碍审计 |
| D7 测试、真实集成与复现 | 4 | 853 unit、239 Frappe、P10 focused、真实 ERP、故障、浏览器和可哈希 artifact |
| D8 治理、追踪与非虚构 | 4 | Action→Approval→Reservation→Receipt→PlanStep 追踪，固定上游，失败证据保留 |
| D9 简洁性与可运维性 | 3 | 复用既有 contracts/policy/Run/Receipt，PlanStep 为单一投影，实验隔离 |

合计 `33/36`，平均 `3.67`。D1/D2/D3/D5/D7/D8 均为 `≥3`；当前已知 P0/P1 为 `0`。

## 风险登记

| 风险 | 初始/当前级别 | 发现方法 | 规避与下一门禁 |
|---|---|---|---|
| 自批、冒用审批人、审批详情越权 | 3×4=P1（已验证关闭） | 双身份、撤权、跨公司和审批详情负面测试 | 服务端身份/对象权限/来源快照重检；后续权限回归继续覆盖 |
| 审批后状态或策略漂移 | 3×4=P1（已验证关闭） | 修改来源、撤权、Workflow 漂移后执行 | 绑定 payload digest/快照/策略版本；变化后重新审批 |
| 并发超收、超开票、重复付款 | 3×4=P1（已验证关闭） | 并发竞争、相同 key 重放、余额回读 | reservation + ERP 原生校验 + Receipt 对账 |
| ERP 成功但调用方误判失败 | 3×4=P1（已验证关闭） | 响应丢失、提交后进程退出 | `UNCERTAIN_RESULT` 冻结 Run，先回读对账，禁止盲重试 |
| 取消扩大下游影响 | 2×4=P2 | 下游依赖、冻结期、重复取消 | 独立取消审批，不级联；无法合法取消时人工接管 |
| Harness/README 管理指纹待同步 | 2×4=P2（当前） | `detect_drift.py` 退出 1 | 使用 [`phase10-harness-sync-proposal.md`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/phase10/phase10-harness-sync-proposal.md)，经维护者确认后同步并复跑 drift |
| 开发站点保留真实验收单据导致历史增长 | 2×3=P2 | 验收批次和唯一前缀核对 | 保留证据；后续按归属走受治理清理，不 reset 或删除历史 |
| 浏览器 icon preload warning、完整无障碍审计未覆盖 | 2×2=P3 | 控制台与手工页面检查 | warning 不影响本轮业务证据；后续 UI 门禁补完整 a11y 审计 |
| LAB_ONLY 指标被误读为生产性能 | 2×3=P2 | artifact policy 和 Adoption Card 审查 | 明确 test double、固定 digest、无 ERP capability；生产性能另行测量 |

没有未关闭 P0/P1。P2 均有 owner、下一门禁和复验条件；Harness/README 同步等待受保护文件流程，不改变已验证业务代码。

## 手工验收

1. 启动固定开发服务并登录 `http://127.0.0.1:8000/desk/runs`。
2. 打开成功 Run 前缀 `a1d259b2`，确认 15 个步骤均为 `SUCCEEDED`，PO 收货/开票率为 100%，两张发票为 `Paid`，页面能展开来源、依赖、reservation 和 Receipt。
3. 打开未知结果 Run 前缀 `9ce932fa`，确认状态为 `RECONCILIATION_REQUIRED`，显示 `UNCERTAIN_RESULT` 和人工恢复入口，页面没有继续执行按钮。
4. 运行 P10.7 实验脚本，确认输出 `PASS / KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE`，并检查 artifact 中事件授权提示没有进入授权决策。
5. 在隔离站点重跑 `bench --site dev.localhost run-tests --module synora_agentic_erp.tests.test_phase10_e2e`，确认 3 tests 和机器 marker；不要把开发站点结果外推为生产或银行付款证据。

## 受保护文件同步

Harness 结构、manifest 和引用检查均有效，但 `detect_drift.py` 的退出码为 `1`，因为 `.harness` 仍是 Phase 9 指纹，且 `docs/PLAN.md` 有用户现有修改。已生成文件级提案 [`phase10-harness-sync-proposal.md`](/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/output/phase10/phase10-harness-sync-proposal.md)，其中列出目标 SHA、最小 source locator、README 公开事实草稿、`.harness/unresolved.json` 的 `NO_CHANGE` 和应用后复验命令。

本轮没有直接写 `.harness/`、README 或覆盖 `docs/PLAN.md`。这是仓库约定的用户维护/managed ownership 边界，不是业务验证失败。提案经用户/Harness 维护者确认后，目标是将 drift 清零；在此之前阶段状态保留 `PENDING PROTECTED FILE SYNC`，不把它写成已同步。

## 独立对抗审查

独立只读对抗审查输入包括最终实现 HEAD、P10.1–P10.8 diff、真实 ERP/浏览器/实验 artifact、全量门禁输出、Harness drift 提案、Rubric 和风险登记。审查允许返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`；本轮第一轮返回 `CHANGES_REQUIRED`，指出出口材料哈希与 pending 语义不一致；修复只更新 manifest、提案和日志的证据绑定，没有改业务代码或测试。第二轮复核返回 `PASS`，确认最终材料、保护边界、风险和证据均一致。

## 阶段边界与未完成项

- 未做生产部署、发布、Tag、推送或银行转账；Payment Entry 仅是开发 ERP 的受治理记账。
- 未修改 Frappe/ERPNext 上游、`.env*`、用户现有 `docs/PLAN.md` 或 `.harness/` 管理文件。
- 真实浏览器验收只覆盖 Administrator/System Manager 的只读观察；后端已覆盖权限边界，不能把截图外推为完整角色 UI 审计。
- P10.7 是可复跑的 LAB_ONLY 对照，不是模型质量、ERP 延迟或成本承诺。
- 本阶段结束后停止，不进入 Phase 11；答疑和学习笔记等待用户另行触发。
