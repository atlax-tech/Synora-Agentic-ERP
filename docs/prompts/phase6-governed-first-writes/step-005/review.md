# Review — Step 005：审查幂等与对账安全

## 审查输入

- 原始任务：P6.4 response loss、idempotency、reconciliation、manual intervention。
- 约束：SPEC §11、PRD F-007、Step 004 writer。
- 预期 diff：reservation/reconciliation/fault tests/log；无新 action scope。
- 证据：最终 diff、独立 real HTTP/process/concurrency 输出、ERP/Receipt timeline。

## 审查维度

- T1 reservation 是否在 write 前 durable，T2 write+Receipt 是否原子收敛。
- same digest/different digest/STARTED 是否有互斥且可证明的行为。
- uncertain result 是否绝不进入 writer retry；新 action 是否需要新 approval。
- reconciliation 是否只读、按授权 scope、以 ERP 最终事实分类，多候选不猜。
- fault tests 是否真的覆盖 post-commit response loss，而非只抛异常回滚。
- lease/owner/CAS 是否抵抗并发和重启，状态是否单调。
- audit/错误/候选摘要是否最小披露。
- 是否为通用未来动作过度设计；当前只需 MR，结构只保留 PO 明确下一步所需复用。

## 判定

只返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。二次写、盲重试、reservation 不持久、post-commit 丢响应未实测或不确定结果错误成功均为 blocking。
