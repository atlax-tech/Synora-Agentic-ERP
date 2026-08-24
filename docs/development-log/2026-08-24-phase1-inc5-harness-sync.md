# Phase 1 · Inc-5b（P1.5）Harness 同步

日期：2026-08-24 ｜ 状态：已完成并验证

## 结果

按 `docs/PLAN.md` §5 与 `harness-update` Skill，在用户明确批准（H1–H6）后，将 P1.5 版本冻结产生的权威事实同步进 Harness 托管文档与清单：

- `.harness/unresolved.json`：`erp-version-pair` 由 `UNRESOLVED` 置为 `RESOLVED`，`resolution` 指向 `docs/decisions/ADR-0002-frozen-baseline-pair.md`。
- `docs/PRD.md` §12 待确认问题第 1 项（Frappe/ERPNext baseline commit pair）标记已解决并给出固定 SHA。
- `docs/ARCHITECTURE.md` Open Architecture Decisions 第 1 项（Complete Frappe/ERPNext commit pair）标记 `RESOLVED` 并引用 ADR-0002。
- `docs/DEVELOPMENT.md` 新增"Phase 1 environment commands (verified)"小节（seed/p2p-users/p2p-run/bootstrap/上游断言）；产品命令仍标 `UNRESOLVED`。
- `.harness/manifest.json`：刷新 PRD/ARCHITECTURE/DEVELOPMENT/unresolved.json/source-index.json 五个托管文件 hash，并将 ADR-0002、P1.4 源码地图、权限/Workflow 基线登记为 managed。
- `.harness/source-index.json`：刷新 PRD/ARCHITECTURE 的 sha256。

## 为什么现在做

P1.5 固定基线产生新权威事实（`erp-version-pair` 解决、已验证环境命令、新权威文档），PLAN §5 要求 Harness/权威文档同步必须走 `harness-update`：只读 drift 检测 → 文件级 proposal → 用户明确批准 → 写入。用户已批准 H1–H6。

## 实际验证（全部真实命令）

```bash
python3 .agents/skills/harness-update/scripts/validate_manifest.py .   # valid: True, errors: []
python3 .agents/skills/harness-update/scripts/validate_harness_structure.py .  # valid: True, broken_refs: 0
python3 .agents/skills/harness-update/scripts/detect_drift.py .        # has_drift: False, drift: []
```

应用前 `detect_drift` 为 `False`（无既有漂移）；应用后 source-index 曾出现 2 项 `source-modified` drift（PRD/ARCHITECTURE hash 过期），已通过同步 source-index 与 manifest 消除，最终三项校验全部通过、drift 归零。

## 局限

- 本步只同步已批准项；README 项目状态（Phase 0 滞后）为 user-owned 文件，同步需另行走 `readme-writer` 与用户确认，不在本步范围。
- `approval-workflow-mapping` 等其余未决项保持不变。

## 可重复人工验收

```bash
python3 .agents/skills/harness-update/scripts/validate_manifest.py .        # valid: True
python3 .agents/skills/harness-update/scripts/validate_harness_structure.py .  # valid: True, broken_refs: 0
python3 .agents/skills/harness-update/scripts/detect_drift.py .             # has_drift: False
python3 -c "import json; print([i['status'] for i in json.load(open('.harness/unresolved.json'))['items'] if i['id']=='erp-version-pair'])"  # ['RESOLVED']
```

期望：三项校验通过、drift 为零、`erp-version-pair` 为 `RESOLVED`。
