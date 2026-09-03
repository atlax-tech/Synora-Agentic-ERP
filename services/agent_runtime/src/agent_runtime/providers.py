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
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

MAX_PROVIDER_RESPONSE_BYTES = 2_000_000

# Named provider configuration. These names select roles; runtime events decide
# whether a candidate is actually called. There is deliberately no legacy
# single-provider or ad-hoc fallback configuration surface.
OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"
OLLAMA_API_KEY_ENV = "OLLAMA_API_KEY"
OLLAMA_MODEL_ENV = "OLLAMA_MODEL"
ASSIST_BASE_URL_ENV = "ASSIST_BASE_URL"
ASSIST_API_KEY_ENV = "ASSIST_API_KEY"
ASSIST_MODEL_ENV = "ASSIST_MODEL"
BACKUP_BASE_URL_ENV = "BACKUP_BASE_URL"
BACKUP_API_KEY_ENV = "BACKUP_API_KEY"
BACKUP_MODEL_ENV = "BACKUP_MODEL"
BACKUP_OLLAMA_BASE_URL_ENV = "BACKUP_OLLAMA_BASE_URL"
BACKUP_OLLAMA_API_KEY_ENV = "BACKUP_OLLAMA_API_KEY"
BACKUP_OLLAMA_MODEL_ENV = "BACKUP_OLLAMA_MODEL"
MODEL_PROXY_ENV = "SYNORA_MODEL_PROXY"
PROVIDER_MAX_OUTPUT_TOKENS = 1024
PROVIDER_MAX_OUTPUT_TOKEN_LIMIT = 8192
LOCAL_PROVIDER_TIMEOUT_SECONDS = 180.0
# The 27B fallback is slow, but an unbounded socket can pin a Coach Run
# forever. Keep a long, explicit deadline rather than allowing an infinite
# provider request; normal Runtime endpoints retain their shorter limits.
SLOW_LOCAL_PROVIDER_TIMEOUT_SECONDS = 900.0
QWEN3_8B_MODEL = "qwen3:8b"
GLM_5_3_FLASH_MODEL = "glm-5.3-flash"
GROK_4_5_MODEL = "grok-4.5"
QWEN3_8_27B_MODEL = "qwen3.8:27b"
GLM_5_3_FLASH_DEFAULT_REASONING_EFFORT = "low"
GROK_4_5_DEFAULT_REASONING_EFFORT = "low"
_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
_FAILOVER_FAILURE_CODES = frozenset(
    {
        "RATE_LIMITED",
        "UPSTREAM_UNAVAILABLE",
        "TIMEOUT",
        "TRANSPORT_ERROR",
        # A 2xx response without a usable completion is unavailable for this
        # request. Move to the next configured provider once; never retry the
        # same paid endpoint with the same malformed response.
        "RESPONSE_SCHEMA",
        "RESPONSE_NO_CHOICES",
        "RESPONSE_CONTENT_MISSING",
    }
)
_NAMED_PROVIDER_ENV_NAMES = frozenset(
    {
        OLLAMA_BASE_URL_ENV,
        OLLAMA_API_KEY_ENV,
        OLLAMA_MODEL_ENV,
        ASSIST_BASE_URL_ENV,
        ASSIST_API_KEY_ENV,
        ASSIST_MODEL_ENV,
        BACKUP_BASE_URL_ENV,
        BACKUP_API_KEY_ENV,
        BACKUP_MODEL_ENV,
        BACKUP_OLLAMA_BASE_URL_ENV,
        BACKUP_OLLAMA_API_KEY_ENV,
        BACKUP_OLLAMA_MODEL_ENV,
        MODEL_PROXY_ENV,
    }
)
ProviderResponseFormat = Literal["json_object"] | Mapping[str, object]
ProviderWireAPI = Literal["chat_completions", "responses"]
ProviderRole = Literal["primary", "assist", "backup", "last_local"]
_NAMED_PROVIDER_ROLES: tuple[ProviderRole, ...] = (
    "primary",
    "assist",
    "backup",
    "last_local",
)


def _primary_model(values: Mapping[str, str]) -> str:
    return values.get(OLLAMA_MODEL_ENV, "").strip()


def provider_model(environ: Mapping[str, str] | None = None) -> str:
    """Return the configured primary model without exposing provider secrets."""
    values = os.environ if environ is None else environ
    return _primary_model(values)


def _output_token_limits(_model: str | None) -> tuple[int, int]:
    return PROVIDER_MAX_OUTPUT_TOKENS, PROVIDER_MAX_OUTPUT_TOKEN_LIMIT


def provider_max_output_token_limit(model: str | None = None) -> int:
    """Return the model-specific hard ceiling for one provider request."""
    return _output_token_limits(model)[1]


def provider_max_output_tokens(_environ: Mapping[str, str] | None = None) -> int:
    """Return the fixed Coach generation cap.

    The cap is a code-level safety boundary, not a provider-selection setting;
    keeping it out of the environment prevents an old deployment variable from
    silently changing request cost or acceptance semantics.
    """
    return PROVIDER_MAX_OUTPUT_TOKENS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ProviderToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    name: str
    arguments: str


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


def _serialize_responses_message(message: ProviderMessage) -> dict[str, object]:
    """Serialize a plain message for the OpenAI Responses input contract.

    Coach requests never contain tool messages or assistant tool calls.  Do
    not silently reinterpret those messages for a different wire protocol;
    callers that need tools must use a provider with a matching chat contract.
    """
    if message.role == "tool" or message.tool_calls:
        raise ProviderError(
            "responses provider does not accept tool messages in this request",
            failure_code="INVALID_REQUEST",
        )
    return {"role": message.role, "content": message.content}


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
        proxy: str | None = None,
        timeout_seconds: float | None = 60.0,
        supports_json_schema: bool = False,
        temperature: float | None = None,
        wire_api: ProviderWireAPI = "chat_completions",
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
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("provider timeout must be positive or None")
        if temperature is not None and (not math.isfinite(temperature) or temperature < 0):
            raise ValueError("provider temperature must be non-negative or None")
        if wire_api not in {"chat_completions", "responses"}:
            raise ValueError("provider wire_api must be chat_completions or responses")
        if reasoning_effort is not None and reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError("provider reasoning_effort must be none, low, medium, high, or xhigh")
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
        self._wire_api = wire_api
        endpoint = "/responses" if wire_api == "responses" else "/chat/completions"
        # 兼容两种填法: 根地址 (https://host/v1) 或该协议的完整端点。
        if self._base_url.endswith(endpoint):
            self._chat_url = self._base_url
        else:
            self._chat_url = f"{self._base_url}{endpoint}"
        self._api_key = api_key
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._proxy = str(proxy_url).rstrip("/") if proxy_url else None
        self._supports_json_schema = supports_json_schema
        self._temperature = temperature
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
        if self._wire_api == "responses":
            payload: dict[str, object] = {
                "model": requested_model,
                "input": [_serialize_responses_message(message) for message in messages],
                # Responses are not needed after this bounded request and must
                # not be retained by a third-party gateway by default.
                "store": False,
            }
        else:
            payload = {
                "model": requested_model,
                "messages": [_serialize_message(message) for message in messages],
                "stream": False,
            }
        # max_tokens/max_output_tokens is the generation-side cap, not a total
        # input-plus-output budget.
        if max_tokens is not None:
            hard_limit = provider_max_output_token_limit(requested_model)
            if max_tokens < 1 or max_tokens > hard_limit:
                raise ValueError(f"provider max_tokens must be within 1..{hard_limit}")
            payload["max_output_tokens" if self._wire_api == "responses" else "max_tokens"] = (
                max_tokens
            )
        if response_format is not None:
            if isinstance(response_format, Mapping):
                if response_format.get("type") == "json_schema" and self._supports_json_schema:
                    format_value: object = dict(response_format)
                else:
                    format_value = {"type": "json_object"}
            else:
                format_value = {"type": response_format}
            if self._wire_api == "responses":
                payload["text"] = {"format": format_value}
            else:
                payload["response_format"] = format_value
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if self._reasoning_effort is not None:
            # xAI Responses uses an object; OpenAI-compatible chat endpoints
            # use the flat field. Neither value is treated as answer content.
            if self._wire_api == "responses":
                payload["reasoning"] = {"effort": self._reasoning_effort}
            else:
                payload["reasoning_effort"] = self._reasoning_effort
        if tools:
            if self._wire_api == "responses":
                payload["tools"] = [
                    {
                        "type": "function",
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "strict": True,
                    }
                    for tool in tools
                ]
            else:
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

        response_text = ""
        tool_calls: tuple[ProviderToolCall, ...] = ()
        reasoning_content_present = False
        prompt_tokens = completion_tokens = reasoning_tokens = total_tokens = 0
        usage_present = False
        if self._wire_api == "responses":
            try:
                completion_responses = _ResponsesEnvelope.model_validate_json(body)
            except (ValueError, TypeError, RecursionError) as error:
                raise ProviderError(
                    "provider returned an invalid response", failure_code="RESPONSE_SCHEMA"
                ) from error
            if not completion_responses.output:
                raise ProviderError(
                    "provider returned no output", failure_code="RESPONSE_NO_CHOICES"
                )
            response_text, tool_calls, reasoning_content_present = _responses_output_values(
                completion_responses
            )
            if completion_responses.usage is not None:
                usage_present = True
                prompt_tokens = completion_responses.usage.input_tokens
                completion_tokens = completion_responses.usage.output_tokens
                total_tokens = completion_responses.usage.total_tokens
                responses_details = completion_responses.usage.output_tokens_details
                reasoning_tokens = responses_details.reasoning_tokens if responses_details else 0
        else:
            try:
                completion = _CompletionEnvelope.model_validate_json(body)
            except (ValueError, TypeError, RecursionError) as error:
                raise ProviderError(
                    "provider returned an invalid response", failure_code="RESPONSE_SCHEMA"
                ) from error
            if not completion.choices:
                raise ProviderError(
                    "provider returned no choices", failure_code="RESPONSE_NO_CHOICES"
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
            response_text = message.content or ""
            reasoning_content_present = message.reasoning_content is not None
            if completion.usage is not None:
                usage_present = True
                prompt_tokens = completion.usage.prompt_tokens
                completion_tokens = completion.usage.completion_tokens
                total_tokens = completion.usage.total_tokens
                chat_details = completion.usage.completion_tokens_details
                reasoning_tokens = chat_details.reasoning_tokens if chat_details else 0
        if max_tokens is not None and not usage_present:
            # 没有 usage 就无法证明服务商遵守输出预算; 宁可回退, 也不接受
            # 未验证的真实模型结果。请求参数仍是服务商侧的首要成本护栏。
            raise ProviderError(
                "provider omitted usage for budgeted response",
                budget_code="TOKEN_BUDGET",
                failure_code="USAGE_MISSING",
            )
        if usage_present:
            if (
                prompt_tokens < 0
                or completion_tokens < 0
                or reasoning_tokens < 0
                or total_tokens < 0
            ):
                raise ProviderError(
                    "provider returned invalid token usage",
                    budget_code="TOKEN_BUDGET",
                    failure_code="USAGE_INVALID",
                )
            # Some providers report reasoning_tokens as a subset of completion_tokens
            # (e.g. GLM), while others report it separately. total - prompt is the
            # provider-neutral billed output count and avoids double-counting either form.
            billed_output_tokens = total_tokens - prompt_tokens
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
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    budget_code="TOKEN_BUDGET",
                    failure_code="BUDGET_EXCEEDED",
                )
        if not response_text and not tool_calls:
            raise ProviderError(
                "provider returned no final content", failure_code="RESPONSE_CONTENT_MISSING"
            )
        return ProviderResponse(
            text=response_text,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_content_present=reasoning_content_present,
        )


class FailoverProvider:
    """Try configured providers once each, in priority order.

    The iterator owns all request progress.  The provider chain itself is
    immutable, so concurrent Coach requests cannot reset or reuse another
    request's attempt state.
    """

    def __init__(self, *providers: Provider) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = tuple(providers)

    def __repr__(self) -> str:
        return f"FailoverProvider(provider_count={len(self._providers)})"

    def _effective_max_tokens(self, index: int, max_tokens: int | None) -> int | None:
        if index == 0 or max_tokens is None:
            return max_tokens
        provider = self._providers[index]
        if isinstance(provider, OpenAICompatibleProvider):
            return min(max_tokens, provider_max_output_token_limit(provider._model))
        return max_tokens

    async def _call_provider(
        self,
        index: int,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None,
        model: str | None,
        max_tokens: int | None,
        response_format: ProviderResponseFormat | None,
    ) -> ProviderResponse:
        provider = self._providers[index]
        return await provider.complete(
            messages,
            tools=tools,
            model=model if index == 0 else None,
            max_tokens=self._effective_max_tokens(index, max_tokens),
            response_format=response_format,
        )

    @staticmethod
    def _aggregate_errors(errors: Sequence[ProviderError]) -> ProviderError:
        last_error = errors[-1]
        return ProviderError(
            "all configured providers failed",
            prompt_tokens=sum(error.prompt_tokens for error in errors),
            completion_tokens=sum(error.completion_tokens for error in errors),
            reasoning_tokens=sum(error.reasoning_tokens for error in errors),
            budget_code=next(
                (error.budget_code for error in reversed(errors) if error.budget_code), None
            ),
            failure_code=last_error.failure_code,
        )

    async def iter_candidates(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> AsyncIterator[ProviderResponse]:
        """Yield one successful response per configured provider at most once.

        Transport, rate-limit, timeout, and unusable response-envelope failures
        advance to the next provider. Authentication, request, context, and
        budget failures are raised immediately and remain fail-closed. A caller
        may stop consuming after a valid answer or refusal; no hidden request is
        made after the iterator is closed.
        """
        errors: list[ProviderError] = []
        yielded = False
        for index in range(len(self._providers)):
            try:
                response = await self._call_provider(
                    index,
                    messages,
                    tools,
                    model if index == 0 else None,
                    max_tokens,
                    response_format,
                )
            except ProviderError as error:
                if error.failure_code not in _FAILOVER_FAILURE_CODES:
                    raise
                errors.append(error)
                continue
            yielded = True
            yield response
        if not yielded and errors:
            raise self._aggregate_errors(errors) from errors[-1]
        if not yielded:
            raise ProviderError("no provider is configured", failure_code="NO_PROVIDER")

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        async for response in self.iter_candidates(
            messages,
            tools,
            model,
            max_tokens,
            response_format,
        ):
            return response
        raise ProviderError("no usable provider response", failure_code="NO_PROVIDER")

    async def aclose(self) -> None:
        errors: list[Exception] = []
        for provider in self._providers:
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


class _ResponsesOutputContent(_WireModel):
    type: str = ""
    text: str | None = None


class _ResponsesOutputItem(_WireModel):
    type: str = ""
    role: str | None = None
    content: tuple[_ResponsesOutputContent, ...] = ()
    id: str = ""
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None


class _ResponsesUsageOutputDetails(_WireModel):
    reasoning_tokens: int = 0


class _ResponsesUsage(_WireModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    output_tokens_details: _ResponsesUsageOutputDetails | None = None


class _ResponsesEnvelope(_WireModel):
    output: tuple[_ResponsesOutputItem, ...] = ()
    usage: _ResponsesUsage | None = None


def _responses_output_values(
    completion: _ResponsesEnvelope,
) -> tuple[str, tuple[ProviderToolCall, ...], bool]:
    text_parts: list[str] = []
    tool_calls: list[ProviderToolCall] = []
    reasoning_present = False
    for item in completion.output:
        if item.type == "reasoning":
            reasoning_present = True
            continue
        if item.type == "message":
            for content in item.content:
                if content.type == "output_text" and content.text:
                    text_parts.append(content.text)
            continue
        if item.type == "function_call":
            if not item.name or item.arguments is None:
                raise ProviderError(
                    "provider returned an invalid function call",
                    failure_code="RESPONSE_SCHEMA",
                )
            tool_calls.append(
                ProviderToolCall(
                    id=item.call_id or item.id,
                    name=item.name,
                    arguments=item.arguments,
                )
            )
    return "".join(text_parts), tuple(tool_calls), reasoning_present


def _required_new_provider_value(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ProviderError(f"{name} is required", failure_code="INVALID_CONFIGURATION")
    return value


def _validate_new_provider_model(values: Mapping[str, str], name: str, expected: str) -> str:
    value = _required_new_provider_value(values, name)
    if value != expected:
        raise ProviderError(
            f"{name} must select the configured provider role", failure_code="INVALID_CONFIGURATION"
        )
    return value


def _validated_named_provider_values(
    values: Mapping[str, str],
) -> tuple[dict[ProviderRole, tuple[str, str, str]], str | None]:
    """Validate all named role slots without constructing network clients."""
    named_values: dict[ProviderRole, tuple[str, str, str]] = {
        "primary": (
            _required_new_provider_value(values, OLLAMA_BASE_URL_ENV),
            _required_new_provider_value(values, OLLAMA_API_KEY_ENV),
            _validate_new_provider_model(values, OLLAMA_MODEL_ENV, QWEN3_8B_MODEL),
        ),
        "assist": (
            _required_new_provider_value(values, ASSIST_BASE_URL_ENV),
            _required_new_provider_value(values, ASSIST_API_KEY_ENV),
            _validate_new_provider_model(values, ASSIST_MODEL_ENV, GLM_5_3_FLASH_MODEL),
        ),
        "backup": (
            _required_new_provider_value(values, BACKUP_BASE_URL_ENV),
            _required_new_provider_value(values, BACKUP_API_KEY_ENV),
            _validate_new_provider_model(values, BACKUP_MODEL_ENV, GROK_4_5_MODEL),
        ),
        "last_local": (
            _required_new_provider_value(values, BACKUP_OLLAMA_BASE_URL_ENV),
            _required_new_provider_value(values, BACKUP_OLLAMA_API_KEY_ENV),
            _validate_new_provider_model(values, BACKUP_OLLAMA_MODEL_ENV, QWEN3_8_27B_MODEL),
        ),
    }
    return named_values, values.get(MODEL_PROXY_ENV, "").strip() or None


def _build_named_provider(
    role: ProviderRole,
    values: Mapping[str, str],
    named_values: Mapping[ProviderRole, tuple[str, str, str]],
    proxy: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> OpenAICompatibleProvider:
    base_url, api_key, model = named_values[role]
    if role == "primary":
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=SecretStr(api_key),
            model=model,
            timeout_seconds=LOCAL_PROVIDER_TIMEOUT_SECONDS,
            supports_json_schema=True,
            temperature=0.0,
            # Ollama's OpenAI-compatible Responses endpoint honors JSON Schema
            # and avoids the chat-compatibility layer's hidden Qwen thinking
            # budget.  Coach expects answer-only structured output.
            reasoning_effort="none",
            wire_api="responses",
            transport=transport,
        )
    if role == "assist":
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=SecretStr(api_key),
            model=model,
            reasoning_effort=GLM_5_3_FLASH_DEFAULT_REASONING_EFFORT,
            temperature=0.0,
            proxy=proxy,
            transport=transport,
        )
    if role == "backup":
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=SecretStr(api_key),
            model=model,
            reasoning_effort=GROK_4_5_DEFAULT_REASONING_EFFORT,
            proxy=proxy,
            wire_api="responses",
            transport=transport,
        )
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=SecretStr(api_key),
        model=model,
        timeout_seconds=SLOW_LOCAL_PROVIDER_TIMEOUT_SECONDS,
        supports_json_schema=True,
        temperature=0.0,
        reasoning_effort="none",
        wire_api="responses",
        transport=transport,
    )


def _new_provider_from_environment(
    values: Mapping[str, str], transport: httpx.AsyncBaseTransport | None
) -> Provider:
    named_values, proxy = _validated_named_provider_values(values)
    try:
        providers = tuple(
            _build_named_provider(role, values, named_values, proxy, transport)
            for role in _NAMED_PROVIDER_ROLES
        )
    except ValueError as error:
        # Do not leak a configured URL, proxy, or secret through configuration errors.
        raise ProviderError(
            "invalid provider configuration", failure_code="INVALID_CONFIGURATION"
        ) from error
    return FailoverProvider(*providers)


def provider_for_role(
    role: ProviderRole,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> OpenAICompatibleProvider:
    """Construct exactly one configured role for a no-fallback connectivity check.

    This is intentionally separate from :func:`provider_from_environment`:
    the runtime may use its bounded failover chain, while an operator's
    connectivity probe must never spend money on another role merely because
    the selected role returned an unusable response.
    """
    values = os.environ if environ is None else environ
    if role not in _NAMED_PROVIDER_ROLES:
        raise ProviderError("unknown provider role", failure_code="INVALID_CONFIGURATION")
    named_values, proxy = _validated_named_provider_values(values)
    try:
        return _build_named_provider(role, values, named_values, proxy, transport)
    except ValueError as error:
        raise ProviderError(
            "invalid provider configuration", failure_code="INVALID_CONFIGURATION"
        ) from error


def provider_from_environment(
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Provider:
    """Construct the configured four-role provider chain.

    All role slots are validated before any network client is created. This
    keeps configuration deterministic and removes the former single-provider
    compatibility path that could silently select a deprecated model.
    """
    values = os.environ if environ is None else environ
    return _new_provider_from_environment(values, transport)
