# Test — Step 005：独立验证幂等与对账

不信任 writer/执行者自述。独立制造重复、并发、响应丢失和重启，核对真实 ERP 与 Synora 最终状态。不要修实现。

## 需求来源

- `docs/SPEC.md#11-idempotency-and-reconciliation`。
- `docs/PRD.md#57-f-007-receipt幂等与对账`。
- `docs/ACCEPTANCE.md#First Governed-Write Acceptance`。

## 行为矩阵

- 正常：first write 一单一 Receipt；same digest replay 返回同一 verified result；post-commit lost response 通过 status/reconcile 找回。
- 错误：different digest conflict、STARTED uncertain、permission loss、no/multiple candidate、read-back mismatch，全部零 second write；结果不足进入 manual intervention。
- 边界：T1/T2 每个 crash point、请求仍运行、lease boundary、并发 execute/reconcile、服务重启、旧 revision/owner、跨用户/公司读取。

## 测试范围

- Unit：state/lease/CAS/replay/classification 全表。
- Integration：两事务边界、rollback、unique lock、Receipt/audit/reconciliation records。
- Real HTTP/process：至少一次 commit 后客户端收不到响应；至少一次 T1 后 process failure；复验 service restart 后仍能收敛。
- Security：no blind retry；cross-user/tenant denial；fault hooks 不存在于 production API；日志不泄漏。
- Manual：从 Runs/ERP Desk 查同一 MR/Receipt，确认所有重放都没有第二个 MR。

## 失败证据

每个 fault case 保存时间线、reservation revision/owner/lease、HTTP observation、writer call count、MR count/name、Receipt/reconciliation ids、最终 Run/Action state。不要清理不确定候选。

## 判定

same/different digest、response loss 和并发三类核心用例必须各自独立通过；任何二次写、盲重试、错误成功或无法审计均为 `FAIL/BLOCKED`，否则 `PASS`。
