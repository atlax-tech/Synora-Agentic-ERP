# n8n LAB_ONLY 对照

这是 Phase 5 的低代码对照工件，不是 Synora Runtime 依赖，也不连接真实 capability、ERP 凭证或生产数据。

## 固定边界

- 镜像固定为 `n8nio/n8n:2.37.2-arm64@sha256:c087fd5b55f790bdbe998d3e16f27882031b2178fb574abcc4039fbcd96f5029`。
- 只允许 `Manual Trigger`、`Set`、`If` 和访问 loopback recorded Gateway 的 `HTTP Request` 节点。
- 禁止 `Execute Command`、文件系统、数据库、任意外网、community node 和真实 credential。
- 导出的 JSON 不含 credential；HTTP 节点只使用 `http://127.0.0.1:18081/recorded-gateway`。

## 可复跑命令

在已获得镜像且 recorded Gateway 仅监听 loopback 后执行：

```bash
docker pull n8nio/n8n@sha256:c087fd5b55f790bdbe998d3e16f27882031b2178fb574abcc4039fbcd96f5029
docker run --rm --name synora-p5-n8n \
  --publish 127.0.0.1:5678:5678 \
  --env N8N_SECURE_COOKIE=false \
  n8nio/n8n@sha256:c087fd5b55f790bdbe998d3e16f27882031b2178fb574abcc4039fbcd96f5029
```

在 n8n UI 导入 `n8n-workflow.json`，确认 HTTP 节点仍指向 loopback recorded Gateway，再执行同一固定输入。导入和执行后必须使用 n8n 官方安全审计命令检查节点、凭证和 URL；审计失败时删除危险能力并重新导入，不能以“只是 lab”为理由放行。

本工作区当前没有该镜像，因此本轮只提交可审查的无凭证导出；`docker pull`、import、execute 和 `n8n audit` 尚未形成通过证据，不能写成已通过。
