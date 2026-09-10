# Phase 10 R10.4 浏览器真实验收 v2

- Run: `50693541-627c-4593-a4aa-1cf3c45b98a8`
- Initiator: `synora-p1-payment-operator@dev.localhost`
- Result: `SUCCEEDED / REVOKED`
- Goal: `PUR-ORD-2026-02297` row `eh3b8b9ttk`, target quantity `1`

## 真实业务链路

1. `CREATE_PR_DRAFT` → `MAT-PRE-2026-00519`
2. `SUBMIT_PR` → `MAT-PRE-2026-00519`
3. `CREATE_PI_DRAFT` → `ACC-PINV-2026-00346`
4. `SUBMIT_PI` → `ACC-PINV-2026-00346`
5. `CREATE_PAYMENT_ENTRY_DRAFT` → `ACC-PAY-2026-00186`
6. `SUBMIT_PAYMENT_ENTRY` → `ACC-PAY-2026-00186`

每个动作均由独立审批角色批准，创建独立 Reservation，并产生 `SUCCEEDED` Receipt。最终独立回读为：Purchase Receipt `Completed`；Purchase Invoice `Paid`、应付余额 `0`；Payment Entry `Submitted`、分配 `10`、未分配 `0`；Payment Entry GL 借贷各 `10`，共 `2` 条。

## 角色证据

- 发起人：`output/playwright/phase10-r104-browser-payment-operator-v2-final.png`
- Receiver 审批/执行：`output/playwright/phase10-r104-browser-receiver-v2-pr-create.png`、`output/playwright/phase10-r104-browser-receiver-v2-pr-submit.png`
- Accountant 审批/执行：`output/playwright/phase10-r104-browser-accountant-v2-pi-create.png`、`output/playwright/phase10-r104-browser-accountant-v2-payment-create.png`
- Payment Approver 审批/执行：`output/playwright/phase10-r104-browser-payment-approver-submit.png`
- 未授权 Viewer：`output/playwright/phase10-r104-viewer-new-run-403.png`、`output/playwright/phase10-r104-viewer-runs-403.png`

完整 Action、Approval、Reservation、Receipt、目标回读和 SHA256 见同目录 `phase10-r104-browser-acceptance-v2.json`。

## 限制记录

一次独立探索点击了 Runtime 分析入口；当前 Runtime 认证不可用，接口按预期返回 `503 UNAVAILABLE`。该 Run 随后通过浏览器取消。最终验收 Run 没有把 Runtime 不可用误报成成功，也没有依赖 Runtime 才执行 P2P 写入链路。
