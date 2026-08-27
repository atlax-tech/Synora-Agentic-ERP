# Test — Step 003：独立验证 Policy、审批与重检

验证每个门禁的顺序、身份权威、并发与 TOCTOU；不要修实现。

## 需求来源

- `docs/SPEC.md#10-policy-and-approval-evaluation-order`。
- `docs/PRD.md#55-f-005-policy--rbac--approval`。
- Step 001 mapping、Step 002 contracts。

## 行为矩阵

- 正常：有权发起人对有效 MR/PO Draft proposal 明确确认，Approval 绑定 digest；precheck 对未变化 current state 返回 typed executable context，但不写 ERP。
- 错误：无 session/无权限/跨公司/伪造 actor、schema invalid、policy reject、Workflow 更严格、过期、digest mismatch、审批后撤权/停用对象/重复采购变化全部 fail closed。
- 边界：同时 approve/decline、重复点击、旧 action/revision、Workflow 在确认后改变、时间边界、decimal/UOM、已有草稿在 precheck 前出现。

## 测试范围

- Unit：严格门禁顺序和 short-circuit；policy/Workflow precedence；snapshot/expiry；typed error。
- Integration：登录态 Buyer/Viewer/跨公司；current DocPerm/User Permission；并发 decision CAS；事务回滚；审计可见性。
- HTTP：请求体伪造 actor/role/digest/version；GET/POST 白名单；CSRF/session 语义；重复点击。
- Architecture：precheck 没有 controller insert/save，MR/PO count-before/count-after 恒等，Runtime 不可达审批/执行凭证。
- Manual：浏览只读 proposal/decision 数据，验证后果/过期/拒绝 reason 可展示但没有执行按钮。

## 失败证据

保留每个 case 的 actor/session、run/action/digest/state_version、permission/Workflow snapshot、DB 状态和业务单据计数。发现任何业务写即停止。

## 判定

所有有限授权/漂移/并发场景 100% 通过且 ERP 仍零写时为 `PASS`；否则 `FAIL/BLOCKED`。
