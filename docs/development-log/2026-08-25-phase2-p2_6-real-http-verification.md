# Phase 2 P2.6：真实 Bench HTTP 端到端验证

- 日期：2026-08-25
- 需求：PRD F-002；PLAN P2.6；SPEC §9/§17。

## 改动

1. **服务端契约修复**：`synora_agentic_erp/api.py` 的 `execute` 入口在 `parse_request` 前剥离 Frappe RPC 路由注入的 `cmd` 键（固定源码 `frappe/api/v1.py` 会把请求路径写入 `form_dict.cmd`，再经 `frappe.call(method, **form_dict)` 传给 whitelist 方法）。此前直接 Python 调用 `execute(**kwargs)` 的集成测试不携带该键，真实 HTTP 下契约严格解析会误判为未知字段而 `INVALID_INPUT`。这是 P2.6 真实 HTTP 验证暴露的 P2.3 传输层契约缺陷。
2. **服务端回归测试**：`test_gateway_contract.py` 新增 `test_execute_strips_frappe_rpc_cmd_injection`，断言带 `cmd` 的 payload 仍按契约解析（未注册工具返回 `TOOL_NOT_ALLOWED` 而非 `INVALID_INPUT`）。
3. **验证脚本**：新增 `env/dev/p26/p26_data.py`（Bench console 内幂等数据准备，成功标记 `P26-DATA-OK`）与 `env/dev/p26/p26_e2e.py`（宿主机真实 HTTP 端到端，11 个场景，成功标记 `P26-E2E-OK`）。数据脚本每次运行先清理自身命名空间数据再重建（重复运行安全），并补齐 **ADR-0003 明确要求的第二公司 fixture**（SYNORA-P26 Test Company + 其 Warehouse/Supplier/开放 PO），用于跨公司数据隔离验证。

## 真实 HTTP 验证证据（11/11 通过）

前置：bench web 监听 `127.0.0.1:8000`；数据准备 `P26-DATA-OK`（含第二公司及其开放 PO）。

```
P26-BASIC-OK              tool=item.lookup rows=1        正常路径，真实 ERP 数据
P26-PERMISSION_DENIED-OK  code=PERMISSION_DENIED         Accountant 调 open PO 被拒（403 语义）
P26-SCOPE_DENIED-OK       code=SCOPE_DENIED              run 限定仓库后请求根仓库被拒
P26-PAGINATION_CLIENT-OK  ValidationError                客户端模型层 limit=51 fail-closed
P26-PAGINATION_SERVER-OK  http=400 code=INVALID_INPUT    服务端 raw limit=51 fail-closed
P26-TIMEOUT-OK            GatewayTimeoutError            极小 deadline 触发客户端超时
P26-DISABLED_SUPPLIER-OK  omissions=2 supplier_rows=0    停用供应商开放 PO 显式省略
P26-CROSS_COMPANY-OK      rows=3 company_b_rows=0        run 限定公司 A 时不含公司 B 数据
P26-CANCELLED_MR-OK       rows=1 cancelled=0             Cancelled MR 不出现在 open MR
P26-MISSING_FIELD-OK      http=400 code=INVALID_INPUT    缺 item_code fail-closed
P26-UNSUPPORTED_VERSION-OK http=400 code=UNSUPPORTED_VERSION  schema_version=2 fail-closed
```

## 验证与限制

- 独立门禁：Test 角色两轮均 `PASS`（第二轮对抗抽查验证 cmd 剥离、幂等、CROSS_COMPANY 断言真实有效）；Review 角色两轮（首轮 `CHANGES_REQUIRED` 提出数据清理声明不实、非幂等、跨公司 fixture 缺失三项 P2，修复后第二轮 `PASS`，另留 3 项 P3 建议不阻断）。
- 宿主机 `format-check`、`lint`、`type`、`unit` 通过（unit 29 passed）。
- Bench App 集成测试（含新增 cmd 剥离用例）通过（Ran 23 tests OK）。
- 端到端使用 Runtime `GatewayClient`（P2.5 交付物）走真实 HTTP，覆盖 PLAN P2.6 全部列项：权限拒绝、跨公司（真实第二公司 fixture）、分页（客户端+服务端）、超时、停用对象、取消单据、缺字段、版本差异。
- P2.5 Review 提出的残余风险已闭环：客户端无法校验 `authorized_scope`/`state_version`/`snapshot` 与真实 Run 一致性，本增量通过真实 `issue_run` 返回值与后续读取的对照验证了 run/correlation/tool 绑定与 scope 生效。

## 可重复人工验收

```bash
# 1) Bench console 准备数据（期望 P26-DATA-OK；重复运行安全）
# 2) 启动 bench web 于 127.0.0.1:8000
# 3) 宿主机运行（需 SYNORA_P2P_USER_PWD）：
SYNORA_P2P_USER_PWD=<pwd> uv run --python 3.14 python env/dev/p26/p26_e2e.py
# 期望输出 P26-E2E-OK，11 行 P26-*-OK
```
