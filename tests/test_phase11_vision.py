from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from labs.web_gui.vision import (
    MAX_IMAGE_BYTES,
    VisionProbeError,
    parse_vision_observation,
    probe_vision,
    request_vision_json,
)

PNG = b"\x89PNG\r\n\x1a\nsynthetic-pixels"


def _env() -> dict[str, str]:
    return {
        "ASSIST_BASE_URL": "https://vision.example/v1",
        "ASSIST_API_KEY": "secret-key",
        "ASSIST_MODEL": "vision-test",
    }


def test_probe_uses_existing_role_and_never_returns_key() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "purchase_order": "PUR-ORD-0001",
                                    "supplier": "Supplier A",
                                    "status": "To Receive and Bill",
                                    "currency": "CNY",
                                    "complete": True,
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    result = probe_vision(
        "Read the four visible purchase fields and return strict JSON.",
        [PNG],
        environ=_env(),
        transport=httpx.MockTransport(handler),
        expected_observations=(
            {
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
        ),
    )

    assert result.status == "PASS"
    assert result.observation is not None
    assert result.observation.currency == "CNY"
    assert result.prompt_tokens == 10
    assert seen[0]["model"] == "vision-test"
    assert seen[0]["messages"][0]["content"][1]["image_url"]["detail"] == "high"
    assert base64.b64encode(PNG).decode() in json.dumps(seen[0])
    assert "secret-key" not in repr(result)
    attempt = result.attempts[0]
    assert attempt.protocol == "chat_completions"
    assert attempt.http_status == 200
    assert attempt.response_shape == "choices"
    assert attempt.response_content_type == "application/json"
    assert attempt.prompt_tokens == 10
    assert attempt.completion_tokens == 20
    assert attempt.elapsed_ms is not None


def test_probe_reads_standard_responses_nested_output() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "purchase_order": "PUR-ORD-0001",
                                        "supplier": "Supplier A",
                                        "status": "To Receive and Bill",
                                        "currency": "CNY",
                                        "complete": True,
                                    }
                                ),
                            }
                        ],
                    },
                ],
                "usage": {"input_tokens": 4, "output_tokens": 7},
            },
        )

    result = probe_vision(
        "read",
        [PNG],
        environ={
            "BACKUP_BASE_URL": "https://vision.example/v1",
            "BACKUP_API_KEY": "secret-key",
            "BACKUP_MODEL": "vision-test",
        },
        transport=httpx.MockTransport(handler),
        expected_observations=(
            {
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
        ),
    )

    assert result.status == "PASS"
    assert result.prompt_tokens == 4
    assert result.completion_tokens == 7
    assert result.attempts[0].protocol == "responses"
    assert result.attempts[0].response_shape == "output"


def test_probe_classifies_http_and_json_failures_without_response_body() -> None:
    def http_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    limited = probe_vision(
        "read",
        [PNG],
        environ=_env(),
        transport=httpx.MockTransport(http_handler),
        expected_observations=({"purchase_order": "PUR-ORD-0001"},),
    )
    attempt = limited.attempts[0]
    assert attempt.failure_code == "RATE_LIMITED"
    assert attempt.failure_stage == "http_status"
    assert attempt.http_status == 429
    assert "rate limited" not in repr(limited)

    def invalid_json(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    invalid = probe_vision(
        "read",
        [PNG],
        environ=_env(),
        transport=httpx.MockTransport(invalid_json),
        expected_observations=({"purchase_order": "PUR-ORD-0001"},),
    )
    attempt = invalid.attempts[0]
    assert attempt.failure_code == "RESPONSE_JSON_INVALID"
    assert attempt.failure_stage == "json_decode"
    assert attempt.http_status == 200
    assert attempt.response_content_type == "text/plain"
    assert attempt.response_shape == "invalid_json"


def test_probe_classifies_connect_and_read_timeouts() -> None:
    def connect_timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection refused")

    connected = probe_vision(
        "read",
        [PNG],
        environ=_env(),
        transport=httpx.MockTransport(connect_timeout),
        expected_observations=({"purchase_order": "PUR-ORD-0001"},),
    )
    assert connected.attempts[0].failure_code == "CONNECT_TIMEOUT"
    assert connected.attempts[0].failure_stage == "connect"

    def read_timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("response took too long")

    read = probe_vision(
        "read",
        [PNG],
        environ=_env(),
        transport=httpx.MockTransport(read_timeout),
        expected_observations=({"purchase_order": "PUR-ORD-0001"},),
    )
    assert read.attempts[0].failure_code == "READ_TIMEOUT"
    assert read.attempts[0].failure_stage == "read"


def test_probe_accepts_a_strict_json_code_fence_only() -> None:
    result = parse_vision_observation(
        "```json\n"
        + json.dumps(
            {
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
                "complete": True,
            }
        )
        + "\n```",
        PNG,
    )
    assert result.purchase_order == "PUR-ORD-0001"

    with pytest.raises(VisionProbeError, match="RESPONSE_SCHEMA"):
        parse_vision_observation("prefix ```json\n{}\n```", PNG)


def test_request_vision_json_returns_untrusted_decision_without_oracle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["messages"][0]["content"][1]["image_url"]["detail"] == "high"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"action_type":"finish","fields":{}}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    result = request_vision_json(
        "return one action",
        [PNG],
        role="assist",
        environ=_env(),
        transport=httpx.MockTransport(handler),
    )

    assert result.payload == {"action_type": "finish", "fields": {}}
    assert result.role == "assist"
    assert result.prompt_tokens == 1


def test_request_vision_json_forwards_output_budget() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action_type":"finish"}'}}]},
        )

    request_vision_json(
        "return one action",
        [PNG],
        role="assist",
        environ=_env(),
        transport=httpx.MockTransport(handler),
        max_output_tokens=7,
    )

    assert seen[0]["max_tokens"] == 7


def test_request_vision_json_preserves_safe_diagnostic_on_invalid_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    with pytest.raises(VisionProbeError) as caught:
        request_vision_json(
            "return one action",
            [PNG],
            role="assist",
            environ=_env(),
            transport=httpx.MockTransport(handler),
        )

    assert caught.value.code == "RESPONSE_JSON_INVALID"
    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.failure_stage == "json_decode"
    assert caught.value.diagnostic.response_content_type == "text/plain"
    assert "secret-key" not in repr(caught.value)


def test_probe_without_config_is_an_explicit_block() -> None:
    result = probe_vision("read", [PNG], environ={})
    assert result.status == "VISION_PROVIDER_UNAVAILABLE"
    assert result.attempts == ()


def test_probe_without_trusted_oracle_cannot_pass() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    result = probe_vision(
        "read",
        [PNG],
        environ=_env(),
        transport=httpx.MockTransport(handler),
    )
    assert result.status == "VISION_PROVIDER_UNAVAILABLE"
    assert called is False


def test_image_and_response_limits_fail_closed() -> None:
    with pytest.raises(VisionProbeError, match="IMAGE_INVALID"):
        probe_vision("read", [b"not-png"], environ={})
    with pytest.raises(VisionProbeError, match="IMAGE_INVALID"):
        probe_vision("read", [PNG[:8] + b"x" * MAX_IMAGE_BYTES], environ={})
    with pytest.raises(VisionProbeError, match="RESPONSE_SCHEMA"):
        parse_vision_observation("{}", PNG)


def test_contentless_or_incomplete_image_response_cannot_pass() -> None:
    with pytest.raises(VisionProbeError, match="RESPONSE_SCHEMA"):
        parse_vision_observation(
            json.dumps(
                {
                    "purchase_order": None,
                    "supplier": None,
                    "status": None,
                    "currency": None,
                    "complete": True,
                }
            ),
            PNG,
        )
    with pytest.raises(VisionProbeError, match="RESPONSE_INCOMPLETE"):
        parse_vision_observation(
            json.dumps(
                {
                    "purchase_order": "PUR-ORD-0001",
                    "supplier": "Supplier A",
                    "status": "To Receive and Bill",
                    "currency": "CNY",
                    "complete": False,
                }
            ),
            PNG,
        )


def test_two_image_probe_requires_each_observation_and_oracle_match() -> None:
    second = b"\x89PNG\r\n\x1a\nother-pixels"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert len(payload["messages"][0]["content"]) == 3
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "observations": [
                                        {
                                            "purchase_order": "PUR-ORD-0001",
                                            "supplier": "Supplier A",
                                            "status": "To Receive and Bill",
                                            "currency": "CNY",
                                            "complete": True,
                                        },
                                        {
                                            "purchase_order": "PUR-ORD-0002",
                                            "supplier": "Supplier B",
                                            "status": "Completed",
                                            "currency": "USD",
                                            "complete": True,
                                        },
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = probe_vision(
        "read",
        [PNG, second],
        environ=_env(),
        transport=httpx.MockTransport(handler),
        expected_observations=(
            {
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
            {
                "purchase_order": "PUR-ORD-0002",
                "supplier": "Supplier B",
                "status": "Completed",
                "currency": "USD",
            },
        ),
    )
    assert result.status == "PASS"
    assert len(result.observations) == 2

    mismatch = probe_vision(
        "read",
        [PNG, second],
        environ=_env(),
        transport=httpx.MockTransport(handler),
        expected_observations=(
            {
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Wrong",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
            {
                "purchase_order": "PUR-ORD-0002",
                "supplier": "Supplier B",
                "status": "Completed",
                "currency": "USD",
            },
        ),
    )
    assert mismatch.status == "VISION_PROVIDER_UNAVAILABLE"
    assert mismatch.attempts[0].failure_code == "RESPONSE_CONTENT_MISMATCH"
    assert mismatch.attempts[0].response_fields == (
        "complete",
        "currency",
        "purchase_order",
        "status",
        "supplier",
    )
    assert mismatch.attempts[0].observation_count == 2
    assert mismatch.attempts[0].declared_complete is True
    assert mismatch.attempts[0].mismatch_fields == ("supplier",)
