# Phase 1 · Inc-4（P1.4）P2P 源码地图

日期：2026-08-24 ｜ 状态：已完成并验证

## 结果

新增单一源码地图文档 `docs/source-maps/phase1-p2p-source-map.md`，覆盖四个核心 DocType（Material Request / Purchase Order / Purchase Receipt / Purchase Invoice）、三条转换链（MR→PO→PR→PI）、默认权限矩阵、Workflow 观察、14 处代表性官方测试与六类业务不变量（币种、会计年度、提交后字段不可变、超收/超开票、数量、取消/回滚）。每条结论带 `[S]` 源码事实 / `[R]` 运行观察 / `[P]` 产品策略 / `[I]` 推断 / `[U]` 未决项 标签，明确区分。

## 改了什么

- 新增 `docs/source-maps/phase1-p2p-source-map.md`：仅文档，无代码、无上游改动。
- 本增量未修改任何 Frappe/ERPNext 上游、未直写数据库、未 reset/cleanup 当前 site/volume、未修改 `docs/PLAN.md`。

## 为什么现在做

交接日志（`2026-08-24-phase1-handoff-after-inc3.md`）明确：P1.1–P1.3 已提交，P1.4 仅完成只读取证、尚无源码地图文件，P1.5 未开始；后续续跑点要求"新增单一源码地图文档和对应中文开发日志"。

## 取证方式与关键事实

- 候选环境容器只读断言：`git -C apps/frappe rev-parse HEAD` = `6a329d0…`、`git -C apps/erpnext rev-parse HEAD` = `11e0ba0…`，两上游 `git status --porcelain` 为空（命令见下方）。
- 逐条核对交接日志中列出的转换入口与官方测试行号，全部一致，并补查了以下细节：
  - PO→PR 剩余数量 = `qty - received_qty`（`purchase_order.py:768-770`）；
  - PR→PI 拒绝已全部开票/退货 items（`purchase_receipt.py:1521-1523`）；
  - 通用超收允许率来自 `status_updater.py:423-425,754,796-805`（Stock Settings / Item 级），`stock_controller.py:1776` 是内部调拨路径；超开票豁免角色来自 `Accounts Settings.role_allowed_to_over_bill`（`accounts_controller.py:2223`）；
  - 首个 Buying Price List 自动设为 Buying 默认（`price_list.py:35,40` `set_default_if_missing`）。
- 候选 site 只读观察（`bench console` 只读查询）：`Workflow` DocType 无记录（四单据均未启用 Workflow）；四单据默认 DocPerm 矩阵（Purchase User 可全链操作 MR/PO/PR、对 PI 只读；Stock User 对 PO 只读；Accounts User 对 PR 只读）；`Global Defaults=CNY`、`Buying Settings.buying_price_list=SYNORA-P1 Buying CNY`、`over_delivery_receipt_allowance=0.0`、`role_allowed_to_over_bill=""`；P1.3 四单据最终状态只读回读（docstatus=1，MR Received、PO/PR Completed、PI Unpaid outstanding=500.0）。
- 明确写入地图的边界结论：site 无 Workflow 是运行观察，不等于审批策略已解决；`PRD.md:232` 与 `SPEC.md:256-261` 的审批基线是产品策略，具体企业 Workflow/多级审批/角色映射为未决项，最迟 Phase 4 启用写入前由用户决定。

## 验证

- 独立 Test Agent：`PASS`（对地图逐条核验源码行号/分类/测试名）。
- 独立 Review Agent：`PASS`（需求完整性、事实/推断区分、对抗场景、日志可读性）。
- 容器内只读断言退出码 0：上游 HEAD 与候选 SHA 相等、上游工作区 clean。
- 语法/结构检查：`git diff --check` 退出 0。

## 局限

- 本地图基于候选 SHA，正式冻结前不得用于结论性证据之外的用途（ADR-0001 约束）；P1.5 冻结后行号以 ADR-0002 记录为准。
- 取消/退货路径本阶段未真实运行，相关行为以官方测试为证据源。
- `[I]` 推断仅两处（无 Workflow 一致性、PI 取消回滚实现位置），依据已在地图中写明。

## 可重复人工验收

```bash
git status --short                 # 期望：仅新增 source-map 与本次开发日志两个文件（见下）
bash env/dev/scripts/dev/env.sh bash \
  "cd /home/frappe/bench && git -C apps/frappe rev-parse HEAD && git -C apps/erpnext rev-parse HEAD && git -C apps/frappe status --porcelain && git -C apps/erpnext status --porcelain"
# 期望：6a329d068416768ec47ccd3326b9cc95a8d7bf99 / 11e0ba0a1c45f217e2e73e885f699102d06da325 / 无输出
```

打开 `docs/source-maps/phase1-p2p-source-map.md`，抽查任意 `[S]` 条目与容器内固定 SHA 源码行号一致，`[R]` 条目与候选 site 只读查询一致。
