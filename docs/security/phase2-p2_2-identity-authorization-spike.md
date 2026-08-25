# P2.2 身份授权安全 Spike 取证

- 状态：`EVIDENCE_RECORDED / ADR APPROVED / PHASE 2 COMPLETE`
- 日期：2026-08-25
- 阶段：Phase 2 / P2.2
- 关联：`docs/PLAN.md` §7、§11；`docs/PRD.md` F-001/F-002；`docs/ARCHITECTURE.md` 信任与依赖边界；`docs/SPEC.md` §5、§7、§9、§14
- 决策载体：[`docs/decisions/ADR-0003-runtime-user-authorization.md`](../decisions/ADR-0003-runtime-user-authorization.md)（用户于 2026-08-25 批准）

本文件只记录固定 Frappe/ERPNext 基线上的认证、权限和安全边界事实。下列“未实现”均指 P2.2 取证完成时的历史状态；后续 P2.3 实现与证据单独记录。

## 1. 取证范围与固定输入

本 Spike 遵循 `docs/PLAN.md` 的停止条件：先验证 Frappe 的真实登录态和凭据行为，再提交安全 ADR；在用户批准 ADR 前不创建生产授权钩子、Gateway endpoint、Run DocType 或读取工具。

固定上游源码：

- Frappe：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`
- ERPNext：`11e0ba0a1c45f217e2e73e885f699102d06da325`
- 容器内 `apps/frappe`、`apps/erpnext` 工作区均 clean。

运行输入：

- Site：`dev.localhost`；HTTP 仅在临时 Bench Web `127.0.0.1:8000` 上运行，取证后停止。
- 命名用户沿用 Phase 1：`synora-p1-buyer@dev.localhost`（Purchase User）、`synora-p1-accountant@dev.localhost`（Accounts User）、`synora-p1-viewer@dev.localhost`（Website User，无业务角色）。密码只从 `env/dev/.env` 的环境变量注入，未进入命令参数、输出、日志或本文。
- 当前 site 只有公司 `SYNORA-P1 Test Company`，没有 User Permission 公司范围记录；这限制了本轮跨公司场景的可执行性，不能据此声称跨公司已通过。

## 2. 固定源码事实

下列事实均来自上述固定 Frappe checkout；路径和符号可在同一容器内复核。

| 位置 | 事实 | 安全含义 |
| --- | --- | --- |
| `frappe/app.py`：`application`、`init_request` | 请求先初始化 site/session，再调用 `validate_auth()`，然后进入 API/handler。 | Gateway 不能把认证委托给 Runtime 请求体；必须在 Frappe 入口解析。 |
| `frappe/auth.py`：`HTTPRequest`、`CookieManager`、`validate_auth` | `sid` 从服务端 Session 恢复；Cookie 为 HttpOnly、SameSite=Lax，Secure 仅在 HTTPS；unsafe 请求按当前 session 的 CSRF 状态校验。 | Cookie 是服务端会话引用，不是 Runtime 可自述的用户身份。 |
| `frappe/auth.py`：`validate_oauth`、`validate_auth_via_api_keys` | OAuth Bearer、API key/Basic 由服务端解释；API key 只有在当前用户仍为 Guest 时才切换 session user；错误 key/secret 的结果依赖当前认证状态。 | 凭据只能被 Frappe 解释；将用户凭据转发给 Runtime 会扩大权限面，混合凭据还会产生身份优先级歧义。 |
| `frappe/auth.py`：`validate_auth_via_hooks` | `auth_hooks` 无参数执行，hook 可读取请求头并调用 `frappe.set_user()`。 | 请求头可能出现二次身份改写；不能允许 Runtime 选择或注册 hook。 |
| `frappe/oauth.py`：Bearer 校验 | 检查 token 过期、Revoked 状态和 client scope，但固定源码路径未检查关联用户当前 `enabled`；`frappe.set_user()` 本身也不检查 enabled。 | OAuth 转发不能作为 Run 身份绑定；即使凭据有效，也必须由服务端 Run 记录重新约束用户和范围。 |
| `frappe/permissions.py`：`has_permission` | 主要基于 `frappe.session.user`，结合角色、DocPerm、User Permission、Share 和 controller hook。 | 绑定 Run 后仍须在每次工具读取时复用/重检 Frappe 权限。 |
| `frappe/handler.py`、`frappe/api/v1.py`、`frappe/api/v2.py` | API 自动处理 whitelist、方法、DocType/字段等通用检查；任意自定义 Gateway 方法的 Run 绑定需自行实现。 | “调用了 Frappe API”不等于已完成 Run 授权；P2.3 必须建立显式门禁。 |
| `frappe/recorder.py`：请求记录 | 固定源码默认可保存原始请求 headers。 | `Authorization`、Cookie、CSRF、capability 的脱敏必须成为 Gateway 的实现和测试要求；本轮没有伪造生产日志来声称已通过。 |

## 3. 真实运行取证

### 3.1 Cookie session、CSRF 与失效

使用三个命名用户通过 `POST /api/method/login` 建立真实 Frappe session，然后只通过 Cookie 调用 `frappe.auth.get_logged_user`、`ping` 和 `/app`；sid 值从未打印。

实际成功标记：`COOKIE_PERMISSION_SPIKE_OK`。

观察结果：

- 三个用户均收到非 `Guest` 的 `sid`；`HttpOnly=True`、`SameSite=Lax`、HTTP 下 `Secure=False`；取证结构明确记录 `sid_value_logged=False`。
- 三个用户的 `get_logged_user` 均解析为各自登录用户，说明当前请求用户由 Frappe session 服务端解析，而不是由请求体提交；这只是未来 Run initiator 绑定的基线，不是 Run 证据。
- Buyer 与 Accountant 的 `/app` → `/desk` 页面 token 对 unsafe API POST 触发 `CSRFTokenError`，携带该 token 后成功。
- Viewer 请求 `/app` 返回 `301 Location: /desk`，跟随后 `/desk` 返回 HTTP 403（Website User 无 Desk 权限）；403 响应体虽包含 `frappe.csrf_token` 字样，但不是可用 Desk 页面，且该 API session 没有 saved CSRF token，unsafe POST 返回 HTTP 200。该差异被记录为真实路由和 session 观察，不能把“页面出现 token”泛化为所有 API 请求都启用 CSRF 门禁。
- logout 后复用旧 sid、以及使用伪造 sid，均未再解析为原登录用户；脚本接受 Frappe 的 `200/401/403` 响应差异，但严格拒绝响应继续返回原用户身份。

### 3.2 API key 与 Basic

通过 Frappe 的 `generate_keys` 临时为 Buyer 生成 key/secret，随后立即清理并复核 `User.api_key` 为空；secret 和完整 Authorization 值从未输出。

实际成功标记：`API_KEY_SPIKE_OK`。

- `Authorization: token <api_key>:<api_secret>` → HTTP 200，用户为 Buyer（请求无 Cookie）。
- `Authorization: Basic <base64(api_key:api_secret)>` → HTTP 200，用户为 Buyer（请求无 Cookie）。
- 错误 secret、随机 key、无效 Bearer → HTTP 401，未解析为 Buyer。
- 临时 key 清理后，`buyer_api_key_present=False`。

### 3.3 OAuth authorization-code、Bearer、过期与撤销

通过 Frappe ORM 建立一次临时 OAuth Client，再以命名 Buyer 的真实 Frappe session 请求 authorization endpoint，交换 Bearer token；测试结束删除临时 OAuth Client、Authorization Code、Bearer Token 和必要的临时 Social Login Key，不保留 token/client 值。

该授权请求使用 Frappe `LoginManager` 建立的真实服务端 Buyer session，不是把密码写入 OAuth URL；浏览器 UI 的密码登录页面未另行自动化，Cookie 登录 Spike 与固定 Frappe OAuth 官方测试已覆盖对应的 session/token 机制。

实际成功标记：`OAUTH_AUTHENTICATE_REVOKE_SPIKE_OK`。

- authorization-code exchange 返回 Bearer token；无 Cookie 的 Bearer 调用 `get_logged_user` → HTTP 200，用户为 Buyer。
- 通过 ORM 将该临时 token 的 `expiration_time` 设为过去时间；Bearer 调用 → HTTP 401/403，未解析为 Buyer。
- 调用 `revoke_token` 后再次使用同一 Bearer → HTTP 401/403，未解析为 Buyer。
- 清理复核：`OAuth Client`、`OAuth Bearer Token`、`OAuth Authorization Code` 数量均为 0。

固定源码还显示关联用户 disabled 状态并非 OAuth Bearer 校验条件，因此 ADR 明确拒绝把用户 OAuth 直接转发给 Runtime。

### 3.4 混合凭据身份优先级

额外使用有效 Buyer Cookie，在同一 `get_logged_user` 请求中分别叠加无效 Bearer 和无效 API key；不打印任何凭据值。

实际成功标记：`MIXED_CREDENTIALS_COOKIE_WINS_BEARER_API_KEY_REJECTED`。

- 有效 Cookie + `Authorization: Bearer invalid` → HTTP 200，仍为 Buyer；Cookie 用户胜出。
- 有效 Cookie + `Authorization: token unknown:wrong` → HTTP 401；API key 路径拒绝请求。

因此，“无 Cookie 的 Bearer 过期/撤销返回 401/403”不能被扩大解释为“任何混合凭据都 fail closed”。未来 Gateway 必须拒绝混合凭据，不能把 Frappe 的 Cookie/Bearer 优先级当作 Run 授权。

### 3.5 `auth_hooks` 真实行为

使用固定 Frappe 官方测试中的 `frappe.tests.test_hooks.custom_auth`，通过同一 Frappe WSGI test client 临时 patch `auth_hooks`：`Bearer set_test_example_user` 被 hook 解析为 `test@example.com`；移除 patch 后同一标记凭据返回 401/403。实际成功标记：`AUTH_HOOK_SPIKE_OK`。

当前 Synora App 的 `frappe.get_hooks("auth_hooks", app_name="synora_agentic_erp")` 为 `[]`；`synora_agentic_erp/hooks.py` 没有生产认证 hook。

尝试运行完整 `bench --site dev.localhost run-tests --module frappe.tests.test_hooks` 时，5 个 unit tests 先通过，但测试环境全局 fixture 在 `Payment Gateway` 不存在处停止（`DoesNotExistError`）；该 site 的 `allow_tests` 已恢复为原值 `0`。因此本文使用官方测试函数的等价最小 WSGI 请求取证，并把完整模块 setup 限制如实保留。

### 3.6 原始 ERP 权限基线

绑定前直接使用 Cookie 登录调用 Frappe REST，作为 Phase 1 权限基线的重复验证：

| 用户 | 请求 | 实际结果 |
| --- | --- | --- |
| Buyer | `GET /api/resource/Material Request` | HTTP 200 |
| Viewer | `GET /api/resource/Material Request` | HTTP 403 |
| Accountant | `GET /api/resource/Purchase Order` | HTTP 403 |

这证明 P2.2 的身份取证没有替换或扩大现有角色权限；它不证明未来 Gateway 已绑定 Run。

## 4. 必须 fail closed、但本轮尚未实现的场景

下表不是“通过”清单，而是 P2.3 实现前置的验收矩阵。Runtime 当前只有 `/healthz`，没有 ERP 凭据、Gateway 路径或 Run 记录，所以本轮不伪造实现来测试它们。

| 场景 | 本轮状态 | P2.3 必须证明 |
| --- | --- | --- |
| Runtime 只能提交不可伪造的 `run_id`/capability | `NOT_IMPLEMENTED_BY_DESIGN` | 请求体不能提交/覆盖 initiator、user、company 或 warehouse；Frappe 服务端解析 Run。 |
| 未知、过期、撤销、错配 Run/capability | `PENDING_P2.3` | 统一拒绝且不泄露 Run 是否存在；不能回退为 Guest、服务用户或请求体用户。 |
| 伪造 initiator、混合 Cookie/Bearer/Service credential | `PENDING_P2.3` | Gateway 先拒绝混合凭据，再做唯一服务端身份解析；任何冲突 fail closed。 |
| 跨公司/仓库与无权限用户 | `NOT_VERIFIABLE` | 需隔离 fixture 建立第二公司和 User Permission；绑定前后权限结果保持不扩大。当前 site 只有一家公司。 |
| 非 Gateway `/api/resource`、任意 whitelisted `/api/method`、任意 URL | `NOT_IMPLEMENTED_BY_DESIGN` | Runtime capability/service credential 只能到注册 Gateway；非 Gateway 必须拒绝。 |
| 敏感请求头、Recorder、响应日志 | `PENDING_P2.3` | Authorization、Cookie、CSRF、capability 和 token 不出现在日志/Recorder/响应；仅保留脱敏 correlation。 |
| 重复只读调用 | `PENDING_P2.3` | 同一 Run/capability 重复读取结果一致，不扩大 scope，不改变 initiator，并保留 correlation。 |

## 5. 本轮结论

1. Frappe 的 Cookie、API key/Basic、OAuth 和 `auth_hooks` 都由服务端解释当前请求；用户身份不能由 Runtime 自己声明，但混合 Cookie + Bearer 存在 Cookie 胜出行为。
2. 用户 Cookie/API key/OAuth 转发会把 ERP 原生权限面、凭据生命周期、混合凭据优先级和 `auth_hooks` 身份改写能力暴露给 Runtime，且 OAuth 还存在 disabled-user 校验缺口；不采纳该方向。
3. P2.2 推荐的短期安全边界是“按 Run 签发的短期 capability + Frappe 服务端 Run 解析”，用户已于 2026-08-25 批准 ADR-0003。
4. 实现最小 Run 身份/范围记录是进入 P2.3 Gateway 的前置条件，但不等同于本轮提前实现完整 Phase 3 Agent Run。

## 6. 复核与停止门禁

- 截至 P2.2 取证提交时没有生产 `auth_hooks`、Gateway endpoint、Run DocType、ERP 读取工具或 Runtime ERP/DB import；后续状态见 P2.3 开发日志。
- 独立 Test/Review 必须分别审查本文件、ADR 和上述原始结果；任一结论不是 `PASS` 都不得提交。
- 本 Spike 当时停止在批准门禁；用户已于 2026-08-25 批准，后续实现证据由 P2.3–P2.6 单独记录。
