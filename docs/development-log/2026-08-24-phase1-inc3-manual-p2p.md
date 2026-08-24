# Phase 1 · Inc-3（P1.3）命名用户人工 P2P 基线

日期：2026-08-24 ｜ 状态：已完成并验证

## 结果

在未修改 Frappe/ERPNext 上游、未直写数据库、未使用 Administrator 执行业务单据的前提下，命名测试用户经官方 HTTP API 跑通：

`Material Request → Purchase Order → Purchase Receipt → Purchase Invoice`

最终 Purchase Invoice 已提交，币种 CNY，状态 `Unpaid`，未付金额 500.0；这就是 Phase 1 要观察的 Payment 状态，本阶段没有创建 Payment Entry。

## 改了什么

- 新增 `env/dev/seed/p2p_users.py`：以 Administrator 仅初始化五个命名测试用户，按固定候选 DocPerm 分配精确显式角色；存在任何额外显式角色时 fail closed；密码只从 gitignored `.env` 注入并由标准 User DocType 哈希保存。
- 新增 `env/dev/p2p/p2p_run.py`：使用命名用户会话和官方 REST/method/savedocs 端点执行转换、创建、提交、最终读回与四个失败用例。任何非预期 HTTP、异常类型、docstatus 或币种均非零退出。
- `env/dev/scripts/dev/env.sh` 新增 `p2p-users` / `p2p-run`；用户初始化只有在 commit 后出现独立整行 `P2P-USERS-OK` 才成功。
- `.env.example` 仅新增空的 `SYNORA_P2P_USER_PWD` 配置项；真实值仍只在 gitignored `.env`，未进入 argv、Git 或 JSON 证据。
- 每次运行在容器 `/tmp/p2p-evidence.json` 写入步骤、用户、HTTP 结果、单据名和最终 ERP 状态；失败也写证据，不含密码、cookie 或 CSRF token。
- 所有 HTTP 请求固定 30 秒 timeout；非 JSON/非对象响应立即失败。中途失败会登记已知单据和“只读回查、禁止盲重跑”的恢复提示。

## 固定输入与角色

- 交易日期：2026-08-24；Item=`SYNORA-P1-Item-1001`；Supplier=`SYNORA-P1-Supplier-1`；Warehouse=`SYNORA-P1 Stores - SP1`；数量=5；单价=100 CNY。
- Buyer：`synora-p1-buyer@dev.localhost`，Purchase User。
- Approver：`synora-p1-approver@dev.localhost`，独立 Purchase User；与 Buyer 分离，仅提交 PO。Purchase Manager 单独提交实测因关联对象权限返回 403，故未采用。
- Receiver：`synora-p1-receiver@dev.localhost`，Stock User + Purchase User。实测 Stock User 单独创建 PR 时无法读取 Account；Purchase User 提供上游 DocPerm 所需 Account read。
- Accountant：`synora-p1-accountant@dev.localhost`，Accounts User。
- Viewer：`synora-p1-viewer@dev.localhost`，无上述业务角色，仅用于权限拒绝。

## 最终成功证据

命令：`bash env/dev/scripts/dev/env.sh p2p-run`，退出 0，输出 `P2P-RUN-OK`。

| 步骤 | 用户 | 单据 | 最终状态 |
| --- | --- | --- | --- |
| MR 创建/提交 | Buyer | `MAT-MR-2026-00009` | docstatus=1, Received |
| MR→PO 创建/提交 | Buyer 创建、Approver 提交 | `PUR-ORD-2026-00009` | docstatus=1, Completed, CNY |
| PO→PR 创建/提交 | Receiver | `MAT-PRE-2026-00007` | docstatus=1, Completed, CNY |
| PR→PI 创建/提交 | Accountant | `ACC-PINV-2026-00005` | docstatus=1, Unpaid, CNY, outstanding=500.0 |

失败路径同轮通过：

- Viewer 创建 MR：HTTP 403 `PermissionError`。
- Accountant 读取 PO：HTTP 403 `PermissionError`。
- Buyer 创建无 items 的 MR：HTTP 417 `MandatoryError`。
- Buyer 修改已提交 MR 的 transaction_date（2026-08-24 → 2026-08-23）：HTTP 417 `UpdateAfterSubmitError`。

## 原始卡点与解决链

1. 空 site 缺 Fiscal Year，MR 被 `FiscalYearError` 拒绝：seed 补 `SYNORA-P1 FY 2026`。
2. 无 Price List，采购链缺少 CNY price-list 基线：seed 补 CNY Buying Price List，并由上游钩子写 Buying Settings。
3. 仅补 Price List 后，PO/PR 仍继承空 site 的 INR global currency，PI 因应付科目 CNY 与单据 INR 不一致被拒绝：按 Setup Wizard 同源路径保存 `Global Defaults.default_currency=CNY`，同时同步运行默认。
4. Receiver 仅 Stock User 时 PR 创建因 Account read 权限拒绝：依据固定 DocPerm 补 Purchase User；未绕过权限。
5. F4 最初用 2030 日期先撞 Fiscal Year，用 2026-08-25 又先撞 Required By 日期规则；改为同会计年度且早于 Required By 的 2026-08-23，最终精确命中 UpdateAfterSubmitError。

诊断期间曾产生多组已提交 MR/PO/PR/PI，它们是本 disposable site 的失败/恢复证据。未取消、删除或 reset；cleanup 会按 Link 校验 fail closed。

第一轮独立 Test 返回 `FAIL`，发现 Buyer 同时提交 PO、无 HTTP timeout、额外强角色未被拒绝、最终业务状态断言不足、非 JSON 与 partial failure 证据不足。现已增加独立 Approver、精确角色、timeout/响应校验、CREATED 恢复证据以及 Received/Completed/Unpaid/outstanding/上下游链接断言。

修复后第二轮 Test 返回 `PASS`；应用 Ponytail 的两项删减后，第三轮完整 Test 再次返回 `PASS`。第一轮独立 Review 返回 `CHANGES_REQUIRED`：错误 JSON 为 `exception:null` 且只有 message 时，`outcome()` 可能掩盖原错误；同时要求闭环日志状态。现已按 `exc_type → exception → message → ?` 安全选值并统一转字符串，最终结论以修正后的复核为准。

修正后第四轮独立 Test：`PASS`；最终独立 Review：`PASS`。Ponytail Review 的最终两项删减均已应用。

## Phase 1 粒度与效率复盘

Phase 1 覆盖环境、确定性数据、真实 P2P、源码地图和版本冻结，作为阶段整体确实较大；但 PLAN 已拆为 P1.1–P1.5，不能把“四小时未结束”简单归因于规划过大。实际低效主要来自执行纪律：

- Inc-3 同时展开用户、运行器、FY、Price List、币种和失败用例，未先把 Setup Wizard 等价环境清单闭合。
- 原运行器在错误响应上直接索引 `body["data"]`，用 `KeyError` 掩盖上游错误，且没有断言最终状态/失败类型。
- `bench console` 异常仍返回 0，早期 wrapper 没有成功 marker 门禁。
- 多次试跑前没有先收敛上游默认来源，造成重复交易和 token/时间浪费。

因此不修改当前 PLAN；后续继续按 P1.4、P1.5 单结果、失败即停、源码先行、小步提交。

## 实际检查与局限

- `python3 -m py_compile env/dev/seed/p2p_users.py env/dev/p2p/p2p_run.py`：退出 0。
- `bash -n env/dev/scripts/dev/env.sh`、`git diff --check`：退出 0。
- 最终 JSON 证据只含上述步骤/身份/状态/错误类型；无密码、cookie、CSRF token。
- 尚未完成 P1.4 源码地图或 P1.5 固定基线；Inc-3 通过不代表 Phase 1 已收尾。
- 本阶段只观察 Invoice 的 Payment 状态，不创建 Payment Entry，符合 P1.3 边界。

## 可重复人工验收

```bash
bash env/dev/scripts/dev/env.sh seed
bash env/dev/scripts/dev/env.sh p2p-users
bash env/dev/scripts/dev/env.sh p2p-run
```

期望三个命令分别输出 `SEED-OK`、`P2P-USERS-OK`、`P2P-RUN-OK`；最后四类单据 docstatus=1，PO/PR/PI 为 CNY，PI 为 Unpaid，四个失败用例异常类型与上文一致。
