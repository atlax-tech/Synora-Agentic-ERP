# n8n LAB_ONLY 对照

这是 Phase 5 的低代码对照工件，不是 Synora Runtime 依赖，也不连接真实 capability、ERP 凭证或生产数据。

## 固定边界

- 镜像固定为 `n8nio/n8n:2.37.2-arm64@sha256:c087fd5b55f790bdbe998d3e16f27882031b2178fb574abcc4039fbcd96f5029`。
- 只允许 `Manual Trigger`、`Set`、`If` 和访问 loopback recorded Gateway 的 `HTTP Request` 节点。
- 禁止 `Execute Command`、文件系统、数据库、任意外网、community node 和真实 credential。
- 导出的 JSON 不含 credential；HTTP 节点只使用 `http://127.0.0.1:18081/recorded-gateway`。
- `recorded_gateway.js` 是一次性实验 fixture，只监听 `127.0.0.1:18081`，只接受固定只读请求并返回脱敏 observation digest；它不是业务 Gateway。

## 可复跑命令

在已获得镜像后执行。以下命令用两个临时容器共享 loopback 网络命名空间；n8n 数据目录是临时文件系统，容器退出后清理：

```bash
IMAGE='n8nio/n8n:2.37.2-arm64@sha256:c087fd5b55f790bdbe998d3e16f27882031b2178fb574abcc4039fbcd96f5029'
docker run -d --rm --name synora-p5-recorded-gateway \
  --mount type=bind,src="$PWD/labs/workflow_comparison/n8n/recorded_gateway.js",dst=/tmp/recorded_gateway.js,readonly \
  --entrypoint node "$IMAGE" /tmp/recorded_gateway.js
docker run --rm --network container:synora-p5-recorded-gateway \
  --mount type=bind,src="$PWD/labs/workflow_comparison/n8n/n8n-workflow.json",dst=/tmp/n8n-workflow.json,readonly \
  --tmpfs /tmp/n8n-user:rw,noexec,nosuid,size=128m \
  --env N8N_USER_FOLDER=/tmp/n8n-user \
  --env N8N_ENCRYPTION_KEY=synora-p5-lab-only-key \
  --env N8N_COMMUNITY_PACKAGES_ENABLED=false \
  --env N8N_PUBLIC_API_DISABLED=true \
  --env N8N_TEMPLATES_ENABLED=false \
  --env N8N_DIAGNOSTICS_ENABLED=false \
  --env N8N_VERSION_NOTIFICATIONS_ENABLED=false \
  --env N8N_LOG_LEVEL=error \
  --entrypoint /bin/sh "$IMAGE" -lc '
    n8n import:workflow --input=/tmp/n8n-workflow.json
    n8n execute --id=p5-n8n-recorded-readonly-v1
    n8n audit --categories=credentials,database,nodes,instance,filesystem
  '
docker stop synora-p5-recorded-gateway
```

CLI 执行与 UI 导入使用同一 JSON；若使用 UI，仍须确认 HTTP 节点指向 loopback recorded Gateway，再执行同一固定输入。导入和执行后必须使用 n8n 官方安全审计命令检查节点、凭证和 URL；审计报告中的风险必须逐项复核，不能以“只是 lab”为理由放行。

Phase 5 证据（2026-08-27，固定 digest）：`docker pull` 退出码 0；CLI `import:workflow` 退出码 0；`execute` 退出码 0，最终节点 `Safe Result` 返回 `safe_result=recorded read succeeded`；官方全类别 `n8n audit` 退出码 0，未发现 credential/database/filesystem/community/custom node，但报告了 HTTP Request 的通用官方风险节点提示和默认实例能力，详见阶段日志。该提示不被隐藏，因此 n8n 仍只作为 `LAB_ONLY` 对照，不进入业务 Runtime。
