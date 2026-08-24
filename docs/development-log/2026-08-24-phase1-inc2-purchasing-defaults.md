# Phase 1 · Inc-2 纠偏：补齐采购日期与币种基础数据

日期：2026-08-24 ｜ 状态：已完成并验证

## 改了什么

- 在确定性 seed 中新增 `SYNORA-P1 FY 2026`，覆盖本次固定交易日期所在会计年度。
- 新增启用的 CNY Buying Price List `SYNORA-P1 Buying CNY`；首个采购价目表由 ERPNext 标准 `PriceList.on_update` 自动写入 Buying Settings 默认值。
- cleanup 同步按命名空间删除上述对象，并把两者纳入残留计数。
- seed/cleanup 均改为整个操作成功后单次提交，任一步失败显式回滚，避免已有交易触发 Link 校验时留下半清理状态。
- seed 对同名既有对象的关键字段和 Buying Settings 默认价目表做漂移检查；不一致时拒绝输出 `SEED-OK`，且不擅自覆盖现场配置。
- `env.sh seed/cleanup` 不再相信 `bench console` 的退出码；只有捕获到对应成功标记才返回成功，避免 IPython 吞掉脚本异常后误报退出 0。
- `bench console` 创建 Fiscal Year 前显式设置当前会话语言，规避固定 Frappe 候选版本在无语言上下文触发 Notification 时的上游异常；未修改上游源码。

## 为什么改

Inc-3 首次真实 P2P 试跑暴露了空 site 未执行 Setup Wizard 的两项缺口：交易日期不属于任何活跃 Fiscal Year；site 没有 Price List 时，生成的 PO/PR 币种落为 INR，而测试公司应付科目币种是 CNY，导致 PI 创建被 ERPNext 正确拒绝。固定候选源码证明，正确根因修复是补齐 Buying Price List 并使用其标准默认钩子，不是在交易运行器中绕过上游默认与校验。

## 实际验证

- `python3 -m py_compile env/dev/seed/seed.py env/dev/seed/cleanup.py`：退出 0。
- `git diff --check`：退出 0。
- `bash env/dev/scripts/dev/env.sh seed`：Price List 首次创建成功，Fiscal Year 已存在，命名空间 7 类对象均为 1，输出 `SEED-OK`。
- 再次运行同一 seed：全部对象为 `exists`，计数不变，输出 `SEED-OK`。
- 运行时只读检查：Buying Settings 默认价目表为 `SYNORA-P1 Buying CNY`；该 Price List 为 CNY、Buying=1、Enabled=1。
- 第一轮独立 Test 发现 cleanup 逐对象 commit 会在 Link 校验失败时留下半清理状态，判定 `FAIL`；已改为单事务提交/异常回滚，并增加 seed 漂移校验。
- 第二轮独立 Test 发现成功标记早于 commit，判定 `FAIL`；已把标记移到 commit 成功之后并改为整行匹配。第三轮完整独立 Test 返回 `PASS`。
- 故障注入：在当前已有已提交 PO/PR 的 site 执行 cleanup，Item 的标准 Link 校验拒绝删除；wrapper 正确返回退出 1。随后只读确认 Price List、Buying Settings 默认值和 Item 均仍存在，证明事务已回滚，没有留下半清理状态。
- 回滚后再次执行 seed：退出 0，所有对象配置匹配，输出 `SEED-OK`。

## 局限

- 当前 site 已有 Inc-3 失败诊断期间产生的已提交 MR/PO/PR；本次只执行了预期失败并回滚的 cleanup 故障注入，没有删除交易数据，也没有执行 reset。
- 这次只证明缺失主数据已按上游标准补齐；完整 MR → PO → Receipt → Invoice 和失败路径属于 Inc-3，尚未据此宣布通过。

## 可重复人工验收

```bash
bash env/dev/scripts/dev/env.sh seed
bash env/dev/scripts/dev/env.sh seed
bash env/dev/scripts/dev/env.sh cleanup  # 当前已有交易时预期退出 1
```

两次 seed 均应退出 0 并输出独立整行 `SEED-OK`，第二次不得创建重复 Fiscal Year 或 Price List。当前已有交易时 cleanup 应因标准 Link 校验退出 1，且不得输出独立整行 `CLEANUP-OK`；失败后再次运行 seed 应仍全部为 `exists` 并退出 0，证明 Price List、默认配置和 Item 未被半清理。在 Desk 的 Buying Settings 中应看到默认价目表 `SYNORA-P1 Buying CNY`，币种 CNY。

## 审查记录

- Ponytail Review：`Lean already. Ship.`，未发现可删除复杂度。
- 第一轮独立 Review：`CHANGES_REQUIRED`，实现无正确性或安全问题；要求修正日志状态并补全 cleanup 回滚/marker 人工验收。本段及上方验收步骤已据此修正，最终结论以修正后的复核为准。
- 修正后第四轮独立 Test：`PASS`；最终独立 Review：`PASS`。
