# Phase 2 / P2.2 身份授权安全 Spike

- 日期：2026-08-25
- 状态：取证完成，独立 Test=`PASS`、Review=`PASS`、source/Ponytail Review=`PASS`；ADR `PROPOSED`，等待用户批准
- 关联：`docs/PLAN.md` §7、§11；`docs/security/phase2-p2_2-identity-authorization-spike.md`；`docs/decisions/ADR-0003-runtime-user-authorization.md`

## 改了什么

在固定 Frappe/ERPNext commit pair 上完成了身份授权行为取证，并新增：

1. `docs/security/phase2-p2_2-identity-authorization-spike.md`：记录源码事实、真实 Cookie/API key/OAuth/`auth_hooks`/权限结果，以及尚未实现的 fail-closed 场景。
2. `docs/decisions/ADR-0003-runtime-user-authorization.md`：比较三种用户绑定方案，提出“按 Run 签发短期 capability、Frappe 服务端解析”的 `PROPOSED` 方向，明确最小 Run 身份/范围记录边界和用户批准门禁。

没有创建生产 `auth_hooks`、Gateway endpoint、Run DocType、读取工具或 Runtime ERP/数据库依赖；没有修改 Frappe/ERPNext 上游。

## 为什么做

`docs/PLAN.md` 要求 P2.2 先验证 Frappe 登录态、服务端 Run 引用和 Runtime 用户绑定方式，形成安全 ADR，并在用户批准前停止。当前 Runtime 仍只提供 `/healthz`，因此不能把 Run 伪装成已实现；本日志把真实通过项和 P2.3 前置项分开。

## 实际验证

固定输入：

- Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`
- ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`
- `apps/frappe`、`apps/erpnext` `git status --porcelain` 为空

临时启动 Bench Web：`cd env/dev && bash scripts/dev/env.sh start`；Web 在 `127.0.0.1:8000` 监听，取证结束后停止。

真实取证成功标记：

- Cookie session、CSRF、logout、伪造 sid、原始权限：`COOKIE_PERMISSION_SPIKE_OK`
- API key token/Basic、错误凭据 401、临时 key 清理：`API_KEY_SPIKE_OK`
- OAuth authorization-code、无 Cookie Bearer、过期 token、撤销 token、临时记录清理：`OAUTH_AUTHENTICATE_REVOKE_SPIKE_OK`
- 有效 Cookie 叠加无效 Bearer/API key 的身份优先级：`MIXED_CREDENTIALS_COOKIE_WINS_BEARER_API_KEY_REJECTED`
- Website Viewer `/app` → `/desk` 403 与无 saved CSRF token 的 unsafe POST：`VIEWER_WEBSITE_ROUTE_CSRF_OBSERVED`
- 固定 Frappe 官方 `custom_auth` 的等价 WSGI `auth_hooks` 请求、移除 hook 后拒绝、Synora hook 为空：`AUTH_HOOK_SPIKE_OK`

清理复核结果：`OAuth Client=0`、`OAuth Bearer Token=0`、`OAuth Authorization Code=0`、Buyer `api_key` 为空；site `allow_tests` 恢复为原值 `0`；当前 Synora `auth_hooks=[]`。

只读 Harness 检查：`validate_harness_structure.py` 为 `valid=true`、引用断裂为 0；`detect_drift.py` 为 `has_drift=false`。本增量新增的安全证据和 ADR 尚未写入 Harness manifest/source index，后续如需同步必须另走 `harness-update` 文件级 proposal 和用户批准，不把本次 ADR 批准门禁偷换成 Harness 批准。

完整 `bench --site dev.localhost run-tests --module frappe.tests.test_hooks` 的 5 个 unit tests 先通过，但全局 fixture 因固定 site 不存在 `Payment Gateway` 以 `DoesNotExistError` 停止；该限制未被隐藏，已使用官方测试函数的最小 WSGI 请求完成 auth_hooks 事实验证。

过程中第一次直接以普通 Python 进程执行 API-key 临时脚本时因未初始化 Frappe site 得到 `AttributeError: site`；随后改用 `frappe.init(site="dev.localhost")` + `frappe.connect()` 的正确 Bench site context 重跑并得到成功标记，临时 key 的 `finally` 清理仍执行。

## 限制与未运行项

- 当前开发 site 只有一家公司且没有 User Permission 公司范围，跨公司拒绝不能在本轮声称已通过。
- 当前没有 Run/Gateway，所以伪造 initiator、未知/过期/错配 Run、非 Gateway 路径、重复读取和敏感头日志脱敏均为 `PENDING_P2.3`，不是 PASS。
- OAuth 固定源码对关联 disabled user 的检查不足，是拒绝转发用户 OAuth 的证据；没有为了测试而停用命名用户。
- 混合凭据结果不是统一拒绝：有效 Cookie + 无效 Bearer 仍返回 Cookie Buyer；有效 Cookie + 无效 API key 返回 401。Gateway 必须自行拒绝混合身份来源。
- Website Viewer 访问 `/app` 实际重定向到 `/desk` 并返回 403；403 页面中的 token 字样不是可用 Desk token，不能作为 CSRF 已启用的证据。
- 独立 Test、主 Review 和 source/Ponytail Review 均已返回 `PASS`；本次文档增量满足提交门禁。用户批准 ADR 前仍不开始 P2.3。

## 可重复人工验收

在不打印凭据的前提下，按 `docs/security/phase2-p2_2-identity-authorization-spike.md` 的固定用户和临时 Web 步骤重跑六个 Spike，必须分别得到六个成功标记；随后确认上游 clean、临时 OAuth/API key 记录清零、`allow_tests=0`，并停止 Bench Web。任何 Run/Gateway 生产实现都必须先取得 ADR 用户批准。
