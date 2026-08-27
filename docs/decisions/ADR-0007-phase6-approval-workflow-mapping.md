# ADR-0007：Phase 6 首批 Draft 写入的审批与 Workflow 映射

- 状态：已批准并固化（固定开发 site；企业更严格配置运行时优先）
- 日期：2026-08-27
- 映射版本：`P6-MAP-20260827-v1`
- 关联：`docs/PLAN.md` §15；`docs/PRD.md` §5.5–5.7；`docs/SPEC.md` §10–11；`docs/ARCHITECTURE.md` “Approval and Workflow Authority”；`.harness/unresolved.json#approval-workflow-mapping`

## 背景

Phase 6 要在不绕过 ERPNext 权限、Workflow、controller、事务和审计的前提下，开放第一批真实 ERP 行动：创建 Material Request Draft 与 Purchase Order Draft。Phase 1 的“候选 site 无 Workflow”只是一次环境观察，不能单独证明审批策略已解决；固定版本的出厂 DocPerm 还允许 `Purchase User` 直接提交部分单据，因此必须把产品审批基线映射为服务端可重检的规则。

本 ADR 的取证对象是固定版本 Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`（16.31.0）、ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`（16.32.3）和隔离开发 site `dev.localhost`。取证脚本只读查询元数据、DocPerm、用户角色、User Permission、`frappe.has_permission`、hooks 和 Server Script 元数据，不读取脚本正文、不执行配置变更、不创建业务单据。

## 证据与事实分类

### `[S]` 固定源码/契约事实

- ERPNext Workflow、Role、DocPerm、controller 是企业权限与状态权威；Synora 只能增加限制，不能降低它们。
- `docs/PRD.md` 与 `docs/SPEC.md` 规定 MR Draft/PO Draft 的测试基线为发起人显式确认；PO Submit 及后续 P2P 写操作必须由不同于发起人的有权审批人授权。
- 提议和执行前均须按 schema → identity → permission → deterministic checks → Workflow/policy → snapshot/expiry/digest 顺序评估；缺失、冲突、陈旧或不可验证的策略必须 fail closed。

### `[R]` 2026-08-27 固定 site 运行观察

原始输出保存在本机忽略目录 `env/dev/p6/artifacts/approval-mapping-console.txt`，结构化副本为 `env/dev/p6/artifacts/approval-mapping.json`；可由 `env/dev/p6/approval_mapping_probe.py` 在 bench console 重跑。结果摘要：

- site 为 `dev.localhost`；两份上游 revision 与 ADR-0002 完全一致，宿主检查确认两仓 clean。
- MR/PO 目标 Workflow 记录为空；没有 active Workflow、state、transition 或 condition 可供当前 site 解释。
- Material Request 的标准 DocPerm 对 `Purchase User`、`Purchase Manager`、`Stock User`、`Stock Manager` 提供 permlevel 0 的 read/create/write/submit/cancel/amend；Purchase Order 对 `Purchase User`、`Purchase Manager` 提供全权行，对 `Stock User` 只读，Purchase Manager 另有 permlevel 1 read/write 行。两类 DocType 均为标准、可提交 DocType，未发现自定义 DocType。
- Buyer、Approver、Receiver、Company-A-only 对 MR/PO 的当前 `frappe.has_permission(read/create)` 均为 true；Accountant 与无业务角色 Viewer 均为 false。角色结果只作证据，不被实现硬编码为审批授权。
- Company-A-only 有一条 Company User Permission，值为 `SYNORA-P1 Test Company`，适用于全部 DocType；其余取证用户没有 Company/Warehouse User Permission。没有目标 DocType 的 `permission_query_conditions`/`has_permission` hooks，也没有 Server Script 元数据记录。
- 当前配置读到 `developer_mode=1`、`allow_tests=0`；未输出任何凭证或 capability。

### `[P]` 已批准产品基线

- Draft 动作必须由发起人明确确认；确认的是不可变 typed proposal digest，不是自然语言摘要。
- 更严格的 ERP Workflow 始终优先；任何 active Workflow、条件、多级规则或金额阈值只要无法被当前 mapping 完整解释，就不得继续执行。
- PO Submit、Purchase Receipt、Purchase Invoice、Payment 以及 generic DocType write 不属于本 ADR 的能力范围。

### `[U]` 有意保留的外部配置不确定性

本 ADR 不声称所有企业 site 都没有 Workflow，也不替企业决定未来的多级审批或金额阈值。不同 site 的配置被当作运行时输入；出现新规则时先进入 `UNRESOLVED/FAIL_CLOSED`，完成新的取证与 mapping 后才能恢复写入。

## 决策

### 1. 当前 Phase 6 Draft mapping

| Action | actor 权威 | 最低有效权限 | confirmation / approval class | 目标状态 |
| --- | --- | --- | --- | --- |
| `CREATE_MR_DRAFT` | 当前 Frappe session user，且必须等于 Run initiator | 服务端重检目标 DocType `read` + `create`，并重检 company/warehouse User Permission、controller 依赖和目标对象状态 | `INITIATOR_CONFIRMATION`；不接受请求体 actor/role | `Material Request.docstatus=0` |
| `CREATE_PO_DRAFT` | 当前 Frappe session user，且必须等于 Run initiator | 服务端重检目标 DocType `read` + `create`，并重检 company/warehouse User Permission、supplier/item/price/controller 依赖和目标对象状态 | `INITIATOR_CONFIRMATION`；不接受请求体 actor/role | `Purchase Order.docstatus=0` |

实现按 effective permission 工作，不把 `Purchase User` 名称当成永恒授权。当前取证中的角色名称只用于解释固定 baseline；用户被撤权、停用或 scope 改变时，执行前重检失败。

### 2. Workflow 与策略优先级

每次提议和执行前都重新查询目标 DocType 的 active Workflow。若存在 active Workflow：

1. 必须固定其 document type、state field、states、transitions、allowed roles、self-approval 和 condition 快照；
2. 以 Workflow 与 Synora 策略中更严格者为准；
3. 任一字段缺失、条件不可确定解释、角色/状态冲突或快照过期，都将 action 置为 fail-closed（`POLICY_REJECTED`/`EXPIRED`），不自动降级为发起人确认；
4. 新规则需要新的 mapping 版本和证据，旧 digest/approval 不迁移。

当前 `dev.localhost` 没有 active Workflow，因此采用表中 `INITIATOR_CONFIRMATION`；这只适用于当前固定 site 的已取证事实。

### 3. 时间、快照和撤销

- mapping 版本为 `P6-MAP-20260827-v1`；proposal 的默认实现有效期为 900 秒，具体 action 仍必须保存 `expires_at`，不得在重检时静默延长。
- proposal、policy、approval、execution 均绑定 Run、action、correlation、snapshot 和 canonical digest。
- session/Run initiator、enabled 状态、effective permission、scope、Workflow、业务对象关键字段、expiry、digest 和 idempotency reservation 在执行前全部重检。
- 任何映射变更、源 SHA 漂移、Workflow 出现或不可解释、权限撤销、状态/金额/供应商/物料变化均撤销旧执行资格；用户必须重新分析并产生新 action/revision。

### 4. 职责分离边界

本版本只解决两个 Draft action。PO Submit 及后续 P2P 写入继续要求与 initiator 不同的 authenticated approver，并留待后续里程碑单独取证和实现。`System Manager` 的 DocType 能见度不能生成 `APPROVED`、`EXECUTED` 或成功 Receipt；这些事实只能由确定性治理服务产生。

## 被拒绝/延期的选项

- **B：在开发 site 主动配置 Workflow。** 本轮不修改 site 策略，避免把一次演示配置误当企业基线；若后续要验证 Workflow，必须建立隔离 site、配置回滚和独立集成证据。
- **C：把 Draft 改为独立审批人、多级审批或金额阈值。** 这会改变 PRD/SPEC 的当前 Draft 基线，必须先走产品需求批准；本 ADR 不用技术文档替代需求决定。

## 后果与风险

- 正向：Phase 6 可以在当前固定 site 上机械执行两个 Draft action，同时保留对企业 Workflow 的 fail-closed 入口；角色、权限、scope、快照和 digest 都能在服务端重检。
- 代价：不同企业配置在取证完成前不可执行；当前 mapping 不提供批量、多级和金额阈值能力。
- 主要风险：默认 ERP DocPerm 比产品策略宽，故不能把 `create`/`submit` 视作审批通过；响应丢失和状态漂移必须由后续 reservation/read-back/reconciliation 解决。

## 撤销与验证

撤销本 ADR 的条件是固定 SHA/site 变化、发现 active Workflow、证据字段不全、permission hook/Server Script 出现，或任何真实写入无法确认。撤销动作是把 `approval-workflow-mapping` 恢复为 `UNRESOLVED`、关闭 Draft writer、保留已产生的审计/对账证据，并从新 mapping 版本重新取证。

验证入口：

```bash
uv run --python 3.14 pytest -q env/dev/p6/test_approval_mapping_probe.py
docker cp env/dev/p6/approval_mapping_probe.py synora_phase1_dev-bench-1:/tmp/synora_p6/approval_mapping_probe.py
docker compose -f env/dev/docker-compose.yml exec -T bench bash -lc "cd /home/frappe/bench && printf '%s\\n' 'exec(open(\"/tmp/synora_p6/approval_mapping_probe.py\").read(), globals())' 'print(render(collect()))' | bench --site \"\$FRAPPE_SITE\" console"
```

上述命令只读；当前运行输出和首次 `python3`/`desk_access` 字段失败均记录在 Phase 6 开发日志，不将失败改写为成功。

## 追踪

- 取证脚本：`env/dev/p6/approval_mapping_probe.py`
- 纯测试：`env/dev/p6/test_approval_mapping_probe.py`
- 原始 artifact：`env/dev/p6/artifacts/approval-mapping-console.txt`
- 结构化 artifact：`env/dev/p6/artifacts/approval-mapping.json`
- 固定上游：`docs/decisions/ADR-0002-frozen-baseline-pair.md`
- 权限/Workflow source-map：`docs/source-maps/phase1-p2p-source-map.md`
