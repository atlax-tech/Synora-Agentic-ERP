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
import ssl
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

MAX_PROVIDER_RESPONSE_BYTES = 2_000_000

# BYOK 环境变量 (用户自行填写, 不进入代码/Git)。
PROVIDER_BASE_URL_ENV = "SYNORA_PROVIDER_BASE_URL"
PROVIDER_API_KEY_ENV = "SYNORA_PROVIDER_API_KEY"
PROVIDER_MODEL_ENV = "SYNORA_PROVIDER_MODEL"
PROVIDER_REASONING_EFFORT_ENV = "SYNORA_PROVIDER_REASONING_EFFORT"
PROVIDER_THINKING_ENV = "SYNORA_PROVIDER_THINKING"
PROVIDER_PROXY_ENV = "SYNORA_PROVIDER_PROXY"
PROVIDER_MAX_OUTPUT_TOKENS_ENV = "SYNORA_PROVIDER_MAX_OUTPUT_TOKENS"
PROVIDER_FALLBACK_BASE_URL_ENV = "SYNORA_PROVIDER_FALLBACK_BASE_URL"
PROVIDER_FALLBACK_API_KEY_ENV = "SYNORA_PROVIDER_FALLBACK_API_KEY"
PROVIDER_FALLBACK_MODEL_ENV = "SYNORA_PROVIDER_FALLBACK_MODEL"
PROVIDER_FALLBACK_REASONING_EFFORT_ENV = "SYNORA_PROVIDER_FALLBACK_REASONING_EFFORT"
PROVIDER_FALLBACK_THINKING_ENV = "SYNORA_PROVIDER_FALLBACK_THINKING"
PROVIDER_FALLBACK_PROXY_ENV = "SYNORA_PROVIDER_FALLBACK_PROXY"
PROVIDER_LOCAL_BASE_URL_ENV = "SYNORA_PROVIDER_LOCAL_BASE_URL"
PROVIDER_LOCAL_SMALL_MODEL_ENV = "SYNORA_PROVIDER_LOCAL_SMALL_MODEL"
PROVIDER_LOCAL_LARGE_MODEL_ENV = "SYNORA_PROVIDER_LOCAL_LARGE_MODEL"
PROVIDER_MAX_OUTPUT_TOKENS = 1024
PROVIDER_MAX_OUTPUT_TOKEN_LIMIT = 8192
GLM_4_7_FLASH_MODEL = "glm-4.7-flash"
DEFAULT_PROVIDER_MODEL = GLM_4_7_FLASH_MODEL
GLM_4_7_FLASH_DEFAULT_MAX_OUTPUT_TOKENS = 65_536
GLM_4_7_FLASH_MAX_OUTPUT_TOKENS = 131_072
_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
_THINKING_MODES = {"enabled", "disabled"}
_FAILOVER_FAILURE_CODES = frozenset(
    {"RATE_LIMITED", "UPSTREAM_UNAVAILABLE", "TIMEOUT", "TRANSPORT_ERROR"}
)
_NEXT_PROVIDER_FAILURE_CODES = _FAILOVER_FAILURE_CODES | {
    "RESPONSE_SCHEMA",
    "RESPONSE_NO_CHOICES",
    "RESPONSE_CONTENT_MISSING",
    "USAGE_MISSING",
}
ProviderResponseFormat = Literal["json_object"]


def _output_token_limits(model: str | None) -> tuple[int, int]:
    if (model or "").strip().lower() == GLM_4_7_FLASH_MODEL:
        return GLM_4_7_FLASH_DEFAULT_MAX_OUTPUT_TOKENS, GLM_4_7_FLASH_MAX_OUTPUT_TOKENS
    return PROVIDER_MAX_OUTPUT_TOKENS, PROVIDER_MAX_OUTPUT_TOKEN_LIMIT


def provider_max_output_token_limit(model: str | None = None) -> int:
    """Return the model-specific hard ceiling for one provider request."""
    return _output_token_limits(model)[1]


def provider_max_output_tokens(environ: Mapping[str, str] | None = None) -> int:
    """Return the bounded output cap shared by real provider call sites.

    The default follows the configured model's quality policy. One environment
    key can tune the effective value in either direction, but never above the
    model-specific hard ceiling.
    """
    values = os.environ if environ is None else environ
    default, hard_limit = _output_token_limits(values.get(PROVIDER_MODEL_ENV))
    raw = values.get(PROVIDER_MAX_OUTPUT_TOKENS_ENV, "")
    if not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("provider max output tokens must be an integer") from error
    if not 1 <= value <= hard_limit:
        raise ValueError(f"provider max output tokens must be within 1..{hard_limit}")
    return value


def provider_thinking_mode(environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve the vendor-neutral thinking mode for the configured model."""
    values = os.environ if environ is None else environ
    return _provider_thinking_mode(
        values,
        model=values.get(PROVIDER_MODEL_ENV, ""),
        thinking_env=PROVIDER_THINKING_ENV,
    )


def _provider_thinking_mode(
    values: Mapping[str, str], *, model: str, thinking_env: str
) -> str | None:
    raw = values.get(thinking_env, "").strip()
    if raw:
        if raw not in _THINKING_MODES:
            raise ValueError("provider thinking must be enabled or disabled")
        return raw
    if model.strip().lower() == GLM_4_7_FLASH_MODEL:
        return "enabled"
    return None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ProviderToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    name: str
    arguments: str

    @property
    def provider_tool_call_id(self) -> str:
        """Descriptive alias for the provider's wire-level ``id`` field."""
        return self.id


class ProviderMessage(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def validate_role_fields(self) -> ProviderMessage:
        if self.role in {"system", "user"}:
            if not self.content or self.tool_calls or self.tool_call_id or self.name:
                raise ValueError("system and user messages only contain content")
        elif self.role == "assistant":
            if not self.content and not self.tool_calls:
                raise ValueError("assistant message requires content or tool calls")
            if self.tool_call_id or self.name:
                raise ValueError("assistant message cannot contain tool result fields")
        elif not self.content or not self.tool_call_id or not self.name or self.tool_calls:
            raise ValueError("tool message requires name, call id, and content")
        return self


class ProviderToolSpec(StrictModel):
    name: str
    description: str
    parameters: dict[str, object] = Field(default_factory=dict)


class ProviderResponse(StrictModel):
    text: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    # 服务商返回的 token 用量 (用于成本透明; 缺失时为空)。
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_content_present: bool = False


class ProviderError(Exception):
    """provider 调用失败 (网络/超时/非法响应/未知请求), 统一 fail closed。"""

    def __init__(
        self,
        message: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        budget_code: Literal["TOKEN_BUDGET"] | None = None,
        failure_code: str = "PROVIDER_ERROR",
    ) -> None:
        super().__init__(message)
        # 即使结果因预算门禁被拒绝, 已观测的 usage 仍需进入证据, 便于审计。
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        # A provider can fail after reporting usage because its response itself
        # violated the bounded output contract. Preserve that classification so
        # the kernel does not turn an auditable budget stop into MODEL_ERROR.
        self.budget_code = budget_code
        self.failure_code = failure_code


class Provider(Protocol):
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse: ...


def _serialize_tool_call(tool_call: ProviderToolCall) -> dict[str, object]:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": tool_call.arguments,
        },
    }


def _serialize_message(message: ProviderMessage) -> dict[str, object]:
    """Serialize the internal message contract to OpenAI chat wire shape."""
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "content": message.content,
        }
    payload: dict[str, object] = {"role": message.role, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        payload["content"] = message.content or None
        payload["tool_calls"] = [_serialize_tool_call(call) for call in message.tool_calls]
    return payload


class DeterministicProvider:
    """CI/测试 provider: 从固定映射返回确定性响应, 无网络、无成本、可复跑。

    未知输入 (没有映射条目且未给默认) 抛 ProviderError, 不做猜测。
    """

    def __init__(
        self,
        responses: Mapping[str, ProviderResponse] | Sequence[ProviderResponse] | None = None,
        default: ProviderResponse | None = None,
        scripted_responses: Sequence[ProviderResponse] | None = None,
    ) -> None:
        if isinstance(responses, Mapping):
            self._responses = dict(responses)
            response_sequence: Sequence[ProviderResponse] = ()
        else:
            self._responses = {}
            response_sequence = responses or ()
        if scripted_responses is not None and responses is not None:
            raise ValueError("provide either positional or named scripted responses, not both")
        self._scripted = list(scripted_responses or response_sequence)
        self._default = default

    def __repr__(self) -> str:
        return (
            f"DeterministicProvider(responses={len(self._responses)}, "
            f"scripted={len(self._scripted)})"
        )

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        del tools, model, max_tokens, response_format
        if not messages:
            raise ProviderError("provider requires at least one message")
        if self._scripted:
            return self._scripted.pop(0)
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
        reasoning_effort: str | None = None,
        thinking: str | None = None,
        proxy: str | None = None,
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
        if reasoning_effort is not None and reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError("provider reasoning_effort must be low, medium, high, or xhigh")
        if thinking is not None and thinking not in _THINKING_MODES:
            raise ValueError("provider thinking must be enabled or disabled")
        proxy_url = httpx.URL(proxy) if proxy else None
        if proxy_url is not None and (
            proxy_url.scheme not in {"http", "https"}
            or not proxy_url.host
            or proxy_url.userinfo
            or proxy_url.query
            or proxy_url.fragment
            or proxy_url.path not in {"", "/"}
        ):
            raise ValueError(
                "provider proxy must be an HTTP(S) address without credentials, path, "
                "query, or fragment"
            )
        self._base_url = str(url).rstrip("/")
        # 兼容两种填法: 根地址 (https://host/v1) 或完整端点 (https://host/v1/chat/completions)。
        if self._base_url.endswith("/chat/completions"):
            self._chat_url = self._base_url
        else:
            self._chat_url = f"{self._base_url}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._thinking = thinking
        self._proxy = str(proxy_url).rstrip("/") if proxy_url else None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key.get_secret_value()}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=False,
            proxy=self._proxy,
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
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        if not messages:
            raise ProviderError(
                "provider requires at least one message", failure_code="INVALID_REQUEST"
            )
        requested_model = model or self._model
        payload: dict[str, object] = {
            "model": requested_model,
            "messages": [_serialize_message(message) for message in messages],
            "stream": False,
        }
        # max_tokens 是生成侧上限, 不是包含 prompt 的 total_tokens 上限。
        if max_tokens is not None:
            hard_limit = provider_max_output_token_limit(requested_model)
            if max_tokens < 1 or max_tokens > hard_limit:
                raise ValueError(f"provider max_tokens must be within 1..{hard_limit}")
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = {"type": response_format}
        if self._thinking is not None:
            payload["thinking"] = {"type": self._thinking}
        if self._reasoning_effort is not None and self._thinking != "disabled":
            # Grok reasoning models default to high effort; simple plan explanations
            # opt in to a lower, explicit effort without weakening output validation.
            payload["reasoning_effort"] = self._reasoning_effort
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
            raise ProviderError("provider request timed out", failure_code="TIMEOUT") from error
        except (httpx.HTTPError, ssl.SSLError) as error:
            raise ProviderError(
                "provider transport failed", failure_code="TRANSPORT_ERROR"
            ) from error
        if len(body) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderError(
                "provider response exceeded size limit", failure_code="RESPONSE_TOO_LARGE"
            )
        if not response.is_success:
            if response.status_code == 429:
                failure_code = "RATE_LIMITED"
            elif response.status_code == 408:
                failure_code = "TIMEOUT"
            elif 500 <= response.status_code <= 599:
                failure_code = "UPSTREAM_UNAVAILABLE"
            else:
                failure_code = "HTTP_ERROR"
            raise ProviderError(
                f"provider returned HTTP {response.status_code}", failure_code=failure_code
            )

        try:
            completion = _CompletionEnvelope.model_validate_json(body)
        except (ValueError, TypeError, RecursionError) as error:
            raise ProviderError(
                "provider returned an invalid response", failure_code="RESPONSE_SCHEMA"
            ) from error
        if not completion.choices:
            raise ProviderError("provider returned no choices", failure_code="RESPONSE_NO_CHOICES")
        if max_tokens is not None and completion.usage is None:
            # 没有 usage 就无法证明服务商遵守输出预算; 宁可回退, 也不接受
            # 未验证的真实模型结果。请求参数仍是服务商侧的首要成本护栏。
            raise ProviderError(
                "provider omitted usage for budgeted response",
                budget_code="TOKEN_BUDGET",
                failure_code="USAGE_MISSING",
            )
        usage = completion.usage
        reasoning_tokens = (
            usage.completion_tokens_details.reasoning_tokens
            if usage and usage.completion_tokens_details
            else 0
        )
        completion_tokens = usage.completion_tokens if usage else 0
        if usage is not None:
            if (
                usage.prompt_tokens < 0
                or completion_tokens < 0
                or reasoning_tokens < 0
                or usage.total_tokens < 0
            ):
                raise ProviderError(
                    "provider returned invalid token usage",
                    budget_code="TOKEN_BUDGET",
                    failure_code="USAGE_INVALID",
                )
            # Some providers report reasoning_tokens as a subset of completion_tokens
            # (e.g. GLM), while others report it separately. total - prompt is the
            # provider-neutral billed output count and avoids double-counting either form.
            billed_output_tokens = usage.total_tokens - usage.prompt_tokens
            if billed_output_tokens < 0:
                raise ProviderError(
                    "provider returned invalid token usage",
                    budget_code="TOKEN_BUDGET",
                    failure_code="USAGE_INVALID",
                )
            if max_tokens is not None and billed_output_tokens > max_tokens:
                # 成本护栏覆盖服务商报告的完整输出 token; 不假设 reasoning token
                # 在不同服务商的 completion_tokens 中采用同一种统计方式。
                raise ProviderError(
                    f"provider exceeded max_tokens budget ({billed_output_tokens} > {max_tokens})",
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    budget_code="TOKEN_BUDGET",
                    failure_code="BUDGET_EXCEEDED",
                )
        message = completion.choices[0].message
        tool_calls = tuple(
            ProviderToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in message.tool_calls or ()
        )
        if not message.content and not tool_calls:
            raise ProviderError(
                "provider returned no final content", failure_code="RESPONSE_CONTENT_MISSING"
            )
        return ProviderResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_content_present=message.reasoning_content is not None,
        )


class FailoverProvider:
    """Try configured providers once each, in priority order."""

    def __init__(self, primary: Provider, fallback: Provider, *others: Provider) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallbacks = (fallback, *others)
        self._last_successful_index = -1

    def __repr__(self) -> str:
        return f"FailoverProvider(fallback_configured={self._fallback is not None})"

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        self._last_successful_index = -1
        try:
            response = await self._primary.complete(
                messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            self._last_successful_index = 0
            return response
        except ProviderError as primary_error:
            if primary_error.failure_code not in _FAILOVER_FAILURE_CODES:
                raise
            errors = [primary_error]
            for index, fallback in enumerate(self._fallbacks, start=1):
                fallback_max_tokens = max_tokens
                if isinstance(fallback, OpenAICompatibleProvider) and max_tokens is not None:
                    fallback_max_tokens = min(
                        max_tokens,
                        provider_max_output_token_limit(fallback._model),
                    )
                try:
                    response = await fallback.complete(
                        messages,
                        tools=tools,
                        model=None,
                        max_tokens=fallback_max_tokens,
                        response_format=response_format,
                    )
                    self._last_successful_index = index
                    return response
                except ProviderError as error:
                    errors.append(error)
                    if error.failure_code not in _NEXT_PROVIDER_FAILURE_CODES:
                        break
            last_error = errors[-1]
            raise ProviderError(
                "all configured providers failed",
                prompt_tokens=sum(error.prompt_tokens for error in errors),
                completion_tokens=sum(error.completion_tokens for error in errors),
                reasoning_tokens=sum(error.reasoning_tokens for error in errors),
                budget_code=next(
                    (error.budget_code for error in reversed(errors) if error.budget_code), None
                ),
                failure_code=last_error.failure_code,
            ) from last_error

    async def complete_next(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        """Try exactly the next provider after a valid but insufficient response."""
        next_index = self._last_successful_index + 1
        if next_index < 1 or next_index > len(self._fallbacks):
            raise ProviderError("no larger provider is available", failure_code="NO_FALLBACK")
        provider = self._fallbacks[next_index - 1]
        effective_max_tokens = max_tokens
        if isinstance(provider, OpenAICompatibleProvider) and max_tokens is not None:
            effective_max_tokens = min(max_tokens, provider_max_output_token_limit(provider._model))
        response = await provider.complete(
            messages,
            tools=tools,
            model=None,
            max_tokens=effective_max_tokens,
            response_format=response_format,
        )
        self._last_successful_index = next_index
        return response

    async def aclose(self) -> None:
        errors: list[Exception] = []
        for provider in (self._primary, *self._fallbacks):
            close = getattr(provider, "aclose", None)
            if not callable(close):
                continue
            try:
                await close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]


class _ToolCallFunction(StrictModel):
    name: str
    arguments: str


class _ProviderToolCall(StrictModel):
    id: str
    type: Literal["function"]
    function: _ToolCallFunction


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class _AssistantMessage(_WireModel):
    role: Literal["assistant"]
    content: str | None = None
    reasoning_content: str | None = None
    # OpenAI 兼容响应 (如 grok) 常携带 refusal 字段 (通常为 null), 纳入严格模型。
    refusal: str | None = None
    tool_calls: tuple[_ProviderToolCall, ...] = ()


class _Choice(_WireModel):
    message: _AssistantMessage
    finish_reason: str = ""
    index: int = 0


class _CompletionTokenDetails(BaseModel):
    # xAI 将 reasoning token 单列, 但其他兼容服务商可能不提供该字段。
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    reasoning_tokens: int = 0


class _Usage(BaseModel):
    # usage 是纯计费/统计元数据 (不同服务商附加字段差异大, 如 cost_in_usd_ticks、
    # num_sources_used), 不影响任何安全决策, 故忽略未知明细; 核心 envelope/message
    # 仍保持 extra="forbid" fail-closed。
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    completion_tokens_details: _CompletionTokenDetails | None = None


class _CompletionEnvelope(_WireModel):
    # 标准 OpenAI chat.completion 元数据字段 (白名单), 缺失时容错默认。
    id: str = ""
    request_id: str = ""
    object: str = ""
    created: int = 0
    model: str = ""
    usage: _Usage | None = None
    choices: tuple[_Choice, ...] = ()


def provider_from_environment(
    transport: httpx.AsyncBaseTransport | None = None,
) -> Provider:
    """BYOK 工厂: 从环境变量读取配置构造 OpenAI 兼容 provider。

    - SYNORA_PROVIDER_BASE_URL: 必填, HTTP(S) origin 加路径段 (如 https://api.example.com/v1);
    - SYNORA_PROVIDER_API_KEY: 可选, 由用户填写, 仅以 SecretStr 传入 (脱敏);
    - SYNORA_PROVIDER_MODEL: 可选, 默认使用 ``glm-4.7-flash``;
    - SYNORA_PROVIDER_THINKING: 可选, enabled 或 disabled (智谱 GLM 可用);
    - SYNORA_PROVIDER_PROXY: 可选, 显式 HTTP(S) 代理; 不读取通用代理环境变量。
    - SYNORA_PROVIDER_FALLBACK_API_KEY: 可选, 填写后启用一次性备用 provider;
    - SYNORA_PROVIDER_FALLBACK_BASE_URL/MODEL/PROXY: 可选, 缺省时沿用主 provider;
    - SYNORA_PROVIDER_FALLBACK_THINKING/REASONING_EFFORT: 可选, 备用 provider 专用设置。

    base_url 未配置时抛 ProviderError (fail closed, 不猜测默认地址)。
    """
    base_url = os.environ.get(PROVIDER_BASE_URL_ENV, "")
    if not base_url:
        raise ProviderError(f"{PROVIDER_BASE_URL_ENV} is not configured; set it in the environment")
    model = os.environ.get(PROVIDER_MODEL_ENV, "").strip() or DEFAULT_PROVIDER_MODEL
    proxy = os.environ.get(PROVIDER_PROXY_ENV) or None
    thinking = _provider_thinking_mode(
        os.environ,
        model=model,
        thinking_env=PROVIDER_THINKING_ENV,
    )
    fallback_api_key = os.environ.get(PROVIDER_FALLBACK_API_KEY_ENV, "")
    local_base_url = os.environ.get(PROVIDER_LOCAL_BASE_URL_ENV, "").strip()
    local_models = tuple(
        value
        for value in (
            os.environ.get(PROVIDER_LOCAL_SMALL_MODEL_ENV, "").strip(),
            os.environ.get(PROVIDER_LOCAL_LARGE_MODEL_ENV, "").strip(),
        )
        if value
    )
    fallback_overrides = (
        PROVIDER_FALLBACK_BASE_URL_ENV,
        PROVIDER_FALLBACK_MODEL_ENV,
        PROVIDER_FALLBACK_REASONING_EFFORT_ENV,
        PROVIDER_FALLBACK_THINKING_ENV,
        PROVIDER_FALLBACK_PROXY_ENV,
    )
    fallback_overrides_configured = any(
        os.environ.get(name, "").strip() for name in fallback_overrides
    )
    if not fallback_api_key.strip() and fallback_overrides_configured:
        raise ProviderError(
            f"{PROVIDER_FALLBACK_API_KEY_ENV} is required when fallback settings are configured",
            failure_code="INVALID_CONFIGURATION",
        )
    api_key = os.environ.get(PROVIDER_API_KEY_ENV, "")
    primary = OpenAICompatibleProvider(
        base_url=base_url,
        api_key=SecretStr(api_key) if api_key else None,
        model=model,
        reasoning_effort=os.environ.get(PROVIDER_REASONING_EFFORT_ENV) or None,
        thinking=thinking,
        proxy=proxy,
        transport=transport,
    )
    if not fallback_api_key.strip() and not local_models:
        return primary

    fallbacks: list[Provider] = []
    if local_models:
        if not local_base_url:
            raise ProviderError(
                f"{PROVIDER_LOCAL_BASE_URL_ENV} is required when local models are configured",
                failure_code="INVALID_CONFIGURATION",
            )
        fallbacks.extend(
            OpenAICompatibleProvider(
                base_url=local_base_url,
                model=local_model,
                reasoning_effort="none",
                transport=transport,
            )
            for local_model in local_models
        )

    if fallback_api_key.strip():
        fallback_model = os.environ.get(PROVIDER_FALLBACK_MODEL_ENV, "").strip() or model
        fallback_thinking = _provider_thinking_mode(
            os.environ,
            model=fallback_model,
            thinking_env=PROVIDER_FALLBACK_THINKING_ENV,
        )
        if (
            not os.environ.get(PROVIDER_FALLBACK_THINKING_ENV, "").strip()
            and fallback_model == model
        ):
            fallback_thinking = thinking
        fallbacks.append(
            OpenAICompatibleProvider(
                base_url=os.environ.get(PROVIDER_FALLBACK_BASE_URL_ENV, "").strip() or base_url,
                api_key=SecretStr(fallback_api_key),
                model=fallback_model,
                reasoning_effort=os.environ.get(PROVIDER_FALLBACK_REASONING_EFFORT_ENV) or None,
                thinking=fallback_thinking,
                proxy=os.environ.get(PROVIDER_FALLBACK_PROXY_ENV) or proxy,
                transport=transport,
            )
        )
    return FailoverProvider(primary, fallbacks[0], *fallbacks[1:])
