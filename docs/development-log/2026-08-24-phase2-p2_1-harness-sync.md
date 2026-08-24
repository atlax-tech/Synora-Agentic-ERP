# Phase 2 · P2.1 Harness 同步

日期：2026-08-24 ｜ 状态：已完成

## Proposal 与授权

- Proposal：`HU-P2.1-20260824-01`。
- 用户授权：2026-08-24，明确批准全部五个 item。
- 证据提交：`e10a4dd chore: scaffold phase 2 app and runtime`。
- 本次只同步 Harness 文档、未决项状态、证据索引、manifest 和本中文日志；不修改业务代码、依赖、Docker 卷、ERP 数据、上游 Frappe/ERPNext 或 `.workbuddy/`。

## 实际变更

- `docs/DEVELOPMENT.md` 登记 P2.1 的 `make setup`、`make format`、`make format-check`、`make lint`、`make type`、`make unit`、`make integration`、`make runtime`，并记录 Runtime 的长驻进程与 `/healthz` 健康检查语义。
- `.harness/unresolved.json` 仅将 `product-commands` 从 `UNRESOLVED` 改为 `RESOLVED`，以 `e10a4dd` 和 P2.1 工程日志为依据。
- `.harness/source-index.json` 注册 P2.1 工程日志为 `CONFIRMED` 开发证据源。
- `.harness/manifest.json` 将 P2.1 工程日志和本同步日志纳入受管文件，并刷新受影响文件指纹。
- `runtime-user-authorization`、其他未决项和 P2.2 身份授权实现保持不变。

## 实际验证

以下命令在本次同步后实际运行；Docker 和本地端口检查使用受控外部权限，未重置卷：

```text
UV_CACHE_DIR=/private/tmp/synora-p2-uv-cache UV_PYTHON_INSTALL_DIR=/private/tmp/synora-p2-python make setup       # 0；uv lock 解析 30 packages，workspace sync 成功
UV_CACHE_DIR=/private/tmp/synora-p2-uv-cache UV_PYTHON_INSTALL_DIR=/private/tmp/synora-p2-python make format-check # 0；52 files already formatted
UV_CACHE_DIR=/private/tmp/synora-p2-uv-cache UV_PYTHON_INSTALL_DIR=/private/tmp/synora-p2-python make lint         # 0；All checks passed!
UV_CACHE_DIR=/private/tmp/synora-p2-uv-cache UV_PYTHON_INSTALL_DIR=/private/tmp/synora-p2-python make type         # 0；mypy no issues in 5 source files
UV_CACHE_DIR=/private/tmp/synora-p2-uv-cache UV_PYTHON_INSTALL_DIR=/private/tmp/synora-p2-python make unit         # 0；4 passed
make integration                                                                                                   # 0；Bench FrappeTestCase Ran 1 test ... OK；site installed synora_agentic_erp
make runtime                                                                                                       # 启动成功；curl /healthz 返回目标 JSON；Ctrl-C 后停止
curl --fail http://127.0.0.1:8001/healthz                                                                         # 0；{"service":"synora-agent-runtime","status":"ok"}
curl http://127.0.0.1:8001/docs                                                                                   # 404
```

最终 Harness 与边界检查结果：

```text
python3 .agents/skills/harness-update/scripts/validate_manifest.py .          # valid=true；errors=[]；warnings=[]
python3 .agents/skills/harness-update/scripts/validate_harness_structure.py . # valid=true；checked=245；broken=0
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .  # valid=true；checked=245；broken=0
python3 .agents/skills/harness-update/scripts/detect_drift.py .               # has_drift=false；drift=[]
uv lock --python 3.14 --check                                               # 0；30 packages resolved
bash -n env/dev/scripts/dev/env.sh; git diff --check                         # 0
Frappe/ERPNext SHA 与工作树复核                                             # UPSTREAM_BASELINE_OK
README、README.zh-CN、LICENSE 边界复核                                       # LOCAL_BOUNDARIES_OK
```

两次结构校验均通过，最终 drift 为空；manifest 中的五个获批文件指纹与当前字节一致。

## 限制与后续门禁

- 本同步只解决 `product-commands`；它不代表身份授权方案已确定。
- P2.2 安全 Spike、ADR、Gateway endpoint、生产授权钩子和 ERP 读取工具仍未创建。
- 下一步仍须先完成 P2.2 安全取证并提交状态为 `PROPOSED` 的 ADR，再请求用户批准；本次 Harness 批准不包含该授权。

## 可重复人工验收

```bash
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
python3 .agents/skills/harness-update/scripts/validate_manifest.py .
python3 .agents/skills/harness-update/scripts/detect_drift.py .
make format-check
make lint
make type
make unit
make integration
make runtime
# 另一个终端：curl --fail http://127.0.0.1:8001/healthz
```

期望：Harness manifest 和结构有效、drift 为空；P2.1 项目检查通过；Runtime 返回目标 JSON；`runtime-user-authorization` 仍为 `UNRESOLVED`；Frappe/ERPNext 固定 SHA 与工作树状态不变。

## 回滚边界

如最终校验失败，只回滚本次获批的 Harness 文件和日志，并且仅在其当前字节仍匹配本次应用产生的内容时执行；不回滚 P2.1 代码提交，不触碰用户未跟踪的 `.workbuddy/`。
