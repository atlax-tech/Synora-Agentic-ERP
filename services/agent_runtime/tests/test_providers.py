"""P3.4 Provider 接口基线测试 (ARCHITECTURE "Model access")。

CI 使用确定性 provider; OpenAI 兼容 provider 通过 MockTransport 验证
请求/响应契约、fail-closed 与 secret 防泄漏, 不发真实网络请求。
"""

import asyncio
import json
import math

import httpx
import pytest
from agent_runtime.providers import (
    ASSIST_API_KEY_ENV,
    ASSIST_BASE_URL_ENV,
    ASSIST_MODEL_ENV,
    BACKUP_API_KEY_ENV,
    BACKUP_BASE_URL_ENV,
    BACKUP_MODEL_ENV,
    BACKUP_OLLAMA_API_KEY_ENV,
    BACKUP_OLLAMA_BASE_URL_ENV,
    BACKUP_OLLAMA_MODEL_ENV,
    DEFAULT_PROVIDER_MODEL,
    GLM_4_7_FLASH_DEFAULT_MAX_OUTPUT_TOKENS,
    GLM_4_7_FLASH_MAX_OUTPUT_TOKENS,
    LOCAL_PROVIDER_TIMEOUT_SECONDS,
    OLLAMA_API_KEY_ENV,
    OLLAMA_BASE_URL_ENV,
    OLLAMA_MODEL_ENV,
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URL_ENV,
    PROVIDER_FALLBACK_API_KEY_ENV,
    PROVIDER_FALLBACK_BASE_URL_ENV,
    PROVIDER_FALLBACK_MODEL_ENV,
    PROVIDER_FALLBACK_PROXY_ENV,
    PROVIDER_FALLBACK_REASONING_EFFORT_ENV,
    PROVIDER_FALLBACK_THINKING_ENV,
    PROVIDER_LOCAL_BASE_URL_ENV,
    PROVIDER_LOCAL_LARGE_MODEL_ENV,
    PROVIDER_LOCAL_SMALL_MODEL_ENV,
    PROVIDER_MAX_OUTPUT_TOKENS,
    PROVIDER_MAX_OUTPUT_TOKENS_ENV,
    PROVIDER_MODEL_ENV,
    PROVIDER_PROXY_ENV,
    PROVIDER_REASONING_EFFORT_ENV,
    PROVIDER_THINKING_ENV,
    DeterministicProvider,
    FailoverProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    ProviderRole,
    ProviderToolCall,
    ProviderToolSpec,
    provider_for_role,
    provider_from_environment,
    provider_max_output_token_limit,
    provider_max_output_tokens,
    provider_model,
    provider_thinking_mode,
)
from pydantic import SecretStr, ValidationError


def _transport_that_returns(body: dict[str, object], status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, request=request)

    return httpx.MockTransport(handler)


def _transport_that_raises(error: Exception) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    return httpx.MockTransport(handler)


def _messages(*, text: str = "user input") -> list[ProviderMessage]:
    return [ProviderMessage(role="user", content=text)]


def _new_provider_environment() -> dict[str, str]:
    return {
        OLLAMA_BASE_URL_ENV: "http://127.0.0.1:11434/v1",
        OLLAMA_API_KEY_ENV: "ollama",
        OLLAMA_MODEL_ENV: "qwen3:8b",
        ASSIST_BASE_URL_ENV: "https://assist.example/v1",
        ASSIST_API_KEY_ENV: "assist-secret",
        ASSIST_MODEL_ENV: "glm-5.3-flash",
        BACKUP_BASE_URL_ENV: "https://backup.example/v1",
        BACKUP_API_KEY_ENV: "backup-secret",
        BACKUP_MODEL_ENV: "grok-4.5",
        BACKUP_OLLAMA_BASE_URL_ENV: "http://localhost:11434/v1",
        BACKUP_OLLAMA_API_KEY_ENV: "ollama",
        BACKUP_OLLAMA_MODEL_ENV: "qwen3.8:27b",
    }


class _ScriptedProvider:
    def __init__(self, name: str, *outcomes: ProviderResponse | ProviderError) -> None:
        self.name = name
        self._outcomes = list(outcomes)
        self.calls = 0

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: object | None = None,
    ) -> ProviderResponse:
        await asyncio.sleep(0)
        del messages, tools, model, max_tokens, response_format
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome


def test_provider_max_output_tokens_defaults_to_phase_cap() -> None:
    assert provider_max_output_tokens({}) == PROVIDER_MAX_OUTPUT_TOKENS == 1024
    assert provider_model({}) == ""


def test_glm_output_policy_prefers_quality_default_and_model_limit() -> None:
    environ = {PROVIDER_MODEL_ENV: "glm-4.7-flash"}
    assert provider_max_output_tokens(environ) == GLM_4_7_FLASH_DEFAULT_MAX_OUTPUT_TOKENS
    assert provider_max_output_token_limit("glm-4.7-flash") == GLM_4_7_FLASH_MAX_OUTPUT_TOKENS


def test_provider_max_output_tokens_can_be_tuned_down() -> None:
    assert provider_max_output_tokens({PROVIDER_MAX_OUTPUT_TOKENS_ENV: "800"}) == 800


def test_glm_output_policy_can_be_tuned_above_legacy_default() -> None:
    assert (
        provider_max_output_tokens(
            {
                PROVIDER_MODEL_ENV: "glm-4.7-flash",
                PROVIDER_MAX_OUTPUT_TOKENS_ENV: "100000",
            }
        )
        == 100000
    )


@pytest.mark.parametrize("value", ["0", "-1", "8193", "not-an-integer"])
def test_provider_max_output_tokens_rejects_invalid_configuration(value: str) -> None:
    with pytest.raises(ValueError, match="provider max output tokens"):
        provider_max_output_tokens({PROVIDER_MAX_OUTPUT_TOKENS_ENV: value})


def test_glm_output_policy_rejects_values_above_model_limit() -> None:
    with pytest.raises(ValueError, match="131072"):
        provider_max_output_tokens(
            {
                PROVIDER_MODEL_ENV: "glm-4.7-flash",
                PROVIDER_MAX_OUTPUT_TOKENS_ENV: "131073",
            }
        )


def test_thinking_policy_defaults_only_for_glm() -> None:
    assert provider_thinking_mode({PROVIDER_MODEL_ENV: "glm-4.7-flash"}) == "enabled"
    assert provider_thinking_mode({PROVIDER_MODEL_ENV: "other-model"}) is None
    assert (
        provider_thinking_mode(
            {PROVIDER_MODEL_ENV: "glm-4.7-flash", PROVIDER_THINKING_ENV: "disabled"}
        )
        == "disabled"
    )


def test_glm53_defaults_to_low_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://api.example.com/v1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV, "glm-secret")
    monkeypatch.setenv(PROVIDER_MODEL_ENV, "glm-5.3-flash")
    monkeypatch.delenv(PROVIDER_REASONING_EFFORT_ENV, raising=False)
    provider = provider_from_environment(
        transport=_transport_that_returns(
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._reasoning_effort == "low"
    asyncio.run(provider.aclose())


class TestDeterministicProvider:
    def test_returns_fixed_response_for_mapped_input(self) -> None:
        async def run() -> None:
            provider = DeterministicProvider(
                responses={
                    "user input": ProviderResponse(text="planned answer"),
                    "other": ProviderResponse(text="other answer"),
                }
            )
            response = await provider.complete(_messages())
            assert response.text == "planned answer"
            response = await provider.complete(_messages(text="other"))
            assert response.text == "other answer"

        asyncio.run(run())

    def test_unknown_input_fails_closed(self) -> None:
        async def run() -> None:
            provider = DeterministicProvider(responses={"known": ProviderResponse(text="ok")})
            with pytest.raises(ProviderError):
                await provider.complete(_messages(text="unmapped"))

        asyncio.run(run())

    def test_default_used_when_provided(self) -> None:
        async def run() -> None:
            provider = DeterministicProvider(
                responses={"known": ProviderResponse(text="ok")},
                default=ProviderResponse(text="fallback"),
            )
            response = await provider.complete(_messages(text="anything"))
            assert response.text == "fallback"

        asyncio.run(run())

    def test_rejects_empty_messages(self) -> None:
        async def run() -> None:
            provider = DeterministicProvider(default=ProviderResponse(text="fallback"))
            with pytest.raises(ProviderError):
                await provider.complete([])

        asyncio.run(run())

    def test_is_repeatable(self) -> None:
        async def run() -> None:
            provider = DeterministicProvider(
                responses={"user input": ProviderResponse(text="same")}
            )
            first = await provider.complete(_messages())
            second = await provider.complete(_messages())
            assert first == second

        asyncio.run(run())

    def test_supports_fixed_multi_turn_responses(self) -> None:
        async def run() -> None:
            provider = DeterministicProvider(
                scripted_responses=[
                    ProviderResponse(text="first"),
                    ProviderResponse(text="second"),
                ]
            )
            first = await provider.complete(_messages())
            second = await provider.complete(_messages())
            assert (first.text, second.text) == ("first", "second")

        asyncio.run(run())

    def test_validates_tool_message_fields(self) -> None:
        with pytest.raises(ValidationError):
            ProviderMessage(role="tool", content="observation")
        with pytest.raises(ValidationError):
            ProviderMessage(role="assistant")
        message = ProviderMessage(
            role="tool",
            content="bounded observation",
            tool_call_id="call-1",
            name="item.lookup",
        )
        assert message.tool_call_id == "call-1"


class TestOpenAICompatibleProvider:
    def test_rejects_non_origin_base_url(self) -> None:
        for bad in (
            "https://user:pass@host/v1",
            "https://host",
            "https://host/",
            "https://host/v1/",
            "https://host/v1?x=1",
            "https://host/v1#frag",
            "ftp://host",
            "",
        ):
            with pytest.raises(ValueError):
                OpenAICompatibleProvider(base_url=bad)

    @pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf, -math.inf])
    def test_rejects_invalid_timeout(self, timeout: float) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleProvider(base_url="http://127.0.0.1:11434/v1", timeout_seconds=timeout)

    def test_allows_true_no_timeout_for_slow_local_provider(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="http://127.0.0.1:11434/v1", timeout_seconds=None
        )
        assert provider._client.timeout.connect is None
        assert provider._client.timeout.read is None
        asyncio.run(provider.aclose())

    def test_rejects_unknown_reasoning_effort(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", reasoning_effort="unbounded"
            )

    def test_rejects_unknown_thinking_mode(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleProvider(base_url="http://127.0.0.1:11434/v1", thinking="maybe")

    def test_rejects_negative_temperature(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            OpenAICompatibleProvider(base_url="http://127.0.0.1:11434/v1", temperature=-0.1)

    def test_parses_text_response(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "hello from model"}}]}
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1",
                model="local-model",
                transport=transport,
            ) as provider:
                response = await provider.complete(_messages())
            assert response.text == "hello from model"
            assert response.tool_calls == ()

        asyncio.run(run())

    def test_parses_tool_calls(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "item.lookup",
                                            "arguments": '{"query": "bearing"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                response = await provider.complete(_messages())
            assert response.text == ""
            assert response.tool_calls == (
                ProviderToolCall(
                    id="call-1",
                    name="item.lookup",
                    arguments='{"query": "bearing"}',
                ),
            )

        asyncio.run(run())

    def test_sends_tools_and_model(self) -> None:
        async def run() -> None:
            captured: dict[str, object] = {}

            def handler(request: httpx.Request) -> httpx.Response:
                captured["json"] = json.loads(request.content)
                captured["path"] = request.url.path
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                    request=request,
                )

            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1",
                model="configured-model",
                thinking="disabled",
                temperature=0.0,
                transport=httpx.MockTransport(handler),
            ) as provider:
                await provider.complete(
                    _messages(),
                    tools=[
                        ProviderToolSpec(
                            name="item.lookup",
                            description="lookup items",
                            parameters={"type": "object"},
                        )
                    ],
                    model="override-model",
                )
            body = captured["json"]
            assert captured["path"] == "/v1/chat/completions"
            assert isinstance(body, dict)
            assert body["model"] == "override-model"
            assert body["stream"] is False
            assert body["thinking"] == {"type": "disabled"}
            assert body["temperature"] == 0.0
            tools = body["tools"]
            assert isinstance(tools, list)
            assert tools[0]["function"]["name"] == "item.lookup"

        asyncio.run(run())

    def test_glm_request_uses_thinking_and_large_quality_cap(self) -> None:
        async def run() -> None:
            captured: dict[str, object] = {}

            def handler(request: httpx.Request) -> httpx.Response:
                captured["json"] = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                    },
                    request=request,
                )

            async with OpenAICompatibleProvider(
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-4.7-flash",
                thinking="enabled",
                transport=httpx.MockTransport(handler),
            ) as provider:
                await provider.complete(_messages(), max_tokens=65_536)
            body = captured["json"]
            assert isinstance(body, dict)
            assert body["max_tokens"] == 65_536
            assert body["thinking"] == {"type": "enabled"}

    def test_request_can_enable_provider_json_object_mode(self) -> None:
        async def run() -> None:
            captured: dict[str, object] = {}

            def handler(request: httpx.Request) -> httpx.Response:
                captured["json"] = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "{}"}}]},
                    request=request,
                )

            async with OpenAICompatibleProvider(
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-4.7-flash",
                transport=httpx.MockTransport(handler),
            ) as provider:
                await provider.complete(_messages(), response_format="json_object")
            body = captured["json"]
            assert isinstance(body, dict)
            assert body["response_format"] == {"type": "json_object"}

        asyncio.run(run())

    def test_responses_wire_payload_and_output_are_normalized(self) -> None:
        async def run() -> None:
            captured: dict[str, object] = {}

            def handler(request: httpx.Request) -> httpx.Response:
                captured["json"] = json.loads(request.content)
                captured["path"] = request.url.path
                return httpx.Response(
                    200,
                    json={
                        "object": "response",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "ok"}],
                            }
                        ],
                        "usage": {
                            "input_tokens": 4,
                            "output_tokens": 2,
                            "total_tokens": 6,
                            "output_tokens_details": {"reasoning_tokens": 0},
                        },
                    },
                    request=request,
                )

            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "coach", "strict": True, "schema": {}},
            }
            async with OpenAICompatibleProvider(
                base_url="https://backup.example/v1",
                model="grok-4.5",
                reasoning_effort="low",
                wire_api="responses",
                transport=httpx.MockTransport(handler),
            ) as provider:
                response = await provider.complete(
                    _messages(),
                    tools=[],
                    max_tokens=32,
                    response_format=response_format,
                )
            body = captured["json"]
            assert captured["path"] == "/v1/responses"
            assert isinstance(body, dict)
            assert body["input"] == [{"role": "user", "content": "user input"}]
            assert body["max_output_tokens"] == 32
            assert body["store"] is False
            assert body["text"] == {"format": {"type": "json_object"}}
            assert body["reasoning"] == {"effort": "low"}
            assert response.text == "ok"
            assert response.prompt_tokens == 4
            assert response.completion_tokens == 2
            assert response.reasoning_tokens == 0

        asyncio.run(run())

    def test_structured_response_format_is_scoped_to_capable_provider(self) -> None:
        async def run() -> None:
            requests: list[dict[str, object]] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(json.loads(request.content))
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "{}"}}]},
                    request=request,
                )

            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "coach_provider_output", "strict": True, "schema": {}},
            }
            async with (
                OpenAICompatibleProvider(
                    base_url="https://remote.example/v1",
                    transport=httpx.MockTransport(handler),
                ) as remote,
                OpenAICompatibleProvider(
                    base_url="http://127.0.0.1:11434/v1",
                    supports_json_schema=True,
                    transport=httpx.MockTransport(handler),
                ) as local,
            ):
                await remote.complete(_messages(), response_format=response_format)
                await local.complete(_messages(), response_format=response_format)

            assert requests[0]["response_format"] == {"type": "json_object"}
            assert requests[1]["response_format"] == response_format

        asyncio.run(run())

    def test_glm_request_rejects_cap_above_model_limit(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.7-flash",
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            ),
        )
        with pytest.raises(ValueError, match="131072"):
            asyncio.run(provider.complete(_messages(), max_tokens=131_073))
        asyncio.run(provider.aclose())

    def test_serializes_assistant_tool_calls_and_tool_results(self) -> None:
        async def run() -> None:
            captured: dict[str, object] = {}

            def handler(request: httpx.Request) -> httpx.Response:
                captured["json"] = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                    request=request,
                )

            messages = [
                ProviderMessage(role="user", content="look up the item"),
                ProviderMessage(
                    role="assistant",
                    tool_calls=(
                        ProviderToolCall(
                            id="call-1",
                            name="item.lookup",
                            arguments='{"query":"bearing"}',
                        ),
                    ),
                ),
                ProviderMessage(
                    role="tool",
                    tool_call_id="call-1",
                    name="item.lookup",
                    content="item found",
                ),
            ]
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1",
                transport=httpx.MockTransport(handler),
            ) as provider:
                await provider.complete(messages)
            body = captured["json"]
            assert isinstance(body, dict)
            wire_messages = body["messages"]
            assert isinstance(wire_messages, list)
            assert wire_messages[1] == {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "item.lookup",
                            "arguments": '{"query":"bearing"}',
                        },
                    }
                ],
            }
            assert wire_messages[2] == {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "item.lookup",
                "content": "item found",
            }

        asyncio.run(run())

    def test_non_success_status_fails_closed(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns({"error": "boom"}, status=500)
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                with pytest.raises(ProviderError) as caught:
                    await provider.complete(_messages())
            assert caught.value.failure_code == "UPSTREAM_UNAVAILABLE"

        asyncio.run(run())

    def test_invalid_envelope_fails_closed(self) -> None:
        async def run() -> None:
            bodies: list[dict[str, object]] = [
                {"choices": []},
                {"unexpected": True},
            ]
            for body in bodies:
                transport = _transport_that_returns(body)
                async with OpenAICompatibleProvider(
                    base_url="http://127.0.0.1:11434/v1", transport=transport
                ) as provider:
                    with pytest.raises(ProviderError):
                        await provider.complete(_messages())

        asyncio.run(run())

    def test_ignores_non_authoritative_wire_metadata(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "system_fingerprint": "backend-version",
                    "service_tier": "default",
                    "choices": [
                        {
                            "logprobs": None,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                                "provider_metadata": {"route": "secondary"},
                            },
                        }
                    ],
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                response = await provider.complete(_messages())
            assert response.text == "ok"

        asyncio.run(run())

    def test_timeout_fails_closed(self) -> None:
        async def run() -> None:
            transport = _transport_that_raises(httpx.TimeoutException("slow"))
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", timeout_seconds=0.1, transport=transport
            ) as provider:
                with pytest.raises(ProviderError):
                    await provider.complete(_messages())

        asyncio.run(run())

    def test_secret_never_appears_in_error(self) -> None:
        async def run() -> None:
            transport = _transport_that_raises(httpx.TimeoutException("slow"))
            secret = "super-secret-key-value"
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1",
                api_key=SecretStr(secret),
                transport=transport,
            ) as provider:
                with pytest.raises(ProviderError) as caught:
                    await provider.complete(_messages())
            assert secret not in str(caught.value)
            assert secret not in repr(provider)

        asyncio.run(run())

    def test_rejects_empty_messages(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "x"}}]}
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                with pytest.raises(ProviderError):
                    await provider.complete([])

        asyncio.run(run())


class TestFailoverProvider:
    def test_primary_success_does_not_call_any_fallback(self) -> None:
        async def run() -> None:
            providers = [
                _ScriptedProvider("qwen8", ProviderResponse(text="primary")),
                _ScriptedProvider("glm", ProviderResponse(text="assist")),
                _ScriptedProvider("grok", ProviderResponse(text="backup")),
                _ScriptedProvider("qwen27", ProviderResponse(text="slow")),
            ]
            chain = FailoverProvider(*providers)
            response = await chain.complete(_messages())
            assert response.text == "primary"
            assert [provider.name for provider in providers if provider.calls] == ["qwen8"]

        asyncio.run(run())

    @pytest.mark.parametrize(
        ("outcomes", "expected_calls", "expected_text"),
        [
            (
                (
                    ProviderError("qwen8 unavailable", failure_code="TIMEOUT"),
                    ProviderResponse(text="glm"),
                    ProviderResponse(text="grok"),
                    ProviderResponse(text="qwen27"),
                ),
                ["qwen8", "glm"],
                "glm",
            ),
            (
                (
                    ProviderError("qwen8 unavailable", failure_code="TRANSPORT_ERROR"),
                    ProviderError("glm unavailable", failure_code="UPSTREAM_UNAVAILABLE"),
                    ProviderResponse(text="grok"),
                    ProviderResponse(text="qwen27"),
                ),
                ["qwen8", "glm", "grok"],
                "grok",
            ),
            (
                (
                    ProviderError("qwen8 unavailable", failure_code="RATE_LIMITED"),
                    ProviderError("glm unavailable", failure_code="RESPONSE_SCHEMA"),
                    ProviderError("grok unavailable", failure_code="TIMEOUT"),
                    ProviderResponse(text="qwen27"),
                ),
                ["qwen8", "glm", "grok", "qwen27"],
                "qwen27",
            ),
        ],
    )
    def test_provider_failures_follow_the_conditional_priority_chain(
        self,
        outcomes: tuple[ProviderResponse | ProviderError, ...],
        expected_calls: list[str],
        expected_text: str,
    ) -> None:
        async def run() -> None:
            providers = [
                _ScriptedProvider(name, outcome)
                for name, outcome in zip(("qwen8", "glm", "grok", "qwen27"), outcomes, strict=True)
            ]
            chain = FailoverProvider(*providers)
            response = await chain.complete(_messages())
            assert response.text == expected_text
            assert [provider.name for provider in providers if provider.calls] == expected_calls

        asyncio.run(run())

    @pytest.mark.parametrize(
        "failure_code",
        [
            "INVALID_CONFIGURATION",
            "HTTP_ERROR",
            "BUDGET_EXCEEDED",
            "USAGE_MISSING",
            "TOOL_CALL",
            "CONTEXT_BUDGET",
        ],
    )
    def test_terminal_provider_failure_does_not_trigger_fallback(self, failure_code: str) -> None:
        async def run() -> None:
            providers = [
                _ScriptedProvider("qwen8", ProviderError("terminal", failure_code=failure_code)),
                _ScriptedProvider("glm", ProviderResponse(text="must not run")),
                _ScriptedProvider("grok", ProviderResponse(text="must not run")),
                _ScriptedProvider("qwen27", ProviderResponse(text="must not run")),
            ]
            chain = FailoverProvider(*providers)
            with pytest.raises(ProviderError) as caught:
                await chain.complete(_messages())
            assert caught.value.failure_code == failure_code
            assert [provider.name for provider in providers if provider.calls] == ["qwen8"]

        asyncio.run(run())

    def test_candidate_iterator_advances_after_malformed_or_unknown_response(self) -> None:
        async def run() -> None:
            providers = [
                _ScriptedProvider("qwen8", ProviderResponse(text='{"answer_status":"UNKNOWN"}')),
                _ScriptedProvider("glm", ProviderResponse(text='{"not":"coach"}')),
                _ScriptedProvider("grok", ProviderResponse(text="production")),
                _ScriptedProvider("qwen27", ProviderResponse(text="must not run")),
            ]
            chain = FailoverProvider(*providers)
            iterator = chain.iter_candidates(
                _messages(), tools=[], model=None, max_tokens=None, response_format=None
            )
            first = await anext(iterator)
            second = await anext(iterator)
            third = await anext(iterator)
            assert [first.text, second.text, third.text] == [
                '{"answer_status":"UNKNOWN"}',
                '{"not":"coach"}',
                "production",
            ]
            await iterator.aclose()
            assert [provider.name for provider in providers if provider.calls] == [
                "qwen8",
                "glm",
                "grok",
            ]

        asyncio.run(run())

    def test_candidate_iterator_skips_transport_failure_once(self) -> None:
        async def run() -> None:
            providers = [
                _ScriptedProvider("qwen8", ProviderError("down", failure_code="TIMEOUT")),
                _ScriptedProvider("glm", ProviderError("glm unavailable", failure_code="TIMEOUT")),
                _ScriptedProvider("grok", ProviderResponse(text="production")),
                _ScriptedProvider("qwen27", ProviderResponse(text="must not run")),
            ]
            chain = FailoverProvider(*providers)
            iterator = chain.iter_candidates(
                _messages(), tools=[], model=None, max_tokens=None, response_format=None
            )
            response = await anext(iterator)
            assert response.text == "production"
            assert [provider.name for provider in providers if provider.calls] == [
                "qwen8",
                "glm",
                "grok",
            ]
            await iterator.aclose()

        asyncio.run(run())

    def test_concurrent_requests_do_not_share_attempt_state(self) -> None:
        async def run() -> None:
            primary = _ScriptedProvider(
                "qwen8",
                ProviderError("first down", failure_code="TIMEOUT"),
                ProviderError("second down", failure_code="TIMEOUT"),
            )
            fallback = _ScriptedProvider(
                "glm",
                ProviderResponse(text="fallback-1"),
                ProviderResponse(text="fallback-2"),
            )
            chain = FailoverProvider(primary, fallback)
            responses = await asyncio.gather(
                chain.complete(_messages(text="one")),
                chain.complete(_messages(text="two")),
            )
            assert {response.text for response in responses} == {"fallback-1", "fallback-2"}
            assert primary.calls == 2
            assert fallback.calls == 2

        asyncio.run(run())


class TestProviderFromEnvironment:
    @pytest.fixture(autouse=True)
    def _without_fallback_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (
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
            PROVIDER_FALLBACK_API_KEY_ENV,
            PROVIDER_FALLBACK_BASE_URL_ENV,
            PROVIDER_FALLBACK_MODEL_ENV,
            PROVIDER_FALLBACK_REASONING_EFFORT_ENV,
            PROVIDER_FALLBACK_THINKING_ENV,
            PROVIDER_FALLBACK_PROXY_ENV,
        ):
            monkeypatch.delenv(name, raising=False)

    def test_missing_base_url_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PROVIDER_BASE_URL_ENV, raising=False)
        monkeypatch.delenv(PROVIDER_API_KEY_ENV, raising=False)
        with pytest.raises(ProviderError):
            provider_from_environment()

    def test_reads_environment_and_hides_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://api.example.com/v1")
        monkeypatch.setenv(PROVIDER_API_KEY_ENV, "sk-secret-123")
        monkeypatch.setenv(PROVIDER_MODEL_ENV, "model-x")
        monkeypatch.setenv(PROVIDER_REASONING_EFFORT_ENV, "low")
        monkeypatch.setenv(PROVIDER_THINKING_ENV, "disabled")
        monkeypatch.setenv(PROVIDER_PROXY_ENV, "http://127.0.0.1:7899")
        provider = provider_from_environment(
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            )
        )
        assert provider._model == "model-x"
        assert provider._reasoning_effort == "low"
        assert provider._thinking == "disabled"
        assert provider._proxy == "http://127.0.0.1:7899"
        assert "sk-secret-123" not in repr(provider)

    def test_named_environment_builds_the_new_priority_chain(self) -> None:
        values = _new_provider_environment()
        provider = provider_from_environment(
            environ=values,
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            ),
        )
        assert isinstance(provider, FailoverProvider)
        assert [candidate._model for candidate in provider._providers] == [
            "qwen3:8b",
            "glm-5.3-flash",
            "grok-4.5",
            "qwen3.8:27b",
        ]
        assert provider._providers[0]._temperature == 0.0
        assert provider._providers[0]._client.timeout.read == LOCAL_PROVIDER_TIMEOUT_SECONDS
        assist = provider._providers[1]
        assert isinstance(assist, OpenAICompatibleProvider)
        assert assist._reasoning_effort == "low"
        backup = provider._providers[2]
        assert isinstance(backup, OpenAICompatibleProvider)
        assert backup._wire_api == "responses"
        assert backup._reasoning_effort == "low"
        assert provider._providers[3]._temperature == 0.0
        assert provider._providers[3]._client.timeout.read is None
        assert provider_model(values) == "qwen3:8b"
        assert provider_max_output_tokens(values) == PROVIDER_MAX_OUTPUT_TOKENS
        assert provider_thinking_mode(values) is None
        assert "assist-secret" not in repr(provider)
        assert "backup-secret" not in repr(provider)
        asyncio.run(provider.aclose())

    @pytest.mark.parametrize(
        ("role", "expected_model", "expected_wire_api", "expected_read_timeout"),
        [
            ("primary", "qwen3:8b", "chat_completions", LOCAL_PROVIDER_TIMEOUT_SECONDS),
            ("assist", "glm-5.3-flash", "chat_completions", 60.0),
            ("backup", "grok-4.5", "responses", 60.0),
            ("last_local", "qwen3.8:27b", "chat_completions", None),
        ],
    )
    def test_role_connectivity_probe_builds_one_provider_without_fallback(
        self,
        role: ProviderRole,
        expected_model: str,
        expected_wire_api: str,
        expected_read_timeout: float | None,
    ) -> None:
        async def run() -> None:
            calls = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                if expected_wire_api == "responses":
                    body: dict[str, object] = {
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "OK"}],
                            }
                        ],
                        "usage": {
                            "input_tokens": 2,
                            "output_tokens": 1,
                            "total_tokens": 3,
                        },
                    }
                else:
                    body = {
                        "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                    }
                return httpx.Response(200, json=body, request=request)

            provider = provider_for_role(
                role,
                environ=_new_provider_environment(),
                transport=httpx.MockTransport(handler),
            )
            assert isinstance(provider, OpenAICompatibleProvider)
            assert not isinstance(provider, FailoverProvider)
            assert provider._model == expected_model
            assert provider._wire_api == expected_wire_api
            assert provider._client.timeout.read == expected_read_timeout
            response = await provider.complete(_messages(), tools=[], max_tokens=32)
            assert response.text == "OK"
            assert calls == 1
            await provider.aclose()

        asyncio.run(run())

    def test_legacy_role_connectivity_probe_rejects_fallback_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
        monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
        with pytest.raises(ProviderError, match="single-role connectivity") as caught:
            provider_for_role("primary")
        assert caught.value.failure_code == "INVALID_CONFIGURATION"

    @pytest.mark.parametrize(
        "missing",
        [ASSIST_API_KEY_ENV, BACKUP_MODEL_ENV, BACKUP_OLLAMA_BASE_URL_ENV],
    )
    def test_named_environment_requires_each_provider_slot(self, missing: str) -> None:
        values = _new_provider_environment()
        values.pop(missing)
        with pytest.raises(ProviderError) as caught:
            provider_from_environment(environ=values)
        assert caught.value.failure_code == "INVALID_CONFIGURATION"

    def test_named_environment_rejects_wrong_role_model(self) -> None:
        values = _new_provider_environment()
        values[ASSIST_MODEL_ENV] = "glm-4.7-flash"
        with pytest.raises(ProviderError) as caught:
            provider_from_environment(environ=values)
        assert caught.value.failure_code == "INVALID_CONFIGURATION"

    def test_named_environment_does_not_merge_with_legacy_selection(self) -> None:
        values = _new_provider_environment()
        values[PROVIDER_BASE_URL_ENV] = "https://legacy.example/v1"
        with pytest.raises(ProviderError) as caught:
            provider_from_environment(environ=values)
        assert caught.value.failure_code == "INVALID_CONFIGURATION"

    def test_defaults_primary_model_to_glm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://open.bigmodel.cn/api/paas/v4")
        monkeypatch.delenv(PROVIDER_MODEL_ENV, raising=False)
        monkeypatch.delenv(PROVIDER_THINKING_ENV, raising=False)
        provider = provider_from_environment(
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            )
        )
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider._model == DEFAULT_PROVIDER_MODEL
        assert provider._thinking == "enabled"
        asyncio.run(provider.aclose())

    def test_fallback_key_enables_failover_after_rate_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run() -> None:
            monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
            monkeypatch.setenv(PROVIDER_API_KEY_ENV, "primary-secret")
            monkeypatch.setenv(PROVIDER_MODEL_ENV, "glm-4.7-flash")
            monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
            monkeypatch.setenv(PROVIDER_FALLBACK_BASE_URL_ENV, "https://fallback.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_MODEL_ENV, "backup-model")
            for name in (
                PROVIDER_FALLBACK_PROXY_ENV,
                PROVIDER_FALLBACK_REASONING_EFFORT_ENV,
                PROVIDER_FALLBACK_THINKING_ENV,
            ):
                monkeypatch.delenv(name, raising=False)
            hosts: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                hosts.append(request.url.host or "")
                if request.url.host == "primary.example":
                    return httpx.Response(429, json={"error": "rate limited"}, request=request)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "backup"}}]},
                    request=request,
                )

            provider = provider_from_environment(transport=httpx.MockTransport(handler))
            assert isinstance(provider, FailoverProvider)
            response = await provider.complete(_messages())
            assert response.text == "backup"
            assert hosts == ["primary.example", "fallback.example"]
            assert "primary-secret" not in repr(provider)
            assert "fallback-secret" not in repr(provider)
            await provider.aclose()

        asyncio.run(run())

    def test_fallback_reuses_primary_settings_when_only_key_is_filled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run() -> None:
            monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
            monkeypatch.setenv(PROVIDER_API_KEY_ENV, "primary-secret")
            monkeypatch.setenv(PROVIDER_MODEL_ENV, "glm-4.7-flash")
            monkeypatch.setenv(PROVIDER_THINKING_ENV, "disabled")
            monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
            for name in (
                PROVIDER_FALLBACK_BASE_URL_ENV,
                PROVIDER_FALLBACK_MODEL_ENV,
                PROVIDER_FALLBACK_PROXY_ENV,
                PROVIDER_FALLBACK_REASONING_EFFORT_ENV,
                PROVIDER_FALLBACK_THINKING_ENV,
            ):
                monkeypatch.delenv(name, raising=False)
            requests: list[tuple[str, str, object]] = []

            def handler(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                requests.append(
                    (
                        request.url.host or "",
                        request.headers.get("authorization", ""),
                        body["model"],
                    )
                )
                if len(requests) == 1:
                    return httpx.Response(503, json={"error": "offline"}, request=request)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "backup"}}]},
                    request=request,
                )

            provider = provider_from_environment(transport=httpx.MockTransport(handler))
            assert isinstance(provider, FailoverProvider)
            response = await provider.complete(_messages())
            assert response.text == "backup"
            assert requests == [
                ("primary.example", "Bearer primary-secret", "glm-4.7-flash"),
                ("primary.example", "Bearer fallback-secret", "glm-4.7-flash"),
            ]
            assert provider._providers[1]._thinking == "disabled"
            await provider.aclose()

        asyncio.run(run())

    def test_provider_chain_uses_local_models_before_paid_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run() -> None:
            monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
            monkeypatch.setenv(PROVIDER_FALLBACK_BASE_URL_ENV, "https://fallback.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_MODEL_ENV, "backup-model")
            monkeypatch.setenv(PROVIDER_LOCAL_BASE_URL_ENV, "http://127.0.0.1:11434/v1")
            monkeypatch.setenv(PROVIDER_LOCAL_SMALL_MODEL_ENV, "qwen3:8b")
            monkeypatch.setenv(PROVIDER_LOCAL_LARGE_MODEL_ENV, "qwen3.8:27b")
            requests: list[tuple[str, str]] = []

            def handler(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                requests.append((request.url.host or "", body["model"]))
                if len(requests) == 1:
                    return httpx.Response(429, json={"error": "rate limited"}, request=request)
                if len(requests) in {2, 3}:
                    raise httpx.ReadError("connection reset", request=request)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "backup"}}]},
                    request=request,
                )

            provider = provider_from_environment(transport=httpx.MockTransport(handler))
            assert isinstance(provider, FailoverProvider)
            response = await provider.complete(_messages())
            assert response.text == "backup"
            assert requests == [
                ("primary.example", "glm-4.7-flash"),
                ("127.0.0.1", "qwen3:8b"),
                ("127.0.0.1", "qwen3.8:27b"),
                ("fallback.example", "backup-model"),
            ]
            assert provider._providers[1]._thinking == "disabled"
            assert provider._providers[2]._thinking == "disabled"
            assert provider._providers[1]._temperature == 0.0
            assert provider._providers[2]._temperature == 0.0
            assert provider._providers[1]._supports_json_schema is True
            assert provider._providers[2]._client.timeout.read == LOCAL_PROVIDER_TIMEOUT_SECONDS
            await provider.aclose()

        asyncio.run(run())

    def test_each_provider_is_called_once_on_invalid_envelopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run() -> None:
            monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
            monkeypatch.setenv(PROVIDER_FALLBACK_BASE_URL_ENV, "https://fallback.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_MODEL_ENV, "backup-model")
            monkeypatch.setenv(PROVIDER_LOCAL_BASE_URL_ENV, "http://127.0.0.1:11434/v1")
            monkeypatch.setenv(PROVIDER_LOCAL_SMALL_MODEL_ENV, "qwen3:8b")
            requests: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request.url.host or "")
                if len(requests) == 1:
                    return httpx.Response(429, json={"error": "rate limited"}, request=request)
                if len(requests) == 2:
                    return httpx.Response(200, content=b'{"unexpected":true}', request=request)
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "backup"}}]},
                    request=request,
                )

            provider = provider_from_environment(transport=httpx.MockTransport(handler))
            assert isinstance(provider, FailoverProvider)
            response = await provider.complete(_messages())
            assert response.text == "backup"
            assert requests == [
                "primary.example",
                "127.0.0.1",
                "fallback.example",
            ]
            await provider.aclose()

        asyncio.run(run())

    def test_fallback_caps_primary_budget_to_backup_model_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run() -> None:
            monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
            monkeypatch.setenv(PROVIDER_MODEL_ENV, "glm-4.7-flash")
            monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
            monkeypatch.setenv(PROVIDER_FALLBACK_BASE_URL_ENV, "https://fallback.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_MODEL_ENV, "backup-model")
            request_budgets: list[int | None] = []

            def handler(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                request_budgets.append(body.get("max_tokens"))
                if len(request_budgets) == 1:
                    return httpx.Response(429, json={"error": "rate limited"}, request=request)
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"role": "assistant", "content": "backup"}}],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 1,
                            "total_tokens": 11,
                        },
                    },
                    request=request,
                )

            provider = provider_from_environment(transport=httpx.MockTransport(handler))
            assert isinstance(provider, FailoverProvider)
            response = await provider.complete(
                _messages(), max_tokens=GLM_4_7_FLASH_DEFAULT_MAX_OUTPUT_TOKENS
            )
            assert response.text == "backup"
            assert request_budgets == [GLM_4_7_FLASH_DEFAULT_MAX_OUTPUT_TOKENS, 8192]
            await provider.aclose()

        asyncio.run(run())

    def test_fallback_does_not_mask_auth_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def run() -> None:
            monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
            monkeypatch.setenv(PROVIDER_FALLBACK_BASE_URL_ENV, "https://fallback.example/v1")
            hosts: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                hosts.append(request.url.host or "")
                return httpx.Response(401, json={"error": "unauthorized"}, request=request)

            provider = provider_from_environment(transport=httpx.MockTransport(handler))
            assert isinstance(provider, FailoverProvider)
            with pytest.raises(ProviderError) as caught:
                await provider.complete(_messages())
            assert caught.value.failure_code == "HTTP_ERROR"
            assert hosts == ["primary.example"]
            await provider.aclose()

        asyncio.run(run())

    def test_fallback_moves_past_unusable_primary_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run() -> None:
            monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
            monkeypatch.setenv(PROVIDER_FALLBACK_API_KEY_ENV, "fallback-secret")
            monkeypatch.setenv(PROVIDER_FALLBACK_BASE_URL_ENV, "https://fallback.example/v1")
            hosts: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                hosts.append(request.url.host or "")
                if len(hosts) == 1:
                    return httpx.Response(
                        200,
                        json={"choices": [{"message": {"role": "assistant", "content": None}}]},
                        request=request,
                    )
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"role": "assistant", "content": "backup"}}]},
                    request=request,
                )

            provider = provider_from_environment(transport=httpx.MockTransport(handler))
            assert isinstance(provider, FailoverProvider)
            response = await provider.complete(_messages())
            assert response.text == "backup"
            assert hosts == ["primary.example", "fallback.example"]
            await provider.aclose()

        asyncio.run(run())

    def test_fallback_settings_require_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://primary.example/v1")
        monkeypatch.setenv(PROVIDER_FALLBACK_BASE_URL_ENV, "https://fallback.example/v1")
        monkeypatch.delenv(PROVIDER_FALLBACK_API_KEY_ENV, raising=False)
        with pytest.raises(ProviderError, match="FALLBACK_API_KEY") as caught:
            provider_from_environment()
        assert caught.value.failure_code == "INVALID_CONFIGURATION"

    def test_glm_defaults_to_enabled_thinking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://open.bigmodel.cn/api/paas/v4")
        monkeypatch.setenv(PROVIDER_MODEL_ENV, "glm-4.7-flash")
        monkeypatch.delenv(PROVIDER_THINKING_ENV, raising=False)
        provider = provider_from_environment(
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            )
        )
        assert provider._thinking == "enabled"

    def test_unrelated_model_does_not_receive_glm_thinking_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://api.example.com/v1")
        monkeypatch.setenv(PROVIDER_MODEL_ENV, "other-model")
        monkeypatch.delenv(PROVIDER_THINKING_ENV, raising=False)
        provider = provider_from_environment(
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            )
        )
        assert provider._thinking is None

    def test_invalid_origin_from_environment_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://user:pass@host/v1?x=1")
        with pytest.raises(ValueError):
            provider_from_environment()


class TestBaseUrlCompatibility:
    def test_full_endpoint_base_url_is_not_duplicated(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://ccapi.us/v1/chat/completions",
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            ),
        )
        assert provider._chat_url == "https://ccapi.us/v1/chat/completions"

    def test_root_base_url_gets_chat_path_appended(self) -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://ccapi.us/v1",
            transport=_transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            ),
        )
        assert provider._chat_url == "https://ccapi.us/v1/chat/completions"


class TestRefusalFieldCompat:
    def test_message_with_refusal_field_is_accepted(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "hello",
                                "refusal": None,
                            }
                        }
                    ]
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                response = await provider.complete(_messages())
            assert response.text == "hello"

        asyncio.run(run())


class TestZhipuResponseCompat:
    def test_parses_request_id_reasoning_content_and_inclusive_usage(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "id": "completion-id",
                    "request_id": "request-id",
                    "created": 123,
                    "model": "glm-4.7-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "pong",
                                "reasoning_content": "hidden reasoning",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 6,
                        "completion_tokens": 16,
                        "completion_tokens_details": {"reasoning_tokens": 16},
                        "prompt_tokens_details": {"cached_tokens": 4},
                        "total_tokens": 22,
                    },
                }
            )
            async with OpenAICompatibleProvider(
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-4.7-flash",
                transport=transport,
            ) as provider:
                response = await provider.complete(_messages(), max_tokens=16)
            assert response.text == "pong"
            assert response.completion_tokens == 16
            assert response.reasoning_tokens == 16
            assert response.reasoning_content_present is True

        asyncio.run(run())

    def test_reasoning_content_never_substitutes_missing_final_content(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": "hidden reasoning",
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                }
            )
            async with OpenAICompatibleProvider(
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-4.7-flash",
                transport=transport,
            ) as provider:
                with pytest.raises(ProviderError, match="no final content") as caught:
                    await provider.complete(_messages(), max_tokens=65_536)
            assert caught.value.failure_code == "RESPONSE_CONTENT_MISSING"

        asyncio.run(run())


class TestMaxTokensRequestBudget:
    def test_completion_over_budget_is_rejected(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 999, "total_tokens": 1009},
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                with pytest.raises(ProviderError, match="max_tokens budget"):
                    await provider.complete(_messages(), max_tokens=16)

        asyncio.run(run())

    def test_completion_within_budget_is_accepted(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                response = await provider.complete(_messages(), max_tokens=16)
            assert response.completion_tokens == 4

        asyncio.run(run())

    def test_total_tokens_can_exceed_output_cap_when_output_is_within_cap(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                response = await provider.complete(_messages(), max_tokens=16)
            assert response.completion_tokens == 10

        asyncio.run(run())

    def test_reasoning_tokens_are_counted_and_exposed(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 20,
                        "completion_tokens_details": {"reasoning_tokens": 6},
                    },
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                response = await provider.complete(_messages(), max_tokens=16)
            assert response.completion_tokens == 4
            assert response.reasoning_tokens == 6

        asyncio.run(run())

    def test_reasoning_tokens_over_budget_are_rejected(self) -> None:
        async def run() -> None:
            transport = _transport_that_returns(
                {
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 100,
                        "total_tokens": 310,
                        "completion_tokens_details": {"reasoning_tokens": 210},
                    },
                }
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                with pytest.raises(ProviderError, match="300 > 256") as caught:
                    await provider.complete(_messages(), max_tokens=256)
            assert caught.value.prompt_tokens == 10
            assert caught.value.completion_tokens == 100
            assert caught.value.reasoning_tokens == 210

        asyncio.run(run())

    def test_budget_absent_usage_fails_closed(self) -> None:
        # 服务商不返回 usage 时无法证明预算遵守, 必须拒绝真实模型结果。
        async def run() -> None:
            transport = _transport_that_returns(
                {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
            )
            async with OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", transport=transport
            ) as provider:
                with pytest.raises(ProviderError, match="omitted usage"):
                    await provider.complete(_messages(), max_tokens=16)

        asyncio.run(run())
