# P3.4 模型 Provider 决策包（模型运行方式与评测边界）

- 状态：`PROPOSED` — 待用户批准；P3.4 接口/CI/评测集已实现，真实模型接入需批准
- 日期：2026-08-25
- 需求：PLAN P3.4；ARCHITECTURE "Model access"（本地默认、可选远程、CI 确定性响应）；SPEC §18 `local and optional provider model baseline`

## 0. 已实现的基线（无需批准）

- `services/agent_runtime/src/agent_runtime/providers.py`：
  - **Provider 接口**：`complete(messages, tools, model) -> ProviderResponse`（typed，未知结构 fail closed）；
  - **DeterministicProvider**（CI/测试）：固定映射返回确定性响应，无网络、无成本、可复跑，未知输入 fail closed；
  - **OpenAICompatibleProvider**：OpenAI 兼容 `/v1/chat/completions` 客户端，base_url 必须是纯 HTTP(S) origin（防 SSRF），`trust_env=False`，响应严格解析，secret 不进入错误/repr。
- 评测集骨架：`agent_runtime/evaluation/`（固定 case JSON：goal + 期望确定性结果 + tags），任何候选模型用同一数据集比较。

## 1. 模型运行方式（PROPOSED，需批准）

| 选项 | 说明 | 数据与成本 | 风险 |
| --- | --- | --- | --- |
| **A. 本地 Ollama/OpenAI 兼容（推荐）** | 本机 Ollama 服务 + 本地模型（如 qwen/llama 系）；Runtime 通过 OpenAI 兼容端点调用 `127.0.0.1:11434` | ERP 数据不出本机；无 API 成本；需安装 Ollama 并拉取模型（约数 GB 磁盘） | 本机资源占用；本地模型能力可能弱于远程大模型 |
| B. 远程 OpenAI 兼容 API | 配置远程 base_url + api_key（如 OpenAI 官方） | 目标文本/检索内容出域；按 token 计费 | 数据出域、成本不可控、依赖外部服务可用性 |
| C. 暂不接真实模型 | P3.5 先用 DeterministicProvider 跑通单 Agent 只读链路，模型评测后置 | 无 | 无法产生真实模型基线；P3.5 计划环节的"可解释结果"只能用确定性文本 |

## 2. 评测集范围（PROPOSED）

- P3.5 先跑**正常场景**基线（当前 1 个真实 case：`p3-dup-risk-item-1001`，来自 P3.3 真实 ERP 数据）；
- P3.7 安全评测扩展到歧义/无权限/tool failure/恶意目标/恶意 ERP 字段/检索注入/完全无写入 8 类场景（与 PLAN P3.7 一致）；
- 评测只产生可复跑原始结果，不据此宣称生产级质量。

## 3. 需要用户批准的决定项

1. 模型运行方式：**A 本地 Ollama（推荐）/ B 远程 API / C 暂不接真实模型**
2. 若选 A：是否允许我安装/启动本机 Ollama 并拉取一个本地模型（磁盘数 GB）？
3. 若选 B：提供 base_url 与 api_key 配置方式（env 变量），并确认接受数据出域与成本
4. 评测集范围按 §2 执行？

## 4. 批准后动作

- A：安装/启动 Ollama → 拉取选定模型 → 用评测集跑真实模型基线 → 记录原始结果到开发日志；
- B：配置 env → 同一评测集跑基线 → 记录成本与结果；
- C：P3.5 用 DeterministicProvider 完成单 Agent 链路，模型评测挂起至批准后。

## 附：事实来源

- ARCHITECTURE §Technology Selection：`Model access | Provider interface; local Ollama/OpenAI-compatible runtime by default, optional remote compatible providers`；
- PLAN §7 `model-selection`：同一评测集比较；涉及远程数据、成本或安全边界时交用户决定。
