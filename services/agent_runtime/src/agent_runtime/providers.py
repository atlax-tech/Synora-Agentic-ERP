"""Model provider abstraction (ARCHITECTURE "Model access").

本地 Ollama/OpenAI 兼容运行时为默认目标, 可选远程兼容 provider (BYOK:
用户自备 base_url 与 API key); CI 使用确定性 recorded/mock 响应。模型
输出一律视为不可信数据, 未知字段/结构 fail closed, 不得因 provider
返回内容改变工具 allowlist 或授权。

API key 脱敏约定 (用户要求):
- key 只通过环境变量注入, 不写入代码、Git、日志或数据库;
- 内部一律以 SecretStr 保存, repr/str/异常均不输出明文;
- 构造对象后立即使用, 不在模块级保存明文。
"""

import math
import os
from collections.abc import Mapping
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

MAX_PROVIDER_RESPONSE_BYTES = 2_000_000

# BYOK 环境变量 (用户自行填写, 不进入代码/Git)。
PROVIDER_BASE_URL_ENV = "SYNORA_PROVIDER_BASE_URL"
PROVIDER_API_KEY_ENV = "SYNORA_PROVIDER_API_KEY"
PROVIDER_MODEL_ENV = "SYNORA_PROVIDER_MODEL"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ProviderMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ProviderToolSpec(StrictModel):
    name: str
    description: str
    parameters: dict[str, object] = Field(default_factory=dict)


class ProviderToolCall(StrictModel):
    name: str
    arguments: str


class ProviderResponse(StrictModel):
    text: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    # 服务商返回的 token 用量 (用于成本透明; 缺失时为空)。
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ProviderError(Exception):
    """provider 调用失败 (网络/超时/非法响应/未知请求), 统一 fail closed。"""


class Provider(Protocol):
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResponse: ...


class DeterministicProvider:
    """CI/测试 provider: 从固定映射返回确定性响应, 无网络、无成本、可复跑。

    未知输入 (没有映射条目且未给默认) 抛 ProviderError, 不做猜测。
    """

    def __init__(
        self,
        responses: Mapping[str, ProviderResponse] | None = None,
        default: ProviderResponse | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._default = default

    def __repr__(self) -> str:
        return f"DeterministicProvider(responses={len(self._responses)})"

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        del tools, model, max_tokens
        if not messages:
            raise ProviderError("provider requires at least one message")
        # 以最后一条 user 消息内容作为确定性键。
        last_user = next(
            (message.content for message in reversed(messages) if message.role == "user"), ""
        )
        response = self._responses.get(last_user, self._default)
        if response is None:
            raise ProviderError("deterministic provider has no response for input")
        return response


class OpenAICompatibleProvider:
    """OpenAI 兼容 /chat/completions 客户端 (远程兼容 API, BYOK)。

    base_url 填到 OpenAI 兼容根, 通常含 /v1 (如 https://api.openai.com/v1、
    https://api.x.ai/v1、http://127.0.0.1:11434/v1); 必须是固定 HTTP(S)
    地址, 无 userinfo/query/fragment, 防止请求被重定向到任意地址或把 Key
    拼进 URL; trust_env=False 避免环境代理改写目标。响应按 OpenAI
    chat.completion 结构严格解析, 未知结构 fail closed。请求路径固定为
    base_url 拼接 /chat/completions, 不依赖 httpx 相对路径 join。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr | None = None,
        model: str = "",
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or url.path in {"", "/"}
            or url.path.endswith("/")
        ):
            raise ValueError(
                "provider base_url must be an HTTP(S) origin plus a path segment, "
                "e.g. https://api.example.com/v1"
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        self._base_url = str(url).rstrip("/")
        # 兼容两种填法: 根地址 (https://host/v1) 或完整端点 (https://host/v1/chat/completions)。
        if self._base_url.endswith("/chat/completions"):
            self._chat_url = self._base_url
        else:
            self._chat_url = f"{self._base_url}/chat/completions"
        self._api_key = api_key
        self._model = model
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key.get_secret_value()}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=False,
            # 显式禁止跟随重定向: 防止请求 (含 Bearer Key) 被 3xx 转发到非预期地址。
            follow_redirects=False,
            headers=headers,
        )

    async def __aenter__(self) -> OpenAICompatibleProvider:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResponse:
        if not messages:
            raise ProviderError("provider requires at least one message")
        payload: dict[str, object] = {
            "model": model or self._model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
        }
        # 成本护栏: 补全价格通常是输入的 5 倍, 每次调用显式限制最大输出 token,
        # 防止异常/冗长响应浪费费用; 默认不发送 (服务商默认值), 测试与工具调用
        # 必须显式传小值。
        if max_tokens is not None:
            if max_tokens < 1 or max_tokens > 8192:
                raise ValueError("provider max_tokens must be within 1..8192")
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        try:
            response = await self._client.post(self._chat_url, json=payload)
            body = response.content
        except httpx.TimeoutException as error:
            raise ProviderError("provider request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderError("provider request failed") from error
        if len(body) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderError("provider response exceeded size limit")
        if not response.is_success:
            raise ProviderError(f"provider returned HTTP {response.status_code}")

        try:
            completion = _CompletionEnvelope.model_validate_json(body)
        except (ValueError, TypeError, RecursionError) as error:
            raise ProviderError("provider returned an invalid response") from error
        if not completion.choices:
            raise ProviderError("provider returned no choices")
        if (
            max_tokens is not None
            and completion.usage is not None
            and completion.usage.completion_tokens > max_tokens
        ):
            # 成本护栏硬上限: 服务商返回的补全 token 超出请求预算视为异常
            # (服务商可能忽略 max_tokens 或极端情况下超额), fail closed 拒绝使用。
            raise ProviderError(
                "provider exceeded max_tokens budget "
                f"({completion.usage.completion_tokens} > {max_tokens})"
            )
        message = completion.choices[0].message
        return ProviderResponse(
            text=message.content or "",
            tool_calls=tuple(
                ProviderToolCall(name=call.function.name, arguments=call.function.arguments)
                for call in message.tool_calls or ()
            ),
            prompt_tokens=completion.usage.prompt_tokens if completion.usage else 0,
            completion_tokens=completion.usage.completion_tokens if completion.usage else 0,
        )


class _ToolCallFunction(StrictModel):
    name: str
    arguments: str


class _ProviderToolCall(StrictModel):
    id: str
    type: Literal["function"]
    function: _ToolCallFunction


class _AssistantMessage(StrictModel):
    role: Literal["assistant"]
    content: str | None = None
    # OpenAI 兼容响应 (如 grok) 常携带 refusal 字段 (通常为 null), 纳入严格模型。
    refusal: str | None = None
    tool_calls: tuple[_ProviderToolCall, ...] = ()


class _Choice(StrictModel):
    message: _AssistantMessage
    finish_reason: str = ""
    index: int = 0


class _Usage(BaseModel):
    # usage 是纯计费/统计元数据 (不同服务商附加字段差异大, 如 cost_in_usd_ticks、
    # num_sources_used), 不影响任何安全决策, 故忽略未知明细; 核心 envelope/message
    # 仍保持 extra="forbid" fail-closed。
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class _CompletionEnvelope(StrictModel):
    # 标准 OpenAI chat.completion 元数据字段 (白名单), 缺失时容错默认。
    id: str = ""
    object: str = ""
    created: int = 0
    model: str = ""
    usage: _Usage | None = None
    choices: tuple[_Choice, ...] = ()


def provider_from_environment(
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatibleProvider:
    """BYOK 工厂: 从环境变量读取配置构造 OpenAI 兼容 provider。

    - SYNORA_PROVIDER_BASE_URL: 必填, 纯 HTTP(S) origin (如 https://api.example.com);
    - SYNORA_PROVIDER_API_KEY: 可选, 由用户填写, 仅以 SecretStr 传入 (脱敏);
    - SYNORA_PROVIDER_MODEL: 可选, 默认模型名。

    base_url 未配置时抛 ProviderError (fail closed, 不猜测默认地址)。
    """
    base_url = os.environ.get(PROVIDER_BASE_URL_ENV, "")
    if not base_url:
        raise ProviderError(f"{PROVIDER_BASE_URL_ENV} is not configured; set it in the environment")
    api_key = os.environ.get(PROVIDER_API_KEY_ENV, "")
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=SecretStr(api_key) if api_key else None,
        model=os.environ.get(PROVIDER_MODEL_ENV, ""),
        transport=transport,
    )
