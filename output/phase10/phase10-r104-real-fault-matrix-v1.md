# Phase 10 R10.4 真实 P2P 进程故障矩阵

- 捕获时间: `2026-09-10T18:44:10Z`
- 基线提交: `4381d47fb488751c2e4596285e27153c0e31acb9`
- 案例数: `28` (7 个动作 x 4 个故障位置)
- 故障注入: 仅通过测试进程内 monkeypatch 与独立 `bench console` 子进程; 生产 HTTP API 没有故障参数。
- 回读: 每次故障后另起 `bench console` 连接读取 Action、Reservation、Receipt、Run 与 ERP 目标。

| 动作 | 故障位置 | worker 退出 | 回读 Action | Reservation | Receipt | ERP 目标 |
| --- | --- | ---: | --- | --- | --- | --- |
| `SUBMIT_PO` | `reservation_committed` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `SUBMIT_PO` | `before_erp_call` | `0` | `EXPIRED` | `FAILED` | `FAILED` | `PUR-ORD-2026-02268` |
| `SUBMIT_PO` | `after_erp_call` | `137` | `EXECUTED` | `RECONCILED_SUCCESS` | `RECONCILED_SUCCESS` | `PUR-ORD-2026-02269` |
| `SUBMIT_PO` | `before_receipt` | `0` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PR_DRAFT` | `reservation_committed` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PR_DRAFT` | `before_erp_call` | `0` | `EXPIRED` | `FAILED` | `FAILED` | `—` |
| `CREATE_PR_DRAFT` | `after_erp_call` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PR_DRAFT` | `before_receipt` | `0` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `SUBMIT_PR` | `reservation_committed` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `SUBMIT_PR` | `before_erp_call` | `0` | `EXPIRED` | `FAILED` | `FAILED` | `MAT-PRE-2026-00499` |
| `SUBMIT_PR` | `after_erp_call` | `137` | `EXECUTED` | `RECONCILED_SUCCESS` | `RECONCILED_SUCCESS` | `MAT-PRE-2026-00500` |
| `SUBMIT_PR` | `before_receipt` | `0` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PI_DRAFT` | `reservation_committed` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PI_DRAFT` | `before_erp_call` | `0` | `EXPIRED` | `FAILED` | `FAILED` | `—` |
| `CREATE_PI_DRAFT` | `after_erp_call` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PI_DRAFT` | `before_receipt` | `0` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `SUBMIT_PI` | `reservation_committed` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `SUBMIT_PI` | `before_erp_call` | `0` | `EXPIRED` | `FAILED` | `FAILED` | `ACC-PINV-2026-00334` |
| `SUBMIT_PI` | `after_erp_call` | `137` | `EXECUTED` | `RECONCILED_SUCCESS` | `RECONCILED_SUCCESS` | `ACC-PINV-2026-00335` |
| `SUBMIT_PI` | `before_receipt` | `0` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PAYMENT_ENTRY_DRAFT` | `reservation_committed` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PAYMENT_ENTRY_DRAFT` | `before_erp_call` | `0` | `EXPIRED` | `FAILED` | `FAILED` | `—` |
| `CREATE_PAYMENT_ENTRY_DRAFT` | `after_erp_call` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `CREATE_PAYMENT_ENTRY_DRAFT` | `before_receipt` | `0` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `SUBMIT_PAYMENT_ENTRY` | `reservation_committed` | `137` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
| `SUBMIT_PAYMENT_ENTRY` | `before_erp_call` | `0` | `EXPIRED` | `FAILED` | `FAILED` | `ACC-PAY-2026-00181` |
| `SUBMIT_PAYMENT_ENTRY` | `after_erp_call` | `137` | `EXECUTED` | `RECONCILED_SUCCESS` | `RECONCILED_SUCCESS` | `ACC-PAY-2026-00182` |
| `SUBMIT_PAYMENT_ENTRY` | `before_receipt` | `0` | `EXPIRED` | `MANUAL_INTERVENTION` | `MANUAL_INTERVENTION` | `—` |
