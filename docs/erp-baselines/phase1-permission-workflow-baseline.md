# Phase 1 权限 / Workflow 基线（固定版本）

- 日期：2026-08-24 ｜ 状态：已冻结（随 ADR-0002）
- 固定基线：Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`（16.31.0）；ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`（16.32.3）
- 用途：Phase 2 起权限、Workflow、契约与集成测试的固定事实引用；详细取证与源码行号见 `docs/source-maps/phase1-p2p-source-map.md`，本文件只保留冻结结论与可重复验证命令。

## 1. 默认权限矩阵（固定版本 DocPerm）

下列为固定版本四类单据的标准 DocPerm（候选 site 只读查询与 DocType JSON 逐格一致；seed/p2p 脚本未修改任何 DocPerm）：

| Role | Material Request | Purchase Order | Purchase Receipt | Purchase Invoice |
| --- | --- | --- | --- | --- |
| Purchase User | 全权 | 全权 | 全权 | 只读 |
| Purchase Manager | 全权 | 全权（另有 level 1 只读+写） | — | — |
| Stock User | 全权 | 只读 | 全权 | — |
| Stock Manager | 全权 | — | 全权（另有 level 1 只读+写） | — |
| Accounts User | — | — | 只读 | 全权（delete=0） |
| Accounts Manager | — | — | — | 全权（另有 level 1 只读+写） |
| Auditor | — | — | — | 只读 |

关键结论（运行观察）：默认 DocPerm 允许**同一个 Purchase User 创建并提交 PO**；PO 对 Stock User 只读、PI 对 Purchase User 只读、PR 对 Accounts User 只读。这是 ERPNext 出厂行为，不是 Synora 审批策略。

## 2. Workflow 基线

- 运行观察：候选 site 与 2026-08-27 Step 001 probe 的 `Workflow` DocType 均无目标记录，MR/PO/PR/PI 未启用 Workflow。
- 结论：**"无 Workflow"是运行观察，不代表所有企业审批策略都无 Workflow**。ADR-0007 将固定 `dev.localhost` 的 MR/PO Draft 映射为 Run 发起人显式确认；其他 site 的更严格 Workflow、多级审批或金额阈值仍作为运行时输入，未映射或冲突时 fail closed。
- 本阶段未在候选 site 启用任何 Workflow（属企业配置决策，不在本阶段擅自更改）。

## 3. 审批基线（产品策略，引用权威文档）

- MR Draft / PO Draft：发起人显式确认即可执行（`docs/PRD.md:232`、`docs/SPEC.md:256-261`）。
- PO Submit、Purchase Receipt、Purchase Invoice、Payment 相关写操作：必须由**不同于发起人**的有权审批人授权。
- 始终采用 ERP Workflow 与 Synora 策略中更严格者；规则缺失、冲突或无法验证时 fail closed。
- 默认 DocPerm 的单用户全链能力不构成审批通过的依据，Synora 治理层必须施加更严格的审批门禁（Phase 6 实现）。
- 固定开发 site mapping（ADR-0007）：`CREATE_MR_DRAFT` / `CREATE_PO_DRAFT` 的 actor 必须是当前 Frappe session 且等于 Run initiator；服务端每次重检目标 `read/create`、company/warehouse scope、controller 依赖、Workflow、snapshot、expiry 和 digest；目标状态只允许 `docstatus=0`。

## 4. 关键配置基线（确定性数据环境）

| 配置 | 值 | 来源 |
| --- | --- | --- |
| Global Defaults.default_currency | CNY | P1.2 seed（Inc-2 日志） |
| Buying Settings.buying_price_list | SYNORA-P1 Buying CNY | P1.2 seed（首个 Buying Price List 默认行为） |
| Stock Settings.over_delivery_receipt_allowance | 0.0 | 固定版本默认 |
| Accounts Settings.role_allowed_to_over_bill | （空） | 固定版本默认，不豁免任何角色 |
| Fiscal Year | SYNORA-P1 FY 2026 | P1.2 seed（MR 提交前提） |

## 5. 未决项（不受本基线影响）

- 企业 site 的 Workflow/多级审批/金额阈值 overrides：当前固定开发 site mapping 已由 ADR-0007 固化；其他配置出现时必须创建新的 mapping 版本，不能复用本 baseline。
- `third-party-licenses`、`runtime-user-authorization`、`frontend-design-baseline`、`product-commands`、`model-selection`、`workflow-engine-spike` 等保持 UNRESOLVED（见 `.harness/unresolved.json`）。
- `erp-version-pair`：已由 ADR-0002 解决。

## 6. 可重复验证命令

```bash
# 0) 工作区干净
git status --short

# 1) 上游 SHA 与清洁断言（期望：两 SHA 与 ADR-0002 一致，两仓无输出）
bash env/dev/scripts/dev/env.sh bash \
  "cd /home/frappe/bench && git -C apps/frappe rev-parse HEAD && git -C apps/erpnext rev-parse HEAD && git -C apps/frappe status --porcelain && git -C apps/erpnext status --porcelain"

# 2) 确定性主数据（幂等，期望 SEED-OK）
bash env/dev/scripts/dev/env.sh seed

# 3) 命名测试用户（幂等，期望 P2P-USERS-OK）
bash env/dev/scripts/dev/env.sh p2p-users

# 4) 人工 P2P 基线（期望 P2P-RUN-OK，退出 0）
bash env/dev/scripts/dev/env.sh p2p-run

# 5) 空卷重建（P1.1 证据；破坏性，仅在隔离/备份完成后执行，期望 bootstrap 全程成功）
bash env/dev/scripts/dev/env.sh bootstrap
```

上述 1–4 于 2026-08-24 复核通过（真实命令与退出码见 `docs/development-log/20260824-Phase-1-开发日志.md`）；第 5 项证据以 Phase 1 第 1 轮空卷重建记录为准，本次未重跑。
