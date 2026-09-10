# Phase 10 P2P 真实 ERP 验收记录

- 状态：`PASS`
- 记录时间：`2026-09-10T12:35:25Z`
- 代码绑定：父提交 `cebdff9` + 源文件 SHA-256（提交前记录）
- 固定 Frappe：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`
- 固定 ERPNext：`11e0ba0a1c45f217e2e73e885f699102d06da325`

真实开发 ERP 批次完成 PO 提交、两次部分收货、两张 Purchase Invoice、三次 Payment Entry（其中一张发票先部分付款再结清），共 15 个独立审批 Action。最终 Run 为 `SUCCEEDED`，PO `per_received=100.0`、`per_billed=100.0`，两张发票均为 `Paid`。

故障注入覆盖提交后响应丢失和 native submit 已提交但 Receipt 未落地。前者重放只读同一 Receipt、没有第二张 PO；后者冻结 Run 为 `RECONCILIATION_REQUIRED`，恢复入口只读重查并保留人工接管。

Playwright 以 System Manager 登录 `/desk/runs`，只打开详情和截图，没有审批、执行、恢复或取消点击；截图同时保留成功链路和对账阻塞状态。

机器可读数据：`output/phase10/phase10-p2p-e2e-recorded-v1.json`。
