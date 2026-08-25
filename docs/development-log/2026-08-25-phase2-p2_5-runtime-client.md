# Phase 2 P2.5：Runtime typed Gateway client

- 日期：2026-08-25
- 需求：PRD F-002；PLAN P2.5；ADR-0003。

## 改动

Runtime 新增严格 Pydantic 请求/响应模型与 HTTPX 异步客户端。六个 READ tool 使用判别联合白名单；客户端只从部署环境 `SYNORA_GATEWAY_ORIGIN` 读取一个 HTTP(S) origin，只 POST 固定 `synora_agentic_erp.api.execute` 路径，不向调用者提供 URL/method 参数、用户 Cookie、Authorization 或 CSRF 接口。capability 严格匹配服务端 `token_urlsafe(32)` 的 43 位 ASCII URL-safe 形状，只在发送时短暂进入请求体；tool input 若包含同一 secret 会在发送前拒绝，模型 repr 和异常均不保留它。

超时、传输、协议和 Gateway 拒绝分别使用 typed exception；未知字段、工具、版本和错误码 fail closed；Frappe `message` wrapper 与完整 Gateway envelope 必须严格解析。响应必须与请求的 Run、correlation、tool/version 匹配，以流式有界读取限制为 2 MB 并显式关闭响应；ambient proxy 被禁用，每个发送前的 request 都剥离 Cookie 且响应后清空 jar，服务端反射内容和局部 payload 不会进入异常链。

## 验证与限制

实际 `format-check`、`lint`、`type`、`unit` 通过（unit 29 passed），Bench App 回归 22 passed。HTTPX 测试覆盖固定路径/头/请求体、tool-input secret 发送前拒绝、未知工具与恶意 origin、禁用 ambient SOCKS proxy、Set-Cookie 顺序/并发隔离、有限正数 deadline、timeout、partial-body HTTPX/未知 transport error 与 task cancellation、畸形/流式超限/错配响应、响应关闭、Gateway 403、重复调用、未知/已知错误、成功响应、任意 JSON 顶层及 Unicode-escaped 反射，以及 Pydantic 校验错误、异常链和 traceback locals 中的 capability 脱敏。

独立 Test 与 Review 门禁曾分别返回 `FAIL`/`CHANGES_REQUIRED`，发现两处 P1 并已修复后重跑通过：`gateway.py` 中 `response_payload.clear()` 触发 mypy `union-attr`（加 `assert isinstance` 收窄）；`test_gateway.py` 相邻字符串字面量未合并（`ruff format` 修复）。门禁后按 Review 建议补齐契约测试（run_id/tool.name/tool.version 错配 fail closed、空 body/非 JSON/多余顶层键 fail closed、`GatewayRejected` 的 `retryable` 传递、failure 包络 correlation_id 错配与成功包络 schema_version 错配 fail closed），unit 由 25 增至 29；三轮独立 Test/Review 最终均返回 `PASS`。多异常 `except` 采用无括号写法（如 `except ValueError, TypeError, RecursionError:`），这是 Python 3.14 PEP 758 新语法且被 `ruff format` 强制（括号形式无法通过 format-check）；项目 `requires-python = ">=3.14,<3.15"`，无跨版本兼容问题。Review 还提出一个信任边界内的残余风险：客户端无法校验服务端返回的 `authorized_scope`/`state_version`/`snapshot` 是否与真实 Run 一致，已列入 P2.6 对照验证。

本增量尚未启动真实 Bench HTTP；该端到端证据属于 P2.6。

大白话解释：本增量做的是「Runtime 侧的安全收件箱」——它只认一个固定地址、只收一种格式的信封、只发一种请求，任何带凭据的请求或陌生回包都会被拒绝，核心密钥（capability）在任何报错信息里都不会留下痕迹。

手工验收：用固定 origin 构造 `GatewayClient`，提交 typed `GatewayRequest`；合法响应解析为 `GatewaySuccess`，未知工具在发请求前被 Pydantic 拒绝，timeout 不泄漏 capability。
