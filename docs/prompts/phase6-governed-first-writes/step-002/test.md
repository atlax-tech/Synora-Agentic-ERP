# Test — Step 002：独立验证治理记录与状态机

独立测试 contract、持久化、权限、并发和不可伪造性。不要修改实现。

## 需求来源

- `docs/SPEC.md#7-canonical-contract-concepts`、`#82-proposed-action`。
- `docs/PRD.md#54-f-004-可解释计划与-proposedaction`、`#57-f-007-receipt幂等与对账`。
- Step 001 已批准 mapping。

## 行为矩阵

- 正常：已知 MR/PO action 产生稳定 digest；合法转换一次成功；有权用户只读可见；Receipt 只有完整 verified outcome 才可生成。
- 错误：unknown version/action/field/enum、缺 evidence、自然语言与 typed payload 冲突、错误 digest、过期时间、伪造 actor/Receipt、非法转换全部拒绝且无持久化副作用。
- 边界：duplicate JSON key、NaN/Infinity、decimal/date/timezone、最大字段长度、相同 action/digest、并发 approve/decline、重复 transition、跨用户/公司读取。

## 测试范围

- Unit：逐 contract 字段；canonical bytes/digest golden；Action 全状态表；receipt terminal invariants。
- Integration：migrate existing site；唯一索引；事务回滚；行锁/CAS 并发；DocType REST/UI create/update/delete 拒绝；Run visibility。
- Architecture：Runtime、浏览器和模型均无创建 `APPROVED/EXECUTED/success receipt` 的直接路径；无 MR/PO insert/save。
- Regression：Phase 3–5 Run/state/Trace/workflow targeted checks。
- Manual：以普通 Buyer、无业务角色、System Manager 检查列表/表单；System Manager 能运维查看但不能绕过 service 构造已执行事实。

## 失败证据

保留 payload、canonical bytes/digest、当前/目标状态、DB 记录数、并发结果、用户/角色和命令退出码；任何测试意外创建 ERP MR/PO 立即停止。

## 判定

仅当治理记录严格、关联、不可变、权限隔离、并发 fail closed 且真实写仍不可达时返回 `PASS`；否则 `FAIL/BLOCKED`。
