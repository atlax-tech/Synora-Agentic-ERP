"""P3.4 Provider 接口基线测试 (ARCHITECTURE "Model access")。

CI 使用确定性 provider; OpenAI 兼容 provider 通过 MockTransport 验证
请求/响应契约、fail-closed 与 secret 防泄漏, 不发真实网络请求。
"""

import asyncio
import json

import httpx
import pytest
from agent_runtime.providers import (
    GLM_4_7_FLASH_DEFAULT_MAX_OUTPUT_TOKENS,
    GLM_4_7_FLASH_MAX_OUTPUT_TOKENS,
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URL_ENV,
    PROVIDER_MAX_OUTPUT_TOKENS,
    PROVIDER_MAX_OUTPUT_TOKENS_ENV,
    PROVIDER_MODEL_ENV,
    PROVIDER_PROXY_ENV,
    PROVIDER_REASONING_EFFORT_ENV,
    PROVIDER_THINKING_ENV,
    DeterministicProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
    provider_from_environment,
    provider_max_output_token_limit,
    provider_max_output_tokens,
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


def test_provider_max_output_tokens_defaults_to_phase_cap() -> None:
    assert provider_max_output_tokens({}) == PROVIDER_MAX_OUTPUT_TOKENS == 1024


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

    def test_rejects_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleProvider(base_url="http://127.0.0.1:11434/v1", timeout_seconds=0)

    def test_rejects_unknown_reasoning_effort(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleProvider(
                base_url="http://127.0.0.1:11434/v1", reasoning_effort="unbounded"
            )

    def test_rejects_unknown_thinking_mode(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleProvider(base_url="http://127.0.0.1:11434/v1", thinking="maybe")

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
                with pytest.raises(ProviderError):
                    await provider.complete(_messages())

        asyncio.run(run())

    def test_invalid_envelope_fails_closed(self) -> None:
        async def run() -> None:
            bodies: list[dict[str, object]] = [
                {"choices": []},
                {"choices": [{"message": {"role": "assistant", "content": "x", "extra": 1}}]},
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


class TestProviderFromEnvironment:
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
