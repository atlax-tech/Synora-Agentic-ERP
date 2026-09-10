# Phase 10 阶段报告草稿（R10.5）

状态：`COMPLETED / PASS / PENDING PROTECTED FILE SYNC`

实现代码 HEAD：`80abbbafba6ec4f443bab3f86f43356dee5bf9c0`

固定 Frappe SHA：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`

固定 ERPNext SHA：`11e0ba0a1c45f217e2e73e885f699102d06da325`

本报告新建，不覆盖历史报告 [`phase10-stage-report-draft-3512f96.md`](phase10-stage-report-draft-3512f96.md) 或历史 manifest。当前报告只把已实际执行的检查、真实 ERP 证据和仍待授权的受保护同步分开记录。

## 1. 阶段结果与边界

Phase 10 已在固定隔离 `dev.localhost` 完成受治理的 PO → Purchase Receipt → Purchase Invoice → Payment Entry 闭环。Run 从已确认的 PO 目标开始，每个有副作用的 Action 都经过 typed payload、独立审批、当前状态/权限重检、reservation、幂等键、ERP 原生 controller 和 Receipt；成功 Receipt 才能推进下一 PlanStep，目标进度和未付余额由 ERP 当前事实回读。

用户能在 Runs 页面看到目标版本、来源与目标单据、前置依赖、审批等待、reservation、Receipt、剩余处理和恢复原因。未知结果会停在 `RECONCILIATION_REQUIRED`，不盲目重试下游；成功页面显示真实 ERP/GL 回读，而不是模型或页面缓存推断。

本阶段没有生产部署、银行转账、客户采用或收益承诺；Payment Entry 仅是隔离开发 ERP 的受治理记账。没有修改 Frappe/ERPNext 上游、`.env*`、README、`.harness/` 或用户现有 `docs/PLAN.md`。

## 2. 当前实现与证据绑定

最重要的三个业务入口保持不变：

1. [`synora_agentic_erp/governance/contracts.py`](../../synora_agentic_erp/governance/contracts.py)：typed Action、参数版本和审批摘要。
2. [`synora_agentic_erp/governance/p2p_execution.py`](../../synora_agentic_erp/governance/p2p_execution.py)：PO/PR/PI/Payment Entry 的受治理写入、reservation、回读和不确定结果冻结。
3. [`synora_agentic_erp/governance/p2p_orchestration.py`](../../synora_agentic_erp/governance/p2p_orchestration.py)：PlanStep 依赖投影、Run resume/finalize/cancel、重新调查和收口条件。

用户直接看到的入口是 [`synora_agentic_erp/synora_agentic_erp/page/runs/runs.js`](../../synora_agentic_erp/synora_agentic_erp/page/runs/runs.js)。

R10.4 真实故障矩阵和浏览器 artifact 的机器绑定保留为：基线 `4381d47fb488751c2e4596285e27153c0e31acb9` 加工作树 diff `234dba35e371a842da1a80210edd097918c37adf28918ecf716b809d0b4e2a55`。随后该工作树被提交为 `52ad17f`。本轮 `80abbba` 相对 `52ad17f` 只增加：

- Runtime `_advance` 的 `WorkflowRequest | WorkflowP2PPlanRequest` 类型边界，以及 P2P 请求拒绝普通工具步骤的 fail-closed 保护；
- 读取测试的显式 `limit=50`，适配持久 dev.localhost 的历史数据增长；
- 当前 `P2P_EXECUTION` New Run 契约、合法 `CREATED → PROPOSED` 状态测试和实验测试类型标注。

没有改动 P2P 治理、ERP 写入、页面业务逻辑或 R10.4 worker；因此 R10.4 artifact 不被重新标成 `80abbba` 生成物，受影响 Runtime 分支和最终代码通过本报告列出的类型、单元、Frappe 集成及真实 P2P E2E 门禁复验。

## 3. 真实业务与故障证据

### 3.1 R10.4 进程故障矩阵

机器记录：[`phase10-r104-real-fault-matrix-v1.json`](phase10-r104-real-fault-matrix-v1.json)（SHA-256 `7e1e7846ffc5a908926964b9ebf42241211c76a5d62ab4b6a5397864f372e0eb`），报告：[`phase10-r104-real-fault-matrix-v1.md`](phase10-r104-real-fault-matrix-v1.md)（SHA-256 `dfbe866e831c55d8a0d79acc9838a4cbe810c9ca4bfefa7f0e30b1c3a0039e5c`）。

- 固定 `7` 个 Action 类型 × `4` 个故障位置，共 `28/28` 个唯一案例。
- 故障位置为 `reservation_committed`、`before_erp_call`、`after_erp_call`、`before_receipt`。
- 通过隔离测试进程和独立 `bench console` 子进程退出/重启；没有给生产 HTTP API 增加故障参数。
- 每次故障后新建连接回读 Action、Reservation、Receipt、Run 和 ERP 目标；ERP 已提交但回执不确定时进入 `RECONCILIATION_REQUIRED`/人工介入，不重复写入。

### 3.2 真实登录态浏览器链路

机器记录：[`phase10-r104-browser-acceptance-v2.json`](phase10-r104-browser-acceptance-v2.json)（SHA-256 `3d138402ca5ef97d1a70a05ae40ccfd86fb3b7f9a2ea700034bc88a33b2fc0a7`）。

- Run `50693541-627c-4593-a4aa-1cf3c45b98a8` 的业务 `run_state=SUCCEEDED`（完成后的 capability `status=REVOKED`）；六个业务 Action 都有独立审批、reservation 和 `SUCCEEDED` Receipt。
- Payment Operator 发起；Receiver、Accountant、Payment Approver 分别承担收货、发票和付款审批/执行；无权 Viewer 的创建/查看操作被拒绝。
- 独立 ERP 回读：PO `PUR-ORD-2026-02297` 的 `per_received=50.0`、`per_billed=50.0`；PR `MAT-PRE-2026-00519` 为 `Completed`；PI `ACC-PINV-2026-00346` 为 `Paid` 且 outstanding `0.0`；Payment Entry `ACC-PAY-2026-00186` 为 `Submitted`，分配 `10.0`，GL 借贷各 `10.0`。
- 最终截图 `output/playwright/phase10-r104-browser-payment-operator-v2-final.png` SHA-256 为 `792b717856c65ad2d36ad78ccd7b63b5787e4c8009ece105d1aa23f28a6038b6`。
- 一条旧探索 Run 的 Runtime 分析曾返回真实 `503 UNAVAILABLE` 并被取消；该失败保留在 artifact 中，不能被主路径成功覆盖，也没有绕过 Runtime 或权限伪造结果。

### 3.3 实验与当前代码复验

- R10.3 真实对照保持 `18` 个固定 trial、三种模式、失败全部保留；fixed Workflow 无模型调用，Provider usage 缺失时写 `null`，Adoption Card 仍为 `KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE`。实验 JSON SHA-256 为 `4accbbb38cb0ad8f29325cecd231520fa710b8a49c6e101a28a4ab998f20546e`。
- `make integration` 在当前 `80abbba` 运行了 `248` 个 Frappe tests；其中新批次真实 P2P E2E 输出 `15` 个 Action、两张 `Paid` 发票和 `SUCCEEDED` Run。

## 4. R10.5 出口门禁

| 检查 | 退出码 | 实际结果 |
|---|---:|---|
| `make format-check` | 0 | 390 files already formatted |
| `make lint` | 0 | All checks passed |
| `make type` | 0 | mypy 120 source files，无错误 |
| `make unit` | 0 | 856 passed，55 个已知 FastAPI/A2A 上游弃用 warning |
| `make integration` | 0 | Frappe 248 tests，`OK` |
| Harness structure | 0 | valid，manifest valid，issues 0 |
| Harness manifest | 0 | valid，errors/warnings 0 |
| Harness references | 0 | checked 724，broken 0，未截断（包含本轮新增报告/提案） |
| Harness drift | 1（预期） | `docs/PLAN.md` 用户修改 + 8 个 source fingerprint 待受保护同步 |
| Harness health | 0 | 只读 `79/100`，grade `C`；不替代语义审查 |
| `git diff --check` | 0 | 无 whitespace error |
| `ponytail-audit` | 0 | `Lean already. Ship.` |
| `ponytail-debt` | 0 | 1 个已有 marker，0 个 no-trigger |

Harness 检查为只读。`detect_drift.py` 的非零退出不表示业务门禁失败，而是受保护文件尚未获授权同步；当前报告不把它写成已清零。

## 5. D1–D9 重新评分

以下分数基于当前 `80abbba`、本轮全量门禁和 R10.3/R10.4 artifact 重新审查；不是沿用旧报告的结论文本。

| 维度 | 分数 | 当前依据 |
|---|---:|---|
| D1 需求与业务正确性 | 4 | 目标版本、部分处理、真实 ERP 结算和严格收口条件均有测试/回读 |
| D2 身份、权限与范围 | 4 | 发起人与审批人分离、Viewer 拒绝、执行前身份/对象/公司范围重检 |
| D3 状态、并发、幂等与恢复 | 4 | reservation/Receipt、28 案例、响应丢失、未知结果冻结和重启回读 |
| D4 Agent 信任与成本 | 3 | Runtime 不持有 ERP 写工具，LAB_ONLY 指标不外推生产；Runtime 503 限制保留 |
| D5 安全与数据保护 | 4 | 未授权、撤权、跨公司、摘要篡改和脱敏 artifact 有覆盖 |
| D6 UI、可访问性与双语 | 3 | 三角色真实页面、双语状态和权限失败已验收；未声称完整 a11y 审计 |
| D7 测试、真实集成与复现 | 4 | 856 unit、248 Frappe、R10.3/R10.4 机器 artifact 与当前代码门禁 |
| D8 治理、追踪与非虚构 | 4 | Action→Approval→Reservation→Receipt→PlanStep 可追踪，历史失败不覆盖 |
| D9 简洁性与可运维性 | 3 | 复用既有 Runtime/PlanStep/治理链；保留有限风险与受保护同步边界 |

合计 `33/36`，平均 `3.67`。当前已知 P0/P1 为 `0`；P2/P3 风险和复验条件见下节。

## 6. 风险登记与未完成项

| 风险 | 级别 | 当前状态与下一门禁 |
|---|---|---|
| Harness/README/权威文件指纹未同步 | P2 | `OPEN_PENDING_APPROVAL`；审阅文件级提案后单独确认并复跑 drift |
| `docs/PLAN.md` 用户修改 | P2 | 保留 unstaged 归属；不混入 Phase 10 同步提交 |
| 开发站点真实验收单据持续增长 | P2 | 保留不可变证据；后续按受治理清理，不 reset、不删除历史 |
| LAB_ONLY 延迟/token 被误读成生产收益 | P2 | Adoption Card 维持固定 Workflow 基线；禁止生产外推 |
| 完整 UI 无障碍审计与 icon warning | P3 | 后续 UI 门禁补齐；本轮不把部分审计写成完整通过 |
| 旧 Runtime 分析 `503 UNAVAILABLE` | P2 | 失败已保留；Runtime 不可用继续 fail closed，不进入付款成功结论 |

本报告不进入 Phase 11，也不把 `PENDING PROTECTED FILE SYNC` 写成 `READY FOR THE NEXT PHASE`。

## 7. Harness / README 文件级同步边界

新的文件级提案为 [`phase10-harness-sync-proposal-80abbba.md`](phase10-harness-sync-proposal-80abbba.md)。它列出当前 drift 的原始 SHA、目标文件范围、README 最小公开事实和应用后复验命令。

- `.harness/manifest.json`、`.harness/source-index.json`、`.harness/unresolved.json` 仍未修改；`unresolved.json` 保持 `NO_CHANGE`。
- `README.md` / `README.zh-CN.md` 只在用户确认公开事实后更新；不公开账号、单据完整敏感字段、Cookie、Token 或 Provider raw response。
- `docs/PLAN.md` 明确 `EXCLUDE`，保留用户的 Phase 13 修改；不能覆盖或混入本阶段提交。
- 提案应用需要单独确认；应用后必须重新运行 structure、manifest、references、drift 和 `git diff --check`，预期 drift 才能归零。

## 8. 独立对抗审查

最终结果：`PASS`，共两轮只读复核。第一轮核对最终实现 diff、R10.3/R10.4 artifact 绑定、R10.5 全量命令退出码、D1–D9、风险登记和受保护文件提案；确认路径与 SHA 真实、R10.4 artifact 没有被错误标成 `80abbba`、状态没有越过 `PENDING PROTECTED FILE SYNC`。第二轮在报告/manifest 更新后复核 hash、状态和受保护文件边界，仍为 `PASS`。审查通过不等于受保护同步已获授权，因此本报告保持 `PENDING PROTECTED FILE SYNC`，不写 `READY`。

## 9. 手工验收

1. 启动固定开发服务并以 Payment Operator 打开 `http://127.0.0.1:8000/desk/runs`，创建完整 P2P Run 并确认 PO 目标。
2. 以 Receiver、Accountant、Payment Approver 分别完成收货、发票和付款审批/执行；刷新确认每一步只有一个当前候选、独立审批、Reservation 和 Receipt。
3. 读取 PO、PR、PI、Payment Entry 与 GL，核对目标数量、`Paid`/`Submitted` 状态、outstanding `0` 和借贷平衡。
4. 运行 R10.4 故障矩阵脚本并检查 28 个唯一案例；未知结果必须停在人工对账，不能盲重试。
5. 在受保护同步批准前，不把报告状态解释为下一阶段就绪。

## 10. 最终边界

- 本报告是 Phase 10 开发验收证据，不是生产部署、银行付款、客户采用或性能承诺。
- 旧报告、旧失败和旧 artifact 保留；本报告只新增绑定和纠正说明，不改写 Git 历史。
- 本阶段停止于 Phase 10；受保护同步完成并复验后，仍需由维护者决定是否更新权威阶段状态。
