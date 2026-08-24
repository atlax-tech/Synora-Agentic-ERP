# Phase 2 P2.3：Run capability 与 Typed Gateway

- 日期：2026-08-25
- 需求：PRD F-002；PLAN P2.3；已批准 ADR-0003。

## 改动与原因

实现最小 `Synora Agent Run` 服务端记录、五分钟 opaque capability（仅存 SHA-256 摘要）、严格版本化 Gateway 请求/错误 envelope、固定工具注册表和不可变调用审计。发行/撤销路径要求单一 Frappe 登录身份并拒绝混合 Cookie+Authorization；capability-only `execute` 路径拒绝 Cookie、Authorization、CSRF 和已登录会话。所有路径拒绝请求体伪造身份/范围及未知字段、工具或版本。该记录只承载 Phase 2 身份与范围，不提前实现 Phase 3 Goal、状态机或 UI。

ADR-0003 已按用户明确批准更新为 `APPROVED`。

## 验证

实际运行 `make format-check`、`make lint`、`make type`、`make unit` 均通过（unit 4 passed）；`make integration` 通过（Bench App 13 passed）。测试覆盖服务端 initiator、摘要存储、错配/未知/过期/撤销 capability、不可变身份范围、撤销审计、身份伪造、混合凭据、Recorder 关闭、严格输入/输出、分页上限、结果限制、超时、快照、异常脱敏和成功/拒绝 Gateway Audit。固定 Frappe/ERPNext SHA 保持 clean。

## 限制与手工验收

本增量尚未注册 ERP 读取工具，也未提供 Runtime client。可用 Buyer 登录态调用 `issue_run`，确认响应只返回一次 capability；再以 Guest/capability 调用未注册工具，应得到 `TOOL_NOT_ALLOWED` 且响应不含 capability。
