# Phase 6 执行任务包 — 受治理的第一批 ERP 行动

状态：`COMPLETED / PASS`。本任务包定义的 001–006 实现增量、真实 ERP/故障/浏览器证据、Harness 五项、独立 Test 和最终独立对抗 Review 均已通过；Phase 7 状态为 `READY_NOT_STARTED`，本文件不授权任何 Phase 7 功能。

主计划：`docs/PLAN.md#15-phase-6--受治理的第一批-erp-行动`

主计划 SHA-256：`e495b898cf17e502730b2929b4048299013570474c3b25be728c6c97d58b5ea0`

## 1. 本轮授权与不变边界

- 用户已明确授权：Phase 6 由 Agent 全程接管代码编写，不触发 Assignment 模式。
- 阶段内不因普通业务开发对话写学习笔记；只有阶段结束后用户主动触发答疑，才按 `docs/learning-notes/README.md` 记录问答。
- 开发日志仍按 `docs/development-log/README.md` 正常维护：仅在每个 commit 前于同一份 Phase 6 日志顶部追加一轮，记录真实业务结果、真实命令、失败/修复、风险和成本，不复制聊天流水。
- 不修改上游 Frappe/ERPNext，不直写 ERP 数据库，不绕过 controller、permission、Workflow、validation 或审计。
- Agent Runtime 继续不持有 ERP 任意写凭证；模型、检索、ERP 字段和用户文本都不能授权写入。
- Phase 6 只开放 `create MR Draft` 与 `create PO Draft`。PO Submit、Receipt、Invoice、Payment 和 generic DocType write 继续不可达。
- `approval-workflow-mapping` 已由 ADR-0007 和固定 `dev.localhost` bench 只读证据确认；任何更严格或无法验证的企业 Workflow 仍优先并 fail closed，写工具、写 endpoint 和 UI 执行按钮不得据此扩权。
- 不推送、不发布、不改写历史；每个步骤只提交一个可解释、可验证、可回滚的主业务结果，审查修复确有独立安全含义时才单独提交。

## 2. 已核验起点

- `HISTORICAL BASELINE`：生成任务包时 Git 为 `main`，与 `origin/main` 对齐且工作区干净；当时 HEAD 为 `51fcc15 docs(phase5): close harness exit gate`。
- `HISTORICAL BASELINE`：任务包生成时的收尾记录曾以 `9d104ab docs(phase6): record final regression evidence` 作为对齐起点；该描述只保留为历史生成基线，不代表当前 HEAD。
- `CONFIRMED`（2026-08-28 最终业务基线）：业务代码冻结于 `a36197c`；独立 Test 以 clean `5125f01` 为复核基线，之后仅有文档、Harness 和 artifact 变更，最终工作区保持 clean。
- `CONFIRMED`：Phase 5 为 `COMPLETED / PASS`，最终独立对抗审查和 Harness 收尾已完成。
- `CONFIRMED`：固定基线为 Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`、ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`。
- `CONFIRMED`：固定测试 site 当前没有 MR/PO Workflow；标准 DocPerm 允许 `Purchase User` 创建 MR/PO Draft，但这不等于审批策略已闭环。
- `CONFIRMED`：产品基线规定 MR Draft 与 PO Draft 由发起人显式确认即可执行；更严格的 ERP Workflow 始终优先。
- `CONFIRMED`：固定 `dev.localhost` 的 Workflow/Role/Permission 映射由 ADR-0007 和 bench probe 复验；具体企业覆盖、多级审批和金额阈值超出固定站点范围时仍必须取得新映射并 fail closed，`.harness/unresolved.json` 保持已批准状态不变。
- `HISTORICAL BASELINE`：生成任务包时现有代码只提供只读 Gateway、Run 状态机、持久只读 workflow、Trace 和 UI；当时没有 Phase 6 ProposedAction/Approval/Receipt 写入能力。
- `CONFIRMED`（当前收尾基线）：Phase 6 已实现 ProposedAction、Policy/Approval、MR/PO Draft writer、Reservation/Receipt、reconciliation 和 Runs 页面治理动作；PO Submit、后续 P2P 写操作与 generic writer 仍不可达。

## 3. 步骤顺序与提交边界

任何一步未通过独立检查时，不得开始下一步。每一步按固定闭环执行：

1. 输出十行内 Context Receipt，确认源、范围、未知、风险、命令和人工验收。
2. Execute 只修改允许文件，先写最小失败测试，再实现最小完整业务结果。
3. 先跑 targeted checks；失败时保留原始证据，按第 5 节分类并修复。
4. 对 L2 安全边界调用一个独立 Test 或 Review；同时跨身份、写入、幂等、审计多个边界时才同时调用两者。
5. `CHANGES_REQUIRED/FAIL` 回到 Execute 修复并只重跑受影响检查；同一问题最多两轮，第三轮仍失败则 `BLOCKED`。
6. commit 前运行受影响的 format/lint/type/unit/integration、`git diff --check` 和 `ponytail-review`，并在 Phase 6 开发日志顶部追加本轮真实记录。
7. 使用一个小而完整的 Conventional Commit；提交后检查 `git status --short --branch` 和提交内容，不能混入用户文件。

| 顺序 | PLAN 映射 | 单一结果 | 前置条件 | 建议主提交 | Execute | Test | Review | 未决项 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 001 | P6.1 映射子门禁 | 固定版本 Workflow/Role/Permission 取证与用户批准的 mapping | Phase 5 PASS；真实 site 可读 | `docs(phase6): resolve approval workflow mapping` | [execute](step-001/execute.md) | [test](step-001/test.md) | [review](step-001/review.md) | 用户必须批准具体 mapping；未批准不得进入 002 |
| 002 | P6.1 治理记录 | ProposedAction、PolicyDecision、ApprovalDecision、ExecutionReceipt 与状态机可持久化且 fail closed | 001 PASS | `feat(phase6): add governed action records` | [execute](step-002/execute.md) | [test](step-002/test.md) | [review](step-002/review.md) | 具体 DocType 字段以已批准 mapping 与 Frappe 约束为准 |
| 003 | P6.2 | 提议前门禁、审批决定和执行前全量重检闭环 | 002 PASS | `feat(phase6): enforce governed approval gates` | [execute](step-003/execute.md) | [test](step-003/test.md) | [review](step-003/review.md) | 无 |
| 004 | P6.3 | 一次受控 MR Draft 创建、read-back 和 Receipt | 003 PASS；隔离测试数据就绪 | `feat(phase6): create material request drafts safely` | [execute](step-004/execute.md) | [test](step-004/test.md) | [review](step-004/review.md) | 真实写测试必须使用 Phase 6 专用、可清理测试数据 |
| 005 | P6.4 | 幂等 replay/conflict、响应丢失、对账和人工接管 | 004 PASS | `feat(phase6): reconcile uncertain governed writes` | [execute](step-005/execute.md) | [test](step-005/test.md) | [review](step-005/review.md) | worker/process 隔离方案只能由真实故障注入证据决定 |
| 006 | P6.5 | PO Draft 闭环与高风险审查/审批/Receipt UI | 005 PASS；MR 闭环稳定 | `feat(phase6): deliver purchase order draft review flow` | [execute](step-006/execute.md) | [test](step-006/test.md) | [review](step-006/review.md) | 自定义视觉 token 不得在本步自由发明 |
| 007 | Phase 6 出口 | 全量真实链路、Rubric、风险、复杂度/Harness 和独立对抗审查 PASS | 001–006 全部 PASS | `docs(phase6): close governed write exit gate` | [execute](step-007/execute.md) | [test](step-007/test.md) | [review](step-007/review.md) | 审查非 PASS 或存在 P0/P1 时禁止结项 |

## 4. 每步最小测试梯度

开发中只先跑最相关检查，扩大范围的顺序固定为：

1. 纯 Python contract/state/policy/idempotency 单测；
2. Frappe app-test：DocType、permission、Workflow、controller、read-back、audit；
3. Runtime targeted tests：写 capability 不可达、模型无法执行写、内部认证与响应分类；
4. 隔离 site 真实 HTTP/E2E：登录态用户目标 → proposal → confirmation/approval → write → read-back → receipt；
5. 故障注入：状态漂移、权限撤销、重复键、同键异 digest、断连/超时、结果不确定、并发审批/执行；
6. 登录态浏览器：可见后果、证据、权限过滤、键盘、焦点、aria、加载/空/失败/过期/拒绝/修改/对账；
7. commit 前增量检查；Phase 6 出口才运行全量 format/lint/type/unit/integration、全部有限安全集、Harness/引用/drift 和真实环境验收。

有限安全场景必须 100% 通过。测试必须断言最终 ERP 状态和单据数量，不能只断言 HTTP 200、Agent 文案或本地 Receipt。

## 5. 发现问题与修复协议

每个失败先冻结证据，再修代码：记录命令、退出码、失败用例、run/action/approval/idempotency/correlation 标识、ERP 最终状态和是否产生单据。禁止在结果不确定时用重跑命令“看看能不能过”。

| 分类 | 识别信号 | 第一检查点 | 修复原则 | 必须复验 |
| --- | --- | --- | --- | --- |
| Schema/Input | unknown version/field/action、digest 不一致、typed parse 失败 | canonical bytes 与版本解析器 | 收紧 parser/validator；自然语言绝不覆盖 payload | 正常、缺字段、未知字段、重复 JSON key、NaN/Infinity、超长输入 |
| Identity/Permission | actor 与 session/run 不一致、403、跨公司可见 | 服务端 session、Run initiator、User Permission、DocPerm | 在 Frappe 边界重新解析身份；不接受 Runtime/客户端 actor | 发起人、无角色、跨公司、审批后撤权、混合凭证 |
| Policy/Workflow | 无匹配规则、规则冲突、审批绕过 | 已批准 mapping 与当前 Workflow snapshot | 更严格规则优先；缺失/冲突 fail closed | 无 Workflow 基线、启用更严格 Workflow 的隔离场景、并发决定 |
| State/TOCTOU | snapshot 过期、state_version/CAS 冲突、目标状态变化 | proposal snapshot、ERP modified/关键字段、审批时间 | 将 action 置 EXPIRED/CONFLICT；重新分析，不静默刷新批准内容 | 状态漂移、权限撤销、物料/供应商停用、已有 MR/PO 变化 |
| ERP validation | controller 抛 ValidationError/PermissionError | 固定版本 controller、官方测试、实际 DocType 必填字段 | 修正 typed payload 或业务前置；绝不 ignore_permissions 绕过 | 明确失败不产生 Receipt success、不留重复/半截单据 |
| Idempotency | same key replay、different digest conflict、STARTED 无终态 | Frappe 边界 reservation/result 与 ERP business key | reservation 先于写；同 digest 返回已核验结果，异 digest 拒绝 | 三类独立用例：same digest、different digest、response-loss recovery |
| Uncertain result | client 断连/超时但 ERP 可能已插入 | ERP 单据数、expected DocType、business keys、critical fields | 停止自动写；进入 `RECONCILIATION_REQUIRED`；查证后分类 | reconciled success/failure/manual intervention，且不盲重试 |
| Audit/Security | secret/原始 capability/未授权数据进入日志或 UI | exception、traceback、buffer、cookie/header、审计字段 | 源头脱敏、最小披露、权限过滤；保留受控运维诊断 | 深层异常图、浏览器输出、API 错误、跨用户审计访问 |
| UI/Accessibility | 按钮可重复点、状态只靠颜色、焦点丢失、危险后果不清 | 浏览器 DOM、网络调用、键盘顺序、aria live | 服务端门禁为主，UI 同步禁用与明确文案 | 重复点击、慢网、失败、权限、过期、拒绝、修改、对账 |

修复后先跑导致失败的最小用例，再跑该步骤全套检查；不要因一次全量通过掩盖未复现的原始失败。真实 ERP 已产生的测试草稿必须按显式测试标识清理或保留为可审计证据，不能用宽泛删除命令清场。

## 6. 初始风险登记与规避

评分使用 PLAN §4.6 的 likelihood × impact；进入开发后以真实证据更新。

| 风险 | 初始 L×I / 等级 | 预防 | 触发后的动作 |
| --- | --- | --- | --- |
| 未经授权的真实 ERP 写入 | 2×4；按定义视为 P0 | mapping 批准前无写 endpoint；Frappe session/permission/Workflow/approval 重检；无 generic write | 立即停止、隔离能力、核对 ERP、保留审计并回滚可安全回滚的 Synora 入口 |
| Agent Runtime 或模型获得写能力 | 2×4；P0 | Runtime 只产 typed proposal；写只在 Frappe deterministic executor；架构测试枚举可达工具 | 关闭入口，修复 allowlist/依赖边界，完整安全回归 |
| TOCTOU 导致批准内容与执行内容不同 | 3×4；P1 | snapshot、expiry、proposal digest、execution recheck、CAS | action 过期；禁止沿用审批，重新分析/确认 |
| 重复或不确定写入 | 3×4；P1 | 写前 reservation、same/different digest 规则、read-back、对账 | 停止重试，进入 reconciliation，按最终 ERP 状态收敛 |
| Workflow/角色策略被实现 Agent 猜测 | 2×4；P1 | Step 001 用户决策门禁；映射有版本/hash 和来源 | 标为 BLOCKED，不生成写代码或配置 |
| ERP controller/必填字段误解 | 3×3；P1 | 固定上游源码+官方测试+真实 app-test；通过标准 insert/save | 修正 typed payload/前置数据，不使用 ignore_permissions 绕过 |
| Secret/业务数据进入 Trace、异常或 UI | 2×4；P1，若泄漏则 P0 | 深层脱敏、最小字段、权限过滤、安全用例 | 立即停写，撤销能力，清理暴露面并复验所有序列化路径 |
| UI 误导用户已执行或可安全重试 | 3×3；P1 | 后果文案、禁重复、Receipt/对账状态、correlation id | 以服务端事实刷新 UI；不允许客户端推断成功 |
| 测试数据污染共享 site | 2×3；P2 | Phase 6 专用命名/idempotency 标识、前置清单、显式清理脚本 | 先盘点再精确清理，禁止重置整个 site/volume |
| 过度抽象拖慢高风险闭环 | 2×2；P3 | 每步调用 ponytail/ponytail-review；先 MR 单动作，再复用到 PO | 删除没有第二个真实调用点的抽象，但保留安全门禁 |

## 7. 停止条件

出现以下任一情况立即停止本步骤并向用户提交精确阻塞报告：

- Step 001 的 Workflow/角色/多级审批/金额策略尚未由用户批准；
- 固定上游 SHA、真实 site、凭证或隔离测试数据不可用；
- 权威文档对审批、权限、状态、字段或验收互相冲突；
- ERP 写入结果无法确认且对账路径尚未建立；
- 发现任何 P0、未关闭 P1、跨用户/公司越权或 Secret 泄漏；
- 同一问题经过两轮修复/独立复查仍失败；
- Harness 有阻塞 drift，或用户批准后来源 fingerprint/工作区发生实质变化；
- 连续 30 分钟没有业务代码、可运行测试、确认决定或明确阻塞证据。

阻塞报告必须给出：阶段/步骤、已完成内容、未提交 diff、命令和退出码、ERP 最终状态、风险等级、为何不能安全继续、可选方案及利弊、推荐项、用户只需回答的一个准确问题、恢复入口。

## 8. 来源指纹

以下来源已由 `fingerprint_sources.py` 只读计算并在 2026-08-28 收尾基线刷新；这些 hash 只证明来源版本，不替代真实测试或独立审查。任一关键来源再次变化时，不覆盖本任务包，先报告 drift 并提议新版本目录。

| Source | SHA-256 |
| --- | --- |
| `.harness/unresolved.json` | `1bf48d8e3b2006d3d0c457272b3c119daa5bf1c24614e67c5433f37c5fcea2aa` |
| `docs/ACCEPTANCE.md` | `b15181d21456722735bbca248ffb539c92ced35a12cfee529c3c40a24d2341a0` |
| `docs/ARCHITECTURE.md` | `d491c3798463e6c9137ddc71f2fd730eae87f6dd2fce8785f0dc606da2e061b4` |
| `docs/DESIGN.md` | `911a70a2afd3a5946f09967ea08f43e7721be6357a31551fa23a3f3271e1bf22` |
| `docs/DEVELOPMENT.md` | `3f0f60442248e2add68a6013047457635675b3d0e5f3abe13183ae1a29830159` |
| `docs/PLAN.md` | `7e8688c65ada7968e920a1973cfa64c54edac6937b83be547a025ab4aa1728ba` |
| `docs/PRD.md` | `8dde597d329a04ca5370b361b9759c83ffc33199faabc1bb961e04dee7ebc286` |
| `docs/SPEC.md` | `3194accd8a843b23918e516823d8c3985a441f20708f52a977637d3f0a7e03e3` |
| `docs/TESTING.md` | `de686677a52c297bfd63dce3e55de6ebe2a827c7df96e0ac2f9b1428b45e594f` |
| `docs/decisions/ADR-0006-phase5-workflow-engine.md` | `e4cbf63d8802c5a97ba90276f008df5c1ced810de078d3a2508c4a791e7442ec` |
| `docs/development-log/20260827-Phase-5-开发日志.md` | `6c805f96aad62135a868152c11737b655e303b12d49c9de19497d17e066e924d` |
| `docs/erp-baselines/phase1-permission-workflow-baseline.md` | `63e9bb56ba5a6ccb6ecf80f184b17fc8bb2fd3ddd31cf0917c322ad3eedf6f98` |
| `docs/source-maps/phase1-p2p-source-map.md` | `c2331e58ce8979d431c5b64c4635b9f67df6041c9320291befb966809b9fc844` |
| `docs/项目方向纠偏.md` | `c5b565bb3aeb042c58ac15de7ac8ac55265b82e0500ef6310305969f27cc3bb0` |

## 9. 手工接受本任务包

1. 确认 001–007 顺序没有跳过 `approval-workflow-mapping`、MR 先于 PO、对账先于阶段出口。
2. 确认每步都有独立 execute/test/review，且 Test 不修代码、Review 不接受 Execute 自述为证据。
3. 确认没有 Assignment 或学习笔记写入任务；只有 Phase 6 开发日志在 commit 前更新。
4. 确认所有写入都经过 Frappe controller、当前身份/权限/Workflow、审批、digest、幂等、read-back 和 Receipt。
5. 确认 PO Submit 及后续 P2P 写操作、generic write、任意 SQL/REST/MCP 仍明确禁止。
