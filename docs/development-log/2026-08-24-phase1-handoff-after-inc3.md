# Phase 1 交接断点：Inc-3 完成，Inc-4 仅完成只读取证

日期：2026-08-24 ｜ 状态：已暂停，等待后续 Agent 从 P1.4 继续

## 当前结论

Phase 1 没有完成。P1.1、P1.2、P1.3 已形成可回滚提交；P1.4 尚未产生源码地图文件，P1.5 尚未开始。暂停时工作区为 clean，不存在需要接管的未提交代码。

Phase 1 整体覆盖环境、确定性数据、真实 P2P、源码地图和版本冻结，范围较大，但 `docs/PLAN.md` 已拆成 P1.1–P1.5。此前耗时的主要原因是 Inc-3 同时处理用户初始化、运行器、Fiscal Year、Price List、全局币种和失败用例，而不是需要修改当前 PLAN。详细复盘见 `2026-08-24-phase1-inc3-manual-p2p.md`。

## 已提交的回滚节点

| 提交 | 结果 |
| --- | --- |
| `ea3a46f` | P1.1 空 volume 候选环境重建验证 |
| `a040142` | P1.2 确定性主数据 seed/cleanup |
| `2269087` | P1.2 Fiscal Year、CNY Buying Price List 与事务/漂移门禁 |
| `ec0fbb6` | P1.2 通过标准 Global Defaults DocType 对齐 CNY |
| `99b4ca2` | P1.3 命名用户真实 MR→PO→PR→PI 与失败路径基线 |

P1.3 最终证据为 `P2P-RUN-OK`：MR `MAT-MR-2026-00009`、PO `PUR-ORD-2026-00009`、PR `MAT-PRE-2026-00007`、PI `ACC-PINV-2026-00005`；PI 为 CNY、`Unpaid`、outstanding 500.0。没有创建 Payment Entry，没有重置或清除现有 site/volume。

## P1.4 已完成的只读取证

固定候选提交再次读回一致，上游 `git status --short` 均为空：

- Frappe：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`
- ERPNext：`11e0ba0a1c45f217e2e73e885f699102d06da325`

已定位的主转换与校验入口：

- MR→PO：`apps/erpnext/erpnext/stock/doctype/material_request/material_request.py:561`，`make_purchase_order`
- PO→PR：`apps/erpnext/erpnext/buying/doctype/purchase_order/purchase_order.py:761`，`make_purchase_receipt`
- PR→PI：`apps/erpnext/erpnext/stock/doctype/purchase_receipt/purchase_receipt.py:1509`，`make_purchase_invoice`
- Price List 币种：`apps/erpnext/erpnext/controllers/accounts_controller.py:1017`，`set_price_list_currency`
- 往来科目币种：`apps/erpnext/erpnext/controllers/accounts_controller.py:2515`，`validate_party_account_currency`
- 会计年度：`apps/erpnext/erpnext/accounts/utils.py:51,141`，`FiscalYearError`
- 提交后字段变更：`apps/frappe/frappe/model/base_document.py:1270`，`_validate_update_after_submit`
- 超收/超开票：`apps/erpnext/erpnext/controllers/stock_controller.py:1776`、`status_updater.py:796`、`accounts_controller.py:2223`
- 首个 Buying Price List 默认行为：`apps/erpnext/erpnext/stock/doctype/price_list/price_list.py:40`

已定位的代表性官方测试：

- MR→PO：`test_material_request.py:41,1228,1234,1264,1288`
- PO→PR/PI 与整链：`test_purchase_order.py:86,647,676,1539`
- PR→PI、状态、退货与取消回滚：`test_purchase_receipt.py:131,706,1121,1147,6417`
- PI 超开票、未付金额与取消：`test_purchase_invoice.py:1139,1172,3136,3271`

运行时取证显示候选 site 对 MR、PO、PR、PI 没有启用 Workflow。标准 DocPerm 的关键观察为：Purchase User 可操作 MR/PO；Stock User 可操作 MR/PR；Accounts User 可操作 PI，并对 PR 只读；PO 对 Stock User 只读；PI 对 Purchase User 只读。完整源码地图必须把以下四类内容明确分开：固定源码事实、候选 site 运行观察、Synora 已批准产品策略、仍待企业配置决定的事项。

特别注意：ERPNext 默认 DocPerm 允许同一 Purchase User 创建并提交 PO，但 Synora 的 `docs/PRD.md:232` 与 `docs/SPEC.md:256-261` 要求 PO Submit 及后续 P2P 写操作由不同于发起人的有权审批人授权，并始终采用 ERP Workflow 与 Synora 策略中更严格者。P1.3 已用 Buyer/Approver 两个身份验证此基线。不要把“当前 site 无 Workflow”误写成审批策略已经解决；具体企业 Workflow、多级审批和角色映射仍需在写入启用前由用户决定。

## 后续 Agent 的精确续跑点

1. 先重读 `AGENTS.md`、`docs/PLAN.md` 与 Phase 1 权威文档，不要仅依赖本日志。
2. 从 P1.4 继续，新增单一源码地图文档和对应中文开发日志；不要修改上游或业务脚本。建议路径为 `docs/source-maps/phase1-p2p-source-map.md`。
3. 地图至少覆盖四个 DocType JSON/controller、转换函数、权限矩阵、Workflow 观察、代表性官方测试、状态/数量/币种/取消等业务不变量，以及事实/观察/推断/未决标签。
4. 按 PLAN 使用独立 Test 与 Review；通过后做一个纯文档小步提交。
5. 然后才进入 P1.5。版本冻结涉及 Harness/权威文档同步，必须先完整读取并遵守 `harness-update`、`harness-check`、`ponytail-audit`、`ponytail-debt`；固定 commit pair 前必须调用独立对抗 Agent。
6. 不得 reset、cancel、cleanup 或删除当前运行证据；如需验证干净环境可复跑，应使用隔离的 Compose project/volume，或先报告成本与证据边界。

## 本次交接检查与限制

- 暂停前 `git status --short`：无输出。
- P1.4 的上述检索来自当前 Docker 候选环境，只读执行；未编辑 Frappe/ERPNext。
- 本交接只记录状态，不声称 P1.4/P1.5 或 Phase 1 完成。
- `/tmp/*.txt` 是早期诊断临时证据，不受 Git 管理；接手者不应把它们当作唯一事实源。

## 可重复只读复核

```bash
git status --short
git log --oneline -8
bash env/dev/scripts/dev/env.sh bash "cd /home/frappe/bench && git -C apps/frappe rev-parse HEAD && git -C apps/erpnext rev-parse HEAD && git -C apps/frappe status --short && git -C apps/erpnext status --short"
```

期望主仓库与两个上游工作区均无未提交改动，两个 SHA 与上文一致。
