# Phase 1 · Inc-2（P1.2）确定性主数据：seed/cleanup

日期：2026-08-24 ｜ 状态：已完成并验证

## 改了什么

- 新增 `env/dev/seed/seed.py`：幂等创建确定性测试主数据。环境基础（标准名、cleanup 保留）：UOM `Unit`、Item Group root `All Item Groups`、Warehouse Type `Transit`；命名空间数据（前缀 `SYNORA-P1`，公司名为锚点）：Company `SYNORA-P1 Test Company`（缩写 SP1）、Item Group `SYNORA-P1 Items`、Supplier `SYNORA-P1-Supplier-1`、Item `SYNORA-P1-Item-1001`、Warehouse `SYNORA-P1 Stores - SP1`。
- 新增 `env/dev/seed/cleanup.py`：按依赖安全序（Item → 子 Item Group → Supplier → 自建 Warehouse → Company）删除，仅作用命名空间；删除后按命名空间口径断言残留为零（含 Contact），非零即抛异常拒绝宣布完成。
- `env/dev/scripts/dev/env.sh` 新增 `seed` / `cleanup` 子命令：docker cp 拷入容器 `/tmp/synora_seed/` 后经 `bench console` 管道执行（`bench execute` 仅接受已安装 app 的模块，外部脚本不可用；console 管道需 `exec(code, globals())` 注入命名空间）。

## 设计依据（上游取证，非猜测）

来源：容器内候选 SHA（erpnext `11e0ba0a1c45f217e2e73e885f699102d06da325`、frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`）DocType schema 与 site 运行实测：

- 命名确定性：Company autoname=`field:company_name`；Item=`field:item_code`；UOM/Item Group=字段同名；Warehouse=autoname 计算 `warehouse_name + " - " + 公司缩写`；Supplier 实测 `supp_master_name='Supplier Name'` → name=supplier_name（若该全局设置改变，Supplier 命名将不确定——见局限）。
- 空 site（未跑 setup wizard）实测：UOM=0、Item Group=0、无 Warehouse Type `Transit`（Company.on_update→create_default_warehouses 链接校验需要）；Currency `CNY`、Country `China` 已随安装存在。故 seed 需先建上述标准基础数据（等同 setup wizard 产物，非命名空间，保留）。
- 级联清理（上游行为，经标准 `frappe.delete_doc` 触发）：Company.on_trash 在无 GL Entry / Stock Ledger Entry 时级联删该公司 Account、Cost Center、全部 Warehouse；Supplier.on_trash 自动删关联 Contact/Address；不传 mobile_no/email_id 时 Supplier 不会自动建 Contact。
- 安全边界遵守：仅标准 DocType API（`frappe.get_doc().insert()` / `frappe.delete_doc`），无 SQL、无 `frappe.db.delete`、无 `ignore_permissions`；每步成功后显式 commit，失败步骤不 commit。

## 实际验证（2026-08-24 实跑，容器 synora_phase1_dev-bench-1 / site dev.localhost）

1. `bash env/dev/scripts/dev/env.sh seed`（第 1 次）：7 类对象 created；namespace_counts = {Company:1, Supplier:1, Item:1, Item Group(ns):1, Warehouse(ns):1}；SEED-OK。
2. `env.sh seed`（第 2 次）：全部 exists、跳过；计数与第 1 次完全一致（幂等，无重复）。
3. `env.sh cleanup`（第 1 次）：5 项 deleted；leftover_counts 全 0（含 Contact(ns):0）。
4. `env.sh cleanup`（第 2 次）：全部 absent；CLEANUP-OK（幂等）。
5. 级联彻底性抽查：cleanup 后 `SP1` 仓库列表空、该公司 Account=0、Cost Center=0、全 site 无任何 Company/Warehouse/Account/Cost Center → 缩写 SP1 可无冲突复用，重新 seed 成功。
6. 恢复：再次 `env.sh seed` 成功（SEED-OK），命名空间数据在位，供 Inc-3 使用。
7. 上游未改动：frappe/erpnext `git status --porcelain` 均 0 dirty，HEAD 与候选 SHA 一致。

未运行/未覆盖：未在多个公司并存、非 Administrator 会话下测试；未测试 `supp_master_name` 被改为 Naming Series 后的行为。

## 局限与边界

- P1.2 范围解读：「需求和采购主数据」= 服务于 MR/PO 流程的主数据（Item/Warehouse/Supplier 本身）；MR/PO/Receipt/Invoice 单据属 Inc-3 人工流程，本步不创建。
- Supplier 确定性命名依赖全局设置 `supp_master_name='Supplier Name'`（当前实测值）；若环境改变需先恢复该设置再 seed。
- Warehouse Type `Transit`、UOM `Unit`、Item Group root 为环境基础，cleanup 有意保留（重复 seed 幂等）；销毁整个 site 仍走 env.sh reset（最后手段，需备份外导确认）。

## 可重复的手工验收

```bash
bash env/dev/scripts/dev/env.sh seed    # 期望: created→SEED-OK, 计数全 1
bash env/dev/scripts/dev/env.sh seed    # 期望: exists, 计数不变
bash env/dev/scripts/dev/env.sh cleanup # 期望: deleted, leftover 全 0, CLEANUP-OK
bash env/dev/scripts/dev/env.sh cleanup # 期望: absent, CLEANUP-OK
bash env/dev/scripts/dev/env.sh seed    # 恢复数据
```

## 审查记录

- ponytail full 自审：两文件均为计划点名交付物；无额外抽象（单一 `_get_or_insert`/`_delete` helper）；常量在两文件间显式重复并注释同步（跨文件 import 在 console exec 模式下不可靠，属有意取舍）。
- 独立对抗审查（2026-08-24，独立 sub-agent）：A 禁用项合规（Grep 实测无 `frappe.db.delete`/SQL/`ignore_permissions` 调用）、B 幂等证据自洽可复现、C 清理边界不可能触碰命名空间外数据（残留断言 fail closed）、D 范围解读与 PLAN L211/Inc-3 无冲突——均通过。裁决 **CHANGES_REQUIRED**，3 项必改已全部修复：① 本审查记录回填；② cleanup.py "创建逆序"表述改为"依赖安全序"（注释与本文档同步修正，删除顺序本身已验证依赖安全，未改动）；③ env.sh 头部用法注释补 `seed|cleanup`。建议级（不阻塞）：残留断言可加 Address 口径，留待后续需要时再加。
- 修复后复核：②③ 仅注释/文档改动，不影响已验证的运行行为；①随本提交闭环。
