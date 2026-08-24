# Phase 1 · Inc-2 纠偏：空 site 全局币种与公司对齐

日期：2026-08-24 ｜ 状态：已完成并验证

## 改了什么

- 确定性 seed 按 ERPNext Setup Wizard 的官方路径更新并保存标准 `Global Defaults` 单据，把专用 disposable site 的全局币种设为 CNY；单据 `on_update` 同步运行默认值。
- 写入后在同一事务内同时读回 `Global Defaults.default_currency` 和 `frappe.defaults.get_global_default("currency")`；任一不是 CNY 时 fail closed，不输出 `SEED-OK`。
- global currency 属 Setup Wizard 应建立的环境基础，cleanup 有意保留；命名空间业务对象仍按原边界清理。

## 为什么改

Inc-3 修复 CNY Buying Price List 后再次真实试跑，MR、PO、PR 均成功提交，但 PO/PR 的 `buying_price_list` 与 `price_list_currency` 已是 CNY 时，单据 `currency` 仍是 INR。只读运行证据显示 site global default currency=INR；固定 ERPNext 的 MR→PO mapper 在这个未执行 Setup Wizard 的空 site 上继承该默认，最终 PI 因 CNY 应付科目与 INR 单据币种不一致被标准校验拒绝。

这不是 PI 校验错误，也不应在运行器里硬编码绕过。固定 ERPNext Setup Wizard 更新并保存 `Global Defaults`，该 DocType 的 `on_update` 再同步运行默认；因此把空 site 的权威设置单据补齐为测试公司币种是最小根因修复。

## 验证计划

- `py_compile`、`git diff --check`、禁用 API/秘密扫描。
- seed 首次把 global currency 从 INR 改为 CNY，再次 seed 保持 CNY 且幂等。
- 重新创建新的 MR→PO→PR，读回 PO/PR `currency=CNY`；不修改或删除此前失败证据单据。
- 上游 Frappe/ERPNext HEAD 与工作区保持固定候选且 clean。

## 实际运行验证

- 第一次 seed：输出 `global currency 'INR' -> CNY`，退出 0；第二次 seed 不再改变币种，退出 0，均输出 `SEED-OK`。
- 修复后新建并提交：MR `MAT-MR-2026-00004`、PO `PUR-ORD-2026-00004`、PR `MAT-PRE-2026-00003`、PI `ACC-PINV-2026-00001`。
- 最终读回：PO/PR/PI `currency=CNY` 且 docstatus=1；PI 状态 `Unpaid`、outstanding=500.0。证明此前 CNY/INR PI 拒绝的根因已消除。
- 同轮前三个失败用例分别得到 403 PermissionError、403 PermissionError、417 MandatoryError；第四个非法更新先被 2030 日期的会计年度校验以 417 ValidationError 拒绝，因此 Inc-3 尚未完成，需改用 2026 会计年度内日期继续验证 Update-after-submit。
- 第一轮独立 Test 判定 `FAIL`：实现只改运行 DefaultValue，`Global Defaults.default_currency` 仍是 INR，形成双源漂移。现已改为 Setup Wizard 同源的 DocType `save()` 并同时断言两个来源。
- 修复后第二轮独立 Test 返回 `PASS`：双源均为 CNY、两次 seed 幂等、完整 P2P 最终状态保持通过、cleanup 无 diff、固定上游 clean，静态/秘密/API/Harness 检查均通过。

## 局限与人工验收

- 该修复只补环境基线，不据此宣布 Inc-3 完成；Invoice、Payment 状态和失败路径仍须独立通过。
- cleanup 不恢复安装时的 INR，因为 CNY 是本专用 site 的确定性 Setup Wizard 等价基础；销毁 site 仍只能走带确认的 reset。

```bash
bash env/dev/scripts/dev/env.sh seed
bash env/dev/scripts/dev/env.sh seed
```

两次均应退出 0 并输出 `SEED-OK`，第二次不得再次改变 global currency。

## 审查记录

- Ponytail Review：`Lean already. Ship.`。
- 第一轮独立 Review：`CHANGES_REQUIRED`，实现无正确性或安全问题；要求同步第二轮 Test 结果，并在提交前只暂存 `seed.py` 与本日志，排除全部 Inc-3 工作区改动。本日志已修正，提交边界将在复核通过后以 cached diff 证明。
- 修正后第三轮独立 Test：`PASS`；最终独立 Review：`PASS`。
