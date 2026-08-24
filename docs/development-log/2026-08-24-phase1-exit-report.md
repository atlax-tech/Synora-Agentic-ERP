# Phase 1 阶段出口报告（ERP 基线与业务考古）

日期：2026-08-24 ｜ 状态：Phase 1 完成，已停止，未进入 Phase 2

## 1. 完成的步骤与用户可见结果

| 步骤 | 结果 | 证据 |
| --- | --- | --- |
| P1.1 候选环境 | 未修改 Bench + Frappe/ERPNext v16 候选环境，空卷重建验证 | ADR-0001；`2026-08-24-phase1-inc1-empty-rebuild.md`（提交 `ea3a46f`） |
| P1.2 确定性数据 | 幂等 seed/cleanup：测试公司、Supplier、Item、Warehouse、FY、CNY Buying Price List、Global Defaults | `2026-08-24-phase1-inc2-*.md`（提交 `a040142`、`2269087`、`ec0fbb6`） |
| P1.3 人工 P2P | MR→PO→Receipt→Invoice 基线 `P2P-RUN-OK`；PI Unpaid outstanding=500.0；4 失败用例 | `2026-08-24-phase1-inc3-manual-p2p.md`（提交 `99b4ca2`） |
| P1.4 源码地图 | `docs/source-maps/phase1-p2p-source-map.md`：四 DocType、转换链、权限矩阵、Workflow 观察、14 处官方测试、业务不变量，带 [S]/[R]/[P]/[I]/[U] 标签 | 提交 `da2db1a` |
| P1.5 固定基线 | ADR-0002 冻结 Frappe `6a329d0`（16.31.0）+ ERPNext `11e0ba0`（16.32.3）；权限/Workflow 基线；可重复验证命令；Harness 同步（`erp-version-pair` → RESOLVED） | 提交 `e9aaa12`、`f14659c` |

用户可见结果：Phase 1 之后，后续阶段的全部权限取证、源码引用、测试与契约都以固定 SHA 为不可变基线；`erp-version-pair` 未决项已解决。

## 2. 提交列表与文件边界（本会话新增）

- `da2db1a` docs: add phase 1 p2p source map (P1.4) — `docs/source-maps/phase1-p2p-source-map.md` + `docs/development-log/2026-08-24-phase1-inc4-source-map.md`
- `e9aaa12` docs: freeze phase 1 frappe/erpnext baseline pair (P1.5) — `docs/decisions/ADR-0002-frozen-baseline-pair.md`、`docs/erp-baselines/phase1-permission-workflow-baseline.md`、`docs/development-log/2026-08-24-phase1-inc5-frozen-baseline.md`、inc1 日志取代标注
- `f14659c` docs: sync harness state for phase 1 baseline freeze — `docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/DEVELOPMENT.md`、`.harness/{manifest,source-index,unresolved}.json`、`docs/development-log/2026-08-24-phase1-inc5-harness-sync.md`

文件边界：仅新增/更新文档与 Harness 状态；未修改 Frappe/ERPNext 上游、未新增 Synora 业务代码、未修改 `docs/PLAN.md`、未修改 seed/p2p 业务脚本。

## 3. 实际运行命令、退出码与证据位置

| 命令 | 退出码 | 关键输出 | 证据位置 |
| --- | --- | --- | --- |
| 上游断言（双 HEAD + porcelain） | 0 | 两 SHA 与候选一致、无输出 | inc5 日志；本轮多次复跑 |
| `env.sh seed` / `p2p-users` / `p2p-run` | 0 | `SEED-OK` / `P2P-USERS-OK` / `P2P-RUN-OK` | inc3、inc5 日志；本会话 2026-08-24 复跑（00010/00013/00014 系列单据） |
| `validate_harness_structure.py .` | 0 | valid, broken_refs=0（218 引用） | harness-sync 日志 |
| `validate_manifest.py .` / `detect_drift.py .` | 0 | valid / has_drift=False | harness-sync 日志 |
| `py_compile`（4 个 env 脚本）、`bash -n env.sh`、`git diff --check` | 0 | 通过 | 本轮 |
| `score_harness_health.py .` | 0 | 79/100（grade C，语义维度受 host 证据上限） | 本轮 |

## 4. 独立 Test / Review / 对抗审查结论

- P1.4 源码地图：独立 Test 两轮（首轮 `FAIL` 修正 2 处行号后 `PASS`）；独立 Review `PASS`。
- P1.5 版本冻结：独立对抗审查四轮（前三轮 `CHANGES_REQUIRED` 分别修正 major tag 与晋升自述不一致、审查记录文件缺失、审查结论前置断言；终轮 `PASS`）。

## 5. ERP 上游保持干净

两仓 `git status --porcelain` 全程为空（含 `banking/yarn.lock` diff 为空）；未以 Administrator 执行业务单据；无 Synora 业务写入代码（无 app 包、无 `services/`）；未直写数据库。

## 6. 未运行检查、限制、未决项与被拒绝技术

**未运行/限制：**
- 未执行破坏性空卷重建（用户约束：不得重置/清理当前 site/volume）；可复跑性以 Inc-1 空卷重建证据（卷时间戳吻合）+ bootstrap 脚本完整性佐证。
- Node/MariaDB/Redis 维持 major tag（非 digest 固定），bootstrap 不断言其点版本；以"固定输入 + 真实 P2P 复核"兜底（ADR-0002 决策 2）。
- 取消/退货路径未真实运行，行为以官方测试（如 `test_purchase_receipt.py:6417`）为证据源。
- README 项目状态仍写 Phase 0（滞后于证据）；README 为 user-owned 文件，同步需走 `readme-writer` 并另行确认——后续项。

**保持未决（按 PLAN §7 路由）：** `approval-workflow-mapping`（Phase 1 已提证，Phase 4 启用写入前用户决定）、`runtime-user-authorization`（Phase 2）、`product-commands`（Phase 2）、`frontend-design-baseline`（Phase 3）、`model-selection`、`workflow-engine-spike`、`third-party-licenses`（Phase 8 前）等。

**已解决：** `erp-version-pair`（ADR-0002）。

**被拒绝的技术：** 固定到 release tag（SHA 不可变更可靠）；同步 digest 固定 Node/MariaDB/Redis（超出 P1.5 范围，作为缓释记录）；继续候选态不冻结（后续阶段无稳定上游引用）。

## 7. 可重复人工验收步骤

```bash
git status --short                                    # 期望：无输出
bash env/dev/scripts/dev/env.sh seed                  # SEED-OK
bash env/dev/scripts/dev/env.sh p2p-users             # P2P-USERS-OK
bash env/dev/scripts/dev/env.sh p2p-run               # P2P-RUN-OK，退出 0
bash env/dev/scripts/dev/env.sh bash \
  "cd /home/frappe/bench && git -C apps/frappe rev-parse HEAD && git -C apps/erpnext rev-parse HEAD && git -C apps/frappe status --porcelain && git -C apps/erpnext status --porcelain"
# 期望：6a329d068416768ec47ccd3326b9cc95a8d7bf99 / 11e0ba0a1c45f217e2e73e885f699102d06da325 / 无输出
python3 .agents/skills/harness-update/scripts/validate_harness_structure.py .   # valid: True
python3 .agents/skills/harness-update/scripts/detect_drift.py .                 # has_drift: False
```

## 8. 下一阶段编号与为什么尚未开始

下一阶段为 **Phase 2（Typed 只读 ERP Gateway）**。按 `docs/PLAN.md` §3"阶段出口通过后提交阶段报告并停止，不得自动进入 Phase X+1"，本报告提交后停止；Phase 2 需用户另行下达"开始完成阶段 2"指令。
