# P3.4 模型 Provider 决策包（模型运行方式与评测边界）

- 状态：`APPROVED` — 2026-08-25 用户决定：BYOK 模式（自备 API Base URL，自行填写 API Key，代码中脱敏）；不安装本地 Ollama；评测集范围批准
- 批准记录：用户要求预留 BYOK 接口（Base URL 由用户提供、API Key 用户自行填写并脱敏）；Q2 不允许安装 Ollama；Q3 评测集范围批准
- 需求：PLAN P3.4；ARCHITECTURE "Model access"（本地默认、可选远程、CI 确定性响应）；SPEC §18 `local and optional provider model baseline`

## 0. 已实现的基线（无需批准）

- `services/agent_runtime/src/agent_runtime/providers.py`：
  - **Provider 接口**：`complete(messages, tools, model) -> ProviderResponse`（typed，未知结构 fail closed）；
  - **DeterministicProvider**（CI/测试）：固定映射返回确定性响应，无网络、无成本、可复跑，未知输入 fail closed；
  - **OpenAICompatibleProvider**：OpenAI 兼容 `/v1/chat/completions` 客户端，base_url 必须是纯 HTTP(S) origin（防 SSRF），`trust_env=False`，响应严格解析，secret 不进入错误/repr。
  - **BYOK 工厂** `provider_from_environment()`：从环境变量读取 Base URL / API Key / Model。
- 评测集骨架：`agent_runtime/evaluation/`（固定 case JSON：goal + 期望确定性结果 + tags），任何候选模型用同一数据集比较。

## 1. 模型运行方式（APPROVED：BYOK 远程兼容 API）

| 决策 | 结论 |
| --- | --- |
| 运行方式 | **BYOK**：用户自备 OpenAI 兼容 API Base URL 与 API Key，通过环境变量注入（`SYNORA_PROVIDER_BASE_URL` / `SYNORA_PROVIDER_API_KEY` / `SYNORA_PROVIDER_MODEL`），代码不持有明文 |
| 本地 Ollama | 不采用（用户决定不安装） |
| 脱敏要求 | Key 只进环境变量 → `SecretStr`；repr/str/异常/日志均不输出明文；不写入 Git（`.env` 已 gitignore） |
| 接入时机 | P3.5 单 Agent 链路运行评测前由用户填写配置；未配置 base_url 时 fail closed（不猜测默认地址） |

## 2. 评测集范围（APPROVED）

- P3.5 先跑**正常场景**基线（当前 1 个真实 case：`p3-dup-risk-item-1001`，来自 P3.3 真实 ERP 数据）；
- P3.7 安全评测扩展到歧义/无权限/tool failure/恶意目标/恶意 ERP 字段/检索注入/完全无写入 8 类场景（与 PLAN P3.7 一致）；
- 评测只产生可复跑原始结果，不据此宣称生产级质量。

## 3. 批准决定（2026-08-25）

1. 模型运行方式 = **BYOK 远程 OpenAI 兼容 API**（用户自备 Base URL / 自填 API Key，代码脱敏）；
2. 不安装本地 Ollama；
3. 评测集范围批准（§2：先正常场景，后扩展 8 类安全场景）。

## 4. 批准后动作

- BYOK 工厂 `provider_from_environment()` 已实现（环境变量读取、SecretStr 脱敏、未配置 fail closed）；
- 脱敏配置说明写入 `docs/DEVELOPMENT.md` 与 `env/dev/.env.example`；
- P3.5 单 Agent 链路评测前，用户在环境变量中填写 Base URL / API Key 后运行。

## 附：事实来源

- ARCHITECTURE §Technology Selection：`Model access | Provider interface; local Ollama/OpenAI-compatible runtime by default, optional remote compatible providers`；
- PLAN §7 `model-selection`：同一评测集比较；涉及远程数据、成本或安全边界时交用户决定。
