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

_OPENAI_CHAT_PATH = "/v1/chat/completions"
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


class ProviderError(Exception):
    """provider 调用失败 (网络/超时/非法响应/未知请求), 统一 fail closed。"""


class Provider(Protocol):
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
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
    ) -> ProviderResponse:
        del tools, model
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
    """OpenAI 兼容 /chat/completions 客户端 (本地 Ollama 默认, 远程可选)。

    base_url 必须是固定 HTTP(S) origin (无 userinfo/query/fragment), 防止
    请求被重定向到任意地址; trust_env=False 避免环境代理改写目标。
    响应按 OpenAI chat.completion 结构严格解析, 未知结构 fail closed。
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
            or url.path not in {"", "/"}
        ):
            raise ValueError("provider base_url must be a plain HTTP(S) origin")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        self._base_url = str(url)
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
    ) -> ProviderResponse:
        if not messages:
            raise ProviderError("provider requires at least one message")
        payload: dict[str, object] = {
            "model": model or self._model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
        }
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
            response = await self._client.post(_OPENAI_CHAT_PATH, json=payload)
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
        message = completion.choices[0].message
        return ProviderResponse(
            text=message.content or "",
            tool_calls=tuple(
                ProviderToolCall(name=call.function.name, arguments=call.function.arguments)
                for call in message.tool_calls or ()
            ),
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
    tool_calls: tuple[_ProviderToolCall, ...] = ()


class _Choice(StrictModel):
    message: _AssistantMessage


class _CompletionEnvelope(StrictModel):
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
