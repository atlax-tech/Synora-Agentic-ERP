# Phase 2 出口修正增量（超时语义诚实化 + Harness 语义一致化 + 三项建议）

- 日期：2026-08-25
- 需求：PLAN §11 Phase 2 出口；用户指出的两个阻塞问题与三个建议项。

## 问题 1：Gateway 超时语义诚实化

现状核查确认：`registry.py` 的 `timeout_ms` 在工具 handler **返回后**才比较耗时（`monotonic()` 差值），若 ERP 调用永久卡住不会运行到超时判断；Runtime 的 10 秒 HTTP deadline 只保护调用方，不中断服务端工作。修复：

- `registry.py`：`timeout_ms` 字段与 dispatch 计时处加注释，明确这是 **post-hoc 耗时分类阈值**（不中断执行）；错误消息改为 `tool execution exceeded its post-hoc timeout classification budget`。
- `SPEC.md` §9：新增 Tool timeout semantics 段落，明确 post-hoc 分类语义、Runtime HTTP deadline 兜底、以及真正执行截止需进程隔离并留待 Phase 4（写操作阶段）。
- 测试：`test_registered_tool_enforces_timeout` 更名为 `test_registered_tool_classifies_post_hoc_timeout`，补充 `retryable=True` 断言。
- 决策记录：不采用强制中断（同步 Python 线程不可安全杀除、signal 受 WSGI 限制、进程隔离成本高），只读场景 post-hoc + Runtime 兜底可接受；写操作阶段再引入进程隔离的执行截止。

## 问题 2：Harness 语义状态一致化

机器指纹检查无法发现"内容已过时"的语义漂移，逐文件修复：

- `.harness/unresolved.json`：`runtime-user-authorization` 由 `UNRESOLVED` → `RESOLVED`（ADR-0003 于 2026-08-25 批准，P2.3/P2.6 实现并验证），补 resolution 字段。
- `.harness/source-index.json`：登记 11 条 Phase 2 新证据（P2.2-P2.6 日志、出口报告、身份 Spike 取证、ADR-0003、gateway.py、p26 数据/端到端脚本），7 → 18 条。
- `README.md` + `README.zh-CN.md`：项目状态由「Phase 0 / 运行时未实现」更新为「Phase 0-2 完成（治理基线 + 固定 ERP 基线 + 只读 Gateway + Runtime 客户端）」，同步产品安装段、技术方向表、Roadmap 勾选（Phase 1/2）、贡献段、FAQ「现在能运行 Synora 吗」。
- `docs/security/phase2-p2_2-identity-authorization-spike.md`：状态由 `P2.3 IMPLEMENTATION IN PROGRESS` → `PHASE 2 COMPLETE`。

## 建议 1：真实跨公司权限拒绝测试

新增仅可访问公司 A 的用户 `synora-p26-aonly@dev.localhost`（Purchase User + User Permission 公司范围）。E2E 实测：该用户 `issue_run(company=A)` 成功并可读 item.lookup（`AONLY_COMPANY_A_ACCESS-OK`）；`issue_run(company=B)` 在**发行阶段**即被 `SCOPE_DENIED (403)` 拒绝（`AONLY_COMPANY_B_DENIED-OK`，User Permission 经 `frappe.get_list` 生效）。数据准备脚本支持该用户的幂等创建与清理。

## 建议 2：无效 capability 安全事件日志策略

未解析出 Run 的失败请求（无效/过期/猜测 capability、未知工具、畸形契约）无法形成绑定 Run 的 Gateway Audit。新增 `api.py::_log_security_event`：按安全事件日志策略记录脱敏事件（仅错误码、correlation、来源 IP，不含 capability 与请求体），`frappe.log_error` 落库。策略写入 `SPEC.md` §14。回归测试 `test_unresolvable_run_failure_is_logged_as_security_event` 断言记录且不泄漏伪造 capability。

## 建议 3：服务端异常诊断证据

响应侧继续统一脱敏为 `ERP_ERROR`，但 `execute` 的 `except Exception` 分支新增 `frappe.log_error` 记录真实异常与 run/correlation 上下文（保留 traceback），避免运维日志只剩统一错误码。回归测试扩展 `test_unexpected_tool_failure_is_sanitized_and_audited`：断言响应脱敏且诊断日志保留内部信息。

## 验证

- 宿主机 `format-check`/`lint`/`type`/`unit` 通过（unit 29 passed）。
- Bench 集成测试 **Ran 24 tests OK**（新增安全事件日志用例）。
- 真实 HTTP E2E **13/13 通过**（`P26-E2E-OK`，含 AONLY 跨公司两场景）。
- `detect_drift.py` 无漂移（README/unresolved/source-index 均已同步）。

## 限制

- 跨公司拒绝实测发生在发行阶段（SCOPE_DENIED）。`security.py` 中 `recheck_run_scope` 的公司可见性检查（`frappe.get_list("Company", ..., user=run.initiator)`）未被独立用例覆盖——Accountant 用例验证的是 DocType read 权限拒绝（Purchase Order 无读权限），与公司可见性路径不同；发行阶段先拒使该路径在当前环境不可达，属已知限制，写操作阶段需单独覆盖。
- 安全事件日志与诊断日志均落在 Frappe Error Log（无专用安全 Doctype）；若后续需要独立安全事件存储/告警，属运维演进项。
- `TIMEOUT` 在错误契约中标记 retryable，与 post-hoc 分类语义叠加：服务端可能已完成执行（结果不可达），Phase 4 写操作阶段需重审 retryable 与重试语义（配合执行截止与幂等）。

## 可重复人工验收

```bash
make unit && make integration          # 29 passed / 24 tests OK
# bench web 运行后：
SYNORA_P2P_USER_PWD=<pwd> uv run --python 3.14 python env/dev/p26/p26_e2e.py  # 13 行 P26-*-OK
python3 .agents/skills/harness-check/scripts/detect_drift.py .                # has_drift=false
```
