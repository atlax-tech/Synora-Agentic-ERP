# Phase 2 · P2.1 最小工程骨架

日期：2026-08-24 ｜ 状态：已完成，等待 Harness 同步提案批准

## 结果

P2.1 建立了可被现有 Bench 环境消费的最小工程边界：

- 根部 Frappe App `synora_agentic_erp` 使用 `Atlax-Tech`、MIT、内部版本 `0.0.1`，未填写虚构联系邮箱，也未修改 README 或许可证。
- `services/agent_runtime` 是独立 Python 项目，只暴露 `GET /healthz`，返回 `{"service":"synora-agent-runtime","status":"ok"}`；运行时代码没有 Frappe、ERPNext 或 MariaDB/MySQL 驱动导入。
- 根 `pyproject.toml`、工作区 `uv.lock` 与 `.python-version` 固定 Python `>=3.14,<3.15`；Runtime 与开发工具按 P2.1 计划锁定版本。
- 根 `Makefile` 提供 `setup`、`format`、`format-check`、`lint`、`type`、`unit`、`integration`、`runtime` 八个真实命令。
- Bench 只读挂载仓库 `/workspace/synora:ro`，并用 tmpfs 屏蔽容器内的 `env/dev`（因此不暴露 `env/dev/.env` 中的凭据）；通过 `bench get-app --soft-link` 幂等链接并安装到现有 `dev.localhost`，脚本同时校验 soft-link 目标和站点真实 installed-app 列表。为适配 Frappe developer mode，模块包在仓库中预先存在，避免安装钩子向只读挂载写入。
- 增加 Runtime 健康测试、Runtime 架构边界测试与 Frappe 原生 App 测试。

## 为什么这样做

这是 `docs/PLAN.md` P2.1 的最小可验证入口：先让 App、Runtime、锁文件、真实产品命令和 Bench 集成路径可重复，再进入 P2.2 身份授权安全 Spike。Runtime 当前不接触 ERP、数据库、用户身份或业务写入，保持 Frappe/ERPNext 为上游事务系统并避免提前实现 P2.3 Gateway。

## 实际验证

以下命令均在 2026-08-24 真实运行；需要访问 PyPI 或 Docker 的命令使用受控的外部权限，未重置卷或重建 Phase 1 数据：

```text
make setup                         # 0；Python 3.14，uv lock 解析 30 packages，workspace sync 成功
make format                        # 0；52 files left unchanged
make format-check                  # 0；52 files already formatted
make lint                          # 0；All checks passed!
make type                          # 0；mypy Success: no issues found in 5 source files
make unit                          # 0；4 passed
make integration                   # 0；FrappeTestCase 1 test，Ran 1 test，OK
bash env/dev/scripts/dev/env.sh up bench   # 0；现有四服务健康，bench 以新只读挂载重建
bash env/dev/scripts/dev/env.sh bash 'set -euo pipefail; test -f /workspace/synora/synora_agentic_erp/hooks.py; test ! -e /workspace/synora/env/dev/.env; test ! -e /workspace/synora/env/dev/docker-compose.yml; test "$(readlink /home/frappe/bench/apps/synora_agentic_erp)" = /workspace/synora; echo REPO_LAYOUT_OK; echo SECRET_PATHS_MASKED' # 0；App 布局、soft-link 与凭据路径隔离
bash env/dev/scripts/dev/env.sh app-install # 0；校验 soft-link、install-app、migrate 成功，站点列出 synora_agentic_erp
make runtime                       # 启动成功；随后 Ctrl-C 正常关闭（长驻命令不以终止码作为门禁）
curl http://127.0.0.1:8001/healthz # 200；目标 JSON
curl http://127.0.0.1:8001/docs    # 404；未启用额外 HTTP 路由
UV_CACHE_DIR=/private/tmp/synora-p2-uv-cache uv run --python 3.13 python -c 'print("unexpected")' # 2；明确拒绝不符合项目约束的 Python 3.13
bash -n env/dev/scripts/dev/env.sh  # 0
git diff --check                   # 0
uv lock --python 3.14 --check      # 0
```

安装首次尝试正确地以 `Errno 30 Read-only file system` 暴露 Frappe 在 developer mode 下创建模块目录的要求；预创建标准模块包后，独立 Review 又发现不能把全局 `sites/apps.txt` 当作站点已安装状态。改为读取 `bench --site dev.localhost list-apps`、校验 soft-link 目标、用 tmpfs 屏蔽 `env/dev`，并在 `app-test` 结束时用 trap 将 `allow_tests` 恢复为 `0` 后，`install-app` 真正执行并列出 `synora_agentic_erp`，随后 `make integration` 重新通过。这一取证确认了只读挂载、站点安装状态、测试模式恢复和敏感配置隔离，而不是放开仓库写权限。

固定上游复核仍为：Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`、ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`，两仓 `git status --porcelain` 均为空。

## 局限与未决项

- P2.2 身份授权 Spike 尚未开始；登录态 cookie、API key/OAuth、`auth_hooks`、Run 绑定和 capability 方案仍待验证与用户批准。
- 尚未建立 Gateway endpoint、Agent Run 业务记录、只读 ERP 工具或生产授权钩子。
- Harness 尚未写入同步变更；下一步只生成 `harness-update` 文件级只读 proposal，预计涉及 `docs/DEVELOPMENT.md`、`product-commands` 未决项、source index/manifest 和同步日志，需用户明确批准后另行提交。
- `.workbuddy/` 为用户未跟踪内容，未纳入本增量。

## 可重复人工验收

```bash
make setup
make format-check
make lint
make type
make unit
bash env/dev/scripts/dev/env.sh app-test
make runtime
# 另一个终端：curl --fail http://127.0.0.1:8001/healthz
```

期望：所有门禁退出 0；Bench 测试输出 `Ran 1 test ... OK` 且 `bench --site dev.localhost list-apps` 含 `synora_agentic_erp`；Runtime 返回目标 JSON；Frappe/ERPNext 上游仍为固定 SHA 且无工作树改动。

相关提交：本日志随 `chore: scaffold phase 2 app and runtime` 增量提交。
