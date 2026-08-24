# ADR-0002：Phase 1 固定 Frappe/ERPNext commit pair 正式基线

- 状态：已批准（正式基线）
- 日期：2026-08-24
- 关联：`docs/PLAN.md` P1.5；`docs/decisions/ADR-0001-docker-bench-environment.md`（环境拓扑不变量）；`docs/source-maps/phase1-p2p-source-map.md`（P1.4 取证）；`.harness/unresolved.json` 未决项 `erp-version-pair`；`docs/development-log/2026-08-24-phase1-inc1-empty-rebuild.md`（取代其中"完整依赖固定属 P1.5 晋升要求"的表述）

## 背景（Context）

Phase 1 需要把人工 P2P 基线通过后验证过的 Frappe/ERPNext 版本正式固定，作为后续全部阶段（权限取证、源码引用、测试、契约、集成）的证据与测试基线。P1.1（空卷重建候选环境）、P1.2（确定性主数据）、P1.3（人工 MR→PO→Receipt→Invoice 基线 `P2P-RUN-OK`）、P1.4（源码地图，独立 Test/Review 均 `PASS`）均已通过。

候选 SHA 自 Inc-1 起由 `env/dev/versions.env` 的 `FDP_REV_FRAPPE` / `FDP_REV_ERP_NEXT` 承载，构建后逐项断言；截至本 ADR，容器内两个 HEAD 与候选 SHA 精确一致、两上游工作区 clean（含 `banking/yarn.lock` 恢复验证）。

## 决策（Decision）

1. **固定 commit pair 为 Phase 1 正式基线**：
   - Frappe：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`（版本 16.31.0）
   - ERPNext：`11e0ba0a1c45f217e2e73e885f699102d06da325`（版本 16.32.3）
   - `versions.env` 中 `FDP_REV_*` 即正式值；此后再变更须新决议并记录新 ADR。
2. **本 ADR 只冻结 commit pair**：Node 24 / MariaDB 11.4 / Redis 7 维持 major tag（非 digest 固定）。风险与缓释：
   - 这些依赖仅用于本项目一次性 dev 环境；bootstrap 的硬断言覆盖两个上游 SHA 与构建产物（`env.sh:81-91`），但**不**对 MariaDB/Redis/Node 点版本做断言，major tag 漂移不会被 bootstrap 捕获；
   - 漂移由"固定输入 + 真实 P2P 复核"兜底：P1.2/P1.3 证据与第 6 节验证命令可复跑，任何漂移导致的业务行为变化会在复跑中暴露（2026-08-24 各轮独立对抗审查均复跑 `P2P-RUN-OK` 通过，终轮复跑见 inc5 日志）；
   - 如需生产级复现或公开交付，再单独决议 digest 级固定（新 ADR），不阻塞 Phase 1 出口。
3. **SHA 来源与性质（如实记录）**：两个 SHA 是 `version-16` 移动分支 tip 处的官方 release bump commit（Frappe `chore(release): Bumped to Version 16.31.0`、ERPNext `Bumped to Version 16.32.3`），解析日期 2026-08-24，**不是独立 release tag**。冻结后 SHA 不可变；上游分支继续移动不影响已冻结基线，如需跟随上游修复须重新决议 pair。
4. **取代声明**：`2026-08-24-phase1-inc1-empty-rebuild.md` 中"完整依赖固定属 Inc-5（P1.5）晋升要求"的表述被本 ADR 取代——P1.5 的固定范围仅为 commit pair，其余运行依赖维持候选态（major tag）并适用上述风险缓释。

## 备选方案（Alternatives）

1. **固定到 release tag 而非 SHA**：version-16 分支没有与 16.31.0/16.32.3 对应的独立 tag 约束（SHA 即 release commit），且 tag 也可能被重新指向；SHA 是唯一不可变引用。否决。
2. **同时 digest 固定 Node/MariaDB/Redis**：增加本轮验证范围且需对现运行环境做破坏性重建验证，超出 P1.5 "固定 commit pair" 的 PLAN 范围；作为风险缓释记录（决策 2），不阻塞。
3. **继续候选态不冻结**：Phase 2 起全部证据、测试与契约将无稳定上游引用，不可接受。否决。

## 后果（Consequences）

- 正向：Phase 2+ 获得不可变的 Frappe/ERPNext 证据基线；源码行号引用以固定 SHA 为准；`erp-version-pair` 未决项由此解决。
- 代价：跟随上游修复需重新决议（新 ADR + 独立对抗审查）；Node/MariaDB/Redis 存在 major tag 漂移可能（已记录缓释）。
- 不变项：`approval-workflow-mapping`、`third-party-licenses`、`runtime-user-authorization` 等未决项不受本 ADR 影响；本 ADR 不改变任何产品、权限或审批策略。

## 证据（Evidence）

- 2026-08-24 当日两次 `p2p-run` 均输出 `P2P-RUN-OK`（Inc-3 一次 + P1.5 复核一次），最终四单据 docstatus=1、PO/PR/PI 币种 CNY、PI `Unpaid` outstanding=500.0，四个失败用例（403×2、417 MandatoryError、417 UpdateAfterSubmitError）全部命中。
- 容器内断言：`git -C apps/frappe rev-parse HEAD` = `6a329d0…`、`git -C apps/erpnext rev-parse HEAD` = `11e0ba0…`；两仓 `status --porcelain` 为空；`banking/yarn.lock` diff 为空。
- 版本冻结前独立对抗审查：四轮审查（前三轮 `CHANGES_REQUIRED`，分别修正 major tag 与晋升自述不一致、审查记录文件缺失、审查结论前置断言；终轮 `PASS`），全部审查结论与真实命令证据见 `docs/development-log/2026-08-24-phase1-inc5-frozen-baseline.md`。
- 空卷重建证据：`2026-08-24-phase1-inc1-empty-rebuild.md`（卷/容器时间戳与日志吻合；本次未做破坏性重建，限制见开发日志）。

## 可重复验证命令

见 `docs/erp-baselines/phase1-permission-workflow-baseline.md` 第 6 节（本 ADR 不重复）。

## 取代（Supersession）

无。ADR-0001 的环境拓扑决策保持不变；本 ADR 只把候选 SHA 晋升为正式基线，并取代 inc1 日志中关于"完整依赖固定属 P1.5"的表述（决策 4）。
