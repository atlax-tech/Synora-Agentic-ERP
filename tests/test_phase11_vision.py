from __future__ import annotations

import base64
import json

import httpx
import pytest

from labs.web_gui.vision import (
    MAX_IMAGE_BYTES,
    VisionProbeError,
    parse_vision_observation,
    probe_vision,
)

PNG = b"\x89PNG\r\n\x1a\nsynthetic-pixels"


def _env() -> dict[str, str]:
    return {
        "ASSIST_BASE_URL": "https://vision.example/v1",
        "ASSIST_API_KEY": "secret-key",
        "ASSIST_MODEL": "vision-test",
    }


def test_probe_uses_existing_role_and_never_returns_key() -> None:
    seen: list[dict[str, object]] = []

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
    )

    assert result.status == "PASS"
    assert result.observation is not None
    assert result.observation.currency == "CNY"
    assert result.prompt_tokens == 10
    assert seen[0]["model"] == "vision-test"
    assert base64.b64encode(PNG).decode() in json.dumps(seen[0])
    assert "secret-key" not in repr(result)


def test_probe_without_config_is_an_explicit_block() -> None:
    result = probe_vision("read", [PNG], environ={})
    assert result.status == "VISION_PROVIDER_UNAVAILABLE"
    assert result.attempts == ()


def test_image_and_response_limits_fail_closed() -> None:
    with pytest.raises(VisionProbeError, match="IMAGE_INVALID"):
        probe_vision("read", [b"not-png"], environ={})
    with pytest.raises(VisionProbeError, match="IMAGE_INVALID"):
        probe_vision("read", [PNG[:8] + b"x" * MAX_IMAGE_BYTES], environ={})
    with pytest.raises(VisionProbeError, match="RESPONSE_SCHEMA"):
        parse_vision_observation("{}", PNG)
