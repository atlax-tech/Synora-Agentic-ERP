# Execute — Step 002：实现治理记录与合法状态转换

## 单一任务

实现版本化、可持久化、可关联且 fail-closed 的 ProposedAction、PolicyDecision、ApprovalDecision、ExecutionReceipt 基线，以及独立的 ProposedAction 状态机和 canonical digest；本步骤只建立治理事实，不连接真实 ERP 写入。

## 先读

- Step 001 已批准的 mapping ADR/决策包与独立 `PASS` 证据。
- `docs/PLAN.md#15-phase-6--受治理的第一批-erp-行动` — P6.1。
- `docs/PRD.md#54-f-004-可解释计划与-proposedaction`、`#55-f-005-policy--rbac--approval`、`#57-f-007-receipt幂等与对账`。
- `docs/SPEC.md#7-canonical-contract-concepts`、`#82-proposed-action`、`#14-observability-and-audit`。
- 现有 `synora_agentic_erp/agent/state_machine.py`、`agent/invocation.py`、Run/Trace/Audit DocTypes 和相邻测试。
- 编码前完整读取 `.agents/skills/ponytail/SKILL.md`，模式 `full`。

## 当前事实

- `CONFIRMED`：Run 生命周期和 workflow checkpoint 已有各自状态权威；新增 Action 状态不能让 checkpoint 或模型成为业务事实。
- `CONFIRMED`：SPEC 要求 unknown action/version/field/enum、自然语言与 payload 不一致 fail closed。
- `CONFIRMED`：Receipt 必须记录 verified ERP fields，但本步骤还没有 ERP 执行，因此不得生成 success Receipt。

## 改动边界

- 允许：新增四类治理 DocType 及 controllers；新增纯 Python contracts/canonicalization/action state module；最小持久化 service；关联 Run 的只读 API serializer（若 UI 后续需要但本步不暴露执行动作）；Frappe/纯 Python tests；Phase 6 日志。
- 候选入口：`synora_agentic_erp/governance/`、`synora_agentic_erp/synora_agentic_erp/doctype/synora_proposed_action/`、`synora_policy_decision/`、`synora_approval_decision/`、`synora_execution_receipt/`、`tests/` 和 `synora_agentic_erp/tests/`。若现有结构有更小的真实落点，可调整但必须在 Context Receipt 列出。
- 禁止：MR/PO insert/save、写 registry/tool/API、Runtime ERP credential、generic JSON blob 绕过 typed validation、`ignore_permissions=True` 执行业务写、模型直接创建/更新治理记录、修改上游、README、`.env*`、学习笔记或 managed Harness。

## 执行

1. 输出 Context Receipt，列出新增记录、字段不变量、状态转换、未开放写能力和 targeted tests。
2. 先写纯 contract/state 失败测试，再实现最小 contract：
   - ProposedAction：schema/action/run/action ids、typed action payload、evidence/calculation refs、risk/approval class、snapshot ref、idempotency key、expiry/revalidation、proposal digest、自然语言摘要仅作展示；
   - PolicyDecision：action/digest、identity/scope/permission/deterministic/Workflow-policy 逐门结果、matched rule/version、outcome/reason、snapshot/expiry、actor/timestamp；
   - ApprovalDecision：action/digest、authenticated actor、allow/decline/changes requested、matched rule、snapshot/expiry、reason/timestamp；
   - ExecutionReceipt：关联 ids/actors、payload digest、ERP DocType/name、verified critical fields、response/failure category、final/reconciliation state、timestamps/correlation。
3. 只接受已知 `schema_version=1`、已知 action `CREATE_MR_DRAFT`/`CREATE_PO_DRAFT` 和逐 action typed payload；拒绝 unknown fields、duplicate JSON keys、NaN/Infinity、错误 UUID/digest/时间、空 evidence。
4. 使用唯一 canonical JSON 规则计算 SHA-256 proposal digest：UTF-8、稳定 key 顺序、明确 decimal/date 表示、排除展示文案和运行后字段；contract tests 固定 golden bytes/digest。
5. 实现 ProposedAction 状态机：`DRAFT -> INVALID|POLICY_REJECTED|AWAITING_APPROVAL`，`AWAITING_APPROVAL -> APPROVED|DECLINED|EXPIRED`，`APPROVED -> EXECUTED|EXPIRED`；非法、重复、乱序和并发转换返回 typed conflict，不改状态。
6. DocType 权限默认最小：普通用户只能按现有 Run 可见性读取自己有权的记录；不能从 Desk 随意新增/编辑/删除；创建/转换只经过 deterministic service。System Manager 也不应通过 UI 绕过状态机产生“已执行”事实。
7. 持久化时将 run/action/digest/correlation 设为稳定关联；不可变 review payload 与 digest 一经进入 `AWAITING_APPROVAL` 不可覆盖。任何修改需求创建新 action/revision，不原地篡改已审内容。
8. Receipt factory 默认只允许真实执行/对账服务调用；缺 target name、read-back verified fields 或终态证据时不能创建 success Receipt。
9. 增加数据库约束/唯一索引能表达的唯一性；不能由 DB 约束表达的 invariants 在 service + 并发测试中验证。迁移必须幂等、可在现有 site 执行。
10. 跑 targeted pure tests、Frappe app-test、权限/迁移/serialization tests、现有 Run/Trace 回归、format/lint/type 和 `ponytail-review`。
11. 独立 Test/Review 不通过时回到 Execute 精确修复；commit 前更新 Phase 6 开发日志并提交一个主业务结果。

## 问题发现与修复

- digest 在 Python/Frappe/JS 表示不一致：以服务端 canonical bytes 为唯一权威；UI 永不自行计算批准 digest。
- Frappe JSON 字段接受未知结构：先经 strict parser 生成 typed value，再序列化持久化；数据库 JSON 不等于 schema 验证。
- 状态竞争导致双转换：使用数据库锁/CAS/state_version，冲突返回 409；不能先读后无条件 set。
- 普通用户能通过 DocType API 写状态：收紧 DocPerm/controller hooks，并加真实 HTTP 权限测试。
- success Receipt 可由测试以外路径伪造：将 factory 依赖 verified execution outcome，入口不可 whitelisted 给浏览器/Runtime。
- 记录数量/抽象明显膨胀：用 ponytail-review 删除无第二个使用场景的 repository/interface，但保留不可变性、状态、权限、digest 和审计边界。

## 验证与证据

- Pure unit：contract、canonical digest golden、状态全表、非法/并发转换。
- Frappe app-test：DocType 安装/迁移、unique/immutable、permission/visibility、Run 关联、serializer、Receipt 伪造拒绝。
- Regression：现有 Run lifecycle、Gateway contract、Trace/Audit、Phase 5 workflow targeted tests。
- 提供实际命令、退出码、数据库迁移结果、文件/字段摘要、未运行检查、人工读取检查；不得声称 ERP write 或 Receipt success 已验收。

## 提交纪律

建议一个提交：`feat(phase6): add governed action records`。commit 前日志顶部新增一轮；不把 Harness/README/学习笔记混入。

## 最终报告

报告可持久化事实、状态和权限；明确真实 MR/PO 写入仍不可达；列出测试证据、剩余风险和 Step 003 前置。不得自评 `PASS`。
