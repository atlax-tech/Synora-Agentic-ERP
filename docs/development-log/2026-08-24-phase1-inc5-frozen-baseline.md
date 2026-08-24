# Phase 1 · Inc-5（P1.5）固定基线冻结

日期：2026-08-24 ｜ 状态：已完成并验证

## 结果

按 `docs/PLAN.md` P1.5 固定 Frappe/ERPNext commit pair 为 Phase 1 正式基线，形成 ADR、权限/Workflow 基线与可重复验证命令：

- Frappe：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`（16.31.0）
- ERPNext：`11e0ba0a1c45f217e2e73e885f699102d06da325`（16.32.3）

冻结决定记录于 `docs/decisions/ADR-0002-frozen-baseline-pair.md`；权限/Workflow 基线（固定版本）与验证命令见 `docs/erp-baselines/phase1-permission-workflow-baseline.md`。

## 改了什么

- 新增 `docs/decisions/ADR-0002-frozen-baseline-pair.md`：冻结 commit pair；明确本次仅冻结 commit pair，Node/MariaDB/Redis 维持 major tag 及其漂移风险缓释；如实记录 SHA 为 version-16 移动分支 tip 的官方 release bump commit 快照（解析日期 2026-08-24，非 release tag）；取代 inc1 日志中"完整依赖固定属 P1.5 晋升要求"的表述。
- 新增 `docs/erp-baselines/phase1-permission-workflow-baseline.md`：固定版本默认权限矩阵、Workflow 观察、审批基线（产品策略引用）、关键配置基线、未决项、可重复验证命令。
- 修改 `docs/development-log/2026-08-24-phase1-inc1-empty-rebuild.md`：在限制节追加【2026-08-24 补充】取代声明（原文未删改）。
- 未修改 Frappe/ERPNext 上游、未直写数据库、未 reset/cleanup 当前 site/volume、未修改 `docs/PLAN.md`。

## 为什么现在做

P1.3 人工 P2P 基线已 `P2P-RUN-OK`（PLAN P1.5 前置条件），P1.4 源码地图已通过独立 Test/Review；按 PLAN"版本固定触发独立对抗审查和 Harness 文档同步授权"，固定前执行独立对抗审查并通过后冻结。

## 实际运行证据（2026-08-24，全部真实命令）

```bash
# 上游 SHA 与清洁断言（退出 0）
bash env/dev/scripts/dev/env.sh bash \
  "cd /home/frappe/bench && git -C apps/frappe rev-parse HEAD && git -C apps/erpnext rev-parse HEAD && git -C apps/frappe status --porcelain && git -C apps/erpnext status --porcelain"
# 输出：6a329d068416768ec47ccd3326b9cc95a8d7bf99 / 11e0ba0a1c45f217e2e73e885f699102d06da325 / （无）

# P1.3 基线复跑（退出 0，输出 P2P-RUN-OK；本轮新证据四单据 MAT-MR-2026-00010→PUR-ORD-2026-00010→MAT-PRE-2026-00008→ACC-PINV-2026-00006，docstatus=1，PO/PR/PI CNY，PI Unpaid outstanding=500.0；失败用例 403×2、417 MandatoryError、417 UpdateAfterSubmitError 全命中）
bash env/dev/scripts/dev/env.sh p2p-run

# 主数据与用户（退出 0，SEED-OK / P2P-USERS-OK；独立对抗审查期间再次复跑通过）
bash env/dev/scripts/dev/env.sh seed
bash env/dev/scripts/dev/env.sh p2p-users
```

## 独立对抗审查（版本冻结门禁）

- 第一轮：`CHANGES_REQUIRED`——HIGH：`versions.env` 的 Node/MariaDB/Redis 维持 major tag 与 inc1 日志"完整依赖固定属 P1.5 晋升要求"自述不一致。修正：ADR-0002 决策 2/4 显式记录冻结范围仅为 commit pair 并取代该表述，inc1 日志追加标注。
- 第二轮：`CHANGES_REQUIRED`——HIGH：ADR 断言"复核结论 PASS"引用的本日志当时不存在（记录缺口）。修正：本日志补录全部审查过程与真实命令；ADR 证据节改为如实记录。另 LOW：ADR 决策 2 "漂移不会静默进入证据链"措辞夸大（bootstrap 不断言 MariaDB/Redis/Node 点版本），已改为如实缓释表述。
- 第三轮：`CHANGES_REQUIRED`——HIGH：本日志验证节与 ADR 状态/证据节在结论产生前预写 PASS（前置断言）。修正：两处改为中性/候审表述，实际结论产生后补录。
- 第四轮（终轮）：`PASS`——冻结决定成立。审查方独立复跑：容器两仓 HEAD 与 SHA 精确一致、porcelain 空、`banking/yarn.lock` diff 空；`p2p-run` 输出 `P2P-RUN-OK` 退出 0（四单据 docstatus=1、PO/PR/PI CNY、PI Unpaid outstanding=500.0、失败用例 403×2 + 417×2 全命中）；`seed`/`p2p-users` 成功标记；`tabWorkflow` 计数 0；四单据 DocPerm 与上游固定版本 JSON 逐格一致；Global Defaults=CNY、Buying Price List=SYNORA-P1 Buying CNY、over_delivery_receipt_allowance=0.0、role_allowed_to_over_bill 空。构造 6 个"冻结应被拒绝"场景（SHA 幻觉、版本号非源码事实、DocPerm 被改写、脚本改权限、环境非固定 SHA、P2P 与 ADR 不符）全部证伪。

审查未修改仓库任何文件；审查期间仅复跑基线操作（p2p-run/seed/p2p-users 属允许的基线操作）。

## 验证

- 容器断言退出码 0：两上游 HEAD 与 ADR-0002 一致、porcelain 为空（含 `banking/yarn.lock` diff 为空）。
- `p2p-run` 退出 0：`P2P-RUN-OK`，最终状态与 ADR-0002 记录一致。
- `git diff --check`：退出 0。
- 独立 Test（P1.4 地图）与独立 Review（P1.4 地图）：`PASS`；版本冻结独立对抗审查：四轮审查，终轮 `PASS`（见上节）。

## 局限

- 未执行破坏性空卷重建（用户约束：不得重置/清理当前 site/volume）；空卷重建可复跑性以 Inc-1 日志（卷/容器时间戳吻合）与 bootstrap 脚本完整性为证。
- Node/MariaDB/Redis 维持 major tag（非 digest），点版本漂移不会被 bootstrap 捕获；以"固定输入 + 真实 P2P 复核"兜底（ADR-0002 决策 2）。
- README 项目状态仍写 Phase 0（滞后于证据而非超于证据）；README 为 user-owned 托管文件，同步需走 `readme-writer` 且属后续项，本次未改。
- `.harness/unresolved.json` 的 `erp-version-pair` 同步为 RESOLVED 属 harness-update 授权范围，另作一步提交（见 `2026-08-24-phase1-inc5-harness-sync.md`，若已产生）。

## 可重复人工验收

```bash
git status --short
bash env/dev/scripts/dev/env.sh seed
bash env/dev/scripts/dev/env.sh p2p-users
bash env/dev/scripts/dev/env.sh p2p-run
bash env/dev/scripts/dev/env.sh bash \
  "cd /home/frappe/bench && git -C apps/frappe rev-parse HEAD && git -C apps/erpnext rev-parse HEAD && git -C apps/frappe status --porcelain && git -C apps/erpnext status --porcelain"
```

期望：前三命令输出 `SEED-OK` / `P2P-USERS-OK` / `P2P-RUN-OK`；上游两 SHA 与 ADR-0002 一致、两仓无输出；打开 ADR-0002 与权限/Workflow 基线文档可读且互相一致。
