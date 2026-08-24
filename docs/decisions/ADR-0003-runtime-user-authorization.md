# ADR-0003：Runtime 与 Frappe 的用户绑定授权方案

- 状态：`PROPOSED`（等待用户批准）
- 日期：2026-08-25
- 关联：`docs/PLAN.md` §7、§11 P2.2；`docs/PRD.md` F-001/F-002；`docs/ARCHITECTURE.md`；`docs/SPEC.md` §5、§7、§9、§14；[`docs/security/phase2-p2_2-identity-authorization-spike.md`](../security/phase2-p2_2-identity-authorization-spike.md)
- 批准门禁：用户明确批准本 ADR 后，才允许实现最小 Run 身份/范围记录和 P2.3 Gateway；批准前不得创建生产 `auth_hooks`、Gateway endpoint、读取工具或完整 Phase 3 Agent Run。

## 背景（Context）

产品要求 Frappe 从登录态记录 Agent Run 的 initiator，Runtime 不能提交或覆盖 initiator；工具读取必须继承授权用户可访问的公司、仓库和 ERP 文档范围。`docs/ARCHITECTURE.md` 和 `docs/SPEC.md` 同时禁止 Runtime 直连 MariaDB、持有任意 ERP 写凭证或绕过 Frappe/ERPNext 权限、校验、Workflow 和审计。

P2.2 Spike 在固定 Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`、ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325` 上验证了：

- Cookie `sid` 是服务端 Session 引用；API key/Basic 和 OAuth Bearer 由 Frappe 解释当前请求；无 Cookie 的错误/过期/撤销 Bearer fail closed，但有效 Cookie + 无效 Bearer 可能继续使用 Cookie 用户。
- `auth_hooks` 可读取请求头并调用 `frappe.set_user()`，所以请求头身份可能被 hook 二次改写；当前 Synora 没有注册 `auth_hooks`。
- OAuth Bearer 校验未检查关联用户当前 `enabled`，因此“把用户 OAuth 转发给 Runtime”不能成为可靠的 Run 绑定。
- Frappe 通用 API 不会替任意自定义 Gateway 方法完成 Run 解析、公司范围、工具 allowlist、敏感头脱敏或重复调用治理。

完整原始结果见安全取证文档；本 ADR 不把尚未实现的 Gateway 或 Run 行为描述为通过。

## 提议决策（Proposed Decision）

### 推荐方向：按 Run 签发的短期 capability，Frappe 服务端解析

拟在进入 P2.3 前建立最小的服务端 Run 身份/范围记录。Frappe 在已认证的用户请求中生成不可变 `run_id`，并为该 Run 签发短期、受 audience/expiry/status 约束的 capability。首选随机 opaque capability，并在 Frappe 只保存不可逆摘要；如后续选择 signed envelope，仍必须保留服务端状态、撤销、audience、expiry 和 scope 绑定，不能让签名载荷成为用户身份来源。

Runtime 每次调用只提交：

```text
run_id + capability + gateway tool request + correlation_id
```

Frappe Gateway 服务端必须：

1. 用 capability 与 `run_id` 查找服务端 Run 记录，解析 initiator、company scope、warehouse scope、状态、过期时间、state version 和 correlation；
2. 拒绝请求体中的 `initiator`、`user`、`company_scope`、`warehouse_scope` 或任何身份替代字段；
3. 在每次读取前，以解析出的 Frappe 用户上下文重新检查对象、公司、仓库、DocType/字段和当前 ERP 权限；
4. 只允许版本化注册表中的 Gateway 工具，拒绝 `/api/resource`、任意 whitelisted method、任意 URL、MariaDB/ERP 内部 import 和非 Gateway 路径；
5. 对未知、过期、撤销、错配、状态不允许、跨公司、无权限和混合凭据统一 fail closed，不泄露 Run 或文档存在性；
6. 将 capability、Cookie、Authorization、CSRF、API secret、OAuth token 等敏感值从响应、普通日志、Recorder 和 Runtime trace 中脱敏，只保留必要 correlation；
7. 对重复只读请求保持同一 Run/scope，不扩大权限、不改变 initiator，并保留可审计 correlation。

### 最小 Run 记录边界

该记录是 P2.3 的前置身份/范围记录，不是本轮提前实现完整 Phase 3 Agent Run。最小字段概念为：

- immutable `run_id`；
- 服务端解析的 `initiator`；
- `company_scope`、可选 `warehouse_scope`；
- capability 摘要、audience、created/expiry/revoked；
- Run 状态、state version、correlation 和时间戳。

Goal、分析状态、UI、checkpoint、Proposal 和写入生命周期仍属于 Phase 3/4 的独立增量，不在本 ADR 授权范围内。

## 备选方案（Alternatives）

| 方案 | 优点 | 主要风险/限制 | 结论 |
| --- | --- | --- | --- |
| 1. 按 Run 签发短期 opaque/signed capability + 服务端 Run 解析 | Runtime 不持有用户凭据；可绑定 Run、范围、audience、expiry、撤销和 correlation；每次读取可重检 ERP 权限 | 需要最小 Run 记录和 capability 生命周期；opaque 与 signed 的撤销/密钥轮换要在 P2.3 细化 | **推荐**：优先 opaque + 服务端摘要；signed 只有在等价撤销与审计证据成立时才可采用 |
| 2. 专用 Runtime 服务凭据 + 服务端 Run 解析 | 适合服务到服务认证；可做最小角色、轮换和网络边界 | 服务凭据本身不代表 initiator；若无 Run 解析会变成宽权限服务账号；仍需 capability/run 绑定和每次权限重检 | **备选**：只能作为服务身份，不能替代 Run 用户绑定 |
| 3. 转发用户 Cookie / API key / OAuth | 复用 Frappe 原生认证，初始实现表面简单 | 暴露用户凭据和原生权限面；Cookie/Bearer/API key 与 `auth_hooks` 存在身份覆盖/顺序歧义；OAuth 过期/撤销/disabled-user 语义复杂；Runtime 难以证明只访问特定 Run 范围 | **拒绝** |

## 安全不变量（必须进入实现验收）

- 用户身份只来自 Frappe 登录态与服务端 Run 记录，不能来自 Runtime body、模型输出、检索内容或 capability 自述字段。
- Capability 必须短期、可撤销、绑定单一 Run/audience/scope；未知、过期、撤销、错配和状态非法均拒绝。
- Gateway 必须拒绝 Cookie、Bearer、API key、service credential 等混合身份来源；不得依赖 Frappe 的 Cookie/Bearer 优先级来决定 initiator。
- Frappe 权限是最终门禁；绑定 Run 不得扩大 Buyer、Viewer、Accountant 的原始权限。
- 任意非 Gateway 路径、任意工具名/版本/字段、任意 URL、直接数据库或 ERP 内部模块路径均拒绝。
- 跨公司、仓库、文档、停用对象和 User Permission 都必须在每次读取重检；当前 site 只有一家公司，第二公司 fixture 是 P2.3 必须补齐的测试条件。
- 敏感头和 token 不进入日志、Recorder、响应或模型上下文；只保留脱敏 correlation。
- 重复只读调用不能改变 Run 身份/范围或产生权限扩张；结果和审计 correlation 可重复核对。

## 后果（Consequences）

正向：Runtime 不接触用户长期凭据；身份、公司范围和 Frappe 权限仍由服务端控制；capability 可做到短期、撤销、审计和 fail closed；为 P2.3 typed Gateway 提供明确的输入边界。

代价：需要在 Gateway 前置建立最小 Run 身份/范围记录、capability 发行/撤销/过期处理、日志脱敏和第二公司权限 fixture；不能仅靠 Frappe 通用 API 或一个服务账号完成。

未决实现细节：opaque 与 signed capability 的最终格式、存储摘要算法、密钥轮换（若采用 signed）、TTL、撤销传播和 Gateway 错误 envelope 必须在用户批准后通过 P2.3 独立契约增量决定，不能在本 ADR 批准前偷偷实现。

## 明确不在本 ADR 内

- 不创建生产 `auth_hooks`；
- 不创建 Gateway endpoint、ERP 读取工具或任意 ERP 写路径；
- 不创建完整 Phase 3 Agent Run、Goal/UI/checkpoint/Proposal；
- 不修改 Frappe/ERPNext 上游，不直写 MariaDB，不转发用户 Cookie/API key/OAuth；
- 不把 `runtime-user-authorization` 在 Harness 中标记为已解决，除非后续实现和审批证据完成。

## 批准请求（Approval Gate）

请用户明确批准或拒绝本 ADR 的“推荐方向 + 最小 Run 记录边界 + 安全不变量”。在收到明确批准前，本 ADR 保持 `PROPOSED`，P2.2 到此停止，不开始 P2.3。

## 证据与取代（Evidence / Supersession）

原始认证与权限取证、命令标记、环境限制和未实现矩阵见 `docs/security/phase2-p2_2-identity-authorization-spike.md`。本 ADR 当前没有取代既有 ADR；用户批准后若实现细节改变本推荐，必须新增或修订 ADR，并重新经过独立 Test/Review。
