"""Bounded, opt-in image probing for the Phase 11 lab.

The business ProviderMessage contract stays text-only.  This module sends a
separate, size-limited image request to one already configured provider role
and treats every response as untrusted data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
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
    MODEL_PROXY_ENV,
    OLLAMA_API_KEY_ENV,
    OLLAMA_BASE_URL_ENV,
    OLLAMA_MODEL_ENV,
)

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 2
MAX_PROMPT_CHARS = 2_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_OUTPUT_TOKENS = 1_024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_ROLE_ENV: dict[str, tuple[str, str, str, bool]] = {
    "primary": (OLLAMA_BASE_URL_ENV, OLLAMA_API_KEY_ENV, OLLAMA_MODEL_ENV, True),
    "assist": (ASSIST_BASE_URL_ENV, ASSIST_API_KEY_ENV, ASSIST_MODEL_ENV, False),
    "backup": (BACKUP_BASE_URL_ENV, BACKUP_API_KEY_ENV, BACKUP_MODEL_ENV, True),
    "last_local": (
        BACKUP_OLLAMA_BASE_URL_ENV,
        BACKUP_OLLAMA_API_KEY_ENV,
        BACKUP_OLLAMA_MODEL_ENV,
        True,
    ),
}


class VisionProbeError(RuntimeError):
    """A safe, classified image request failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VisionAttempt:
    role: str
    model: str | None
    status: str
    failure_code: str | None = None


@dataclass(frozen=True)
class VisionObservation:
    purchase_order: str | None
    supplier: str | None
    status: str | None
    currency: str | None
    complete: bool
    source_image_sha256: str


@dataclass(frozen=True)
class VisionProbeResult:
    status: str
    observation: VisionObservation | None
    attempts: tuple[VisionAttempt, ...]
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    observations: tuple[VisionObservation, ...] = ()


def _image_data(images: list[bytes] | tuple[bytes, ...]) -> list[str]:
    if not images or len(images) > MAX_IMAGES:
        raise VisionProbeError("IMAGE_COUNT")
    encoded: list[str] = []
    for image in images:
        if len(image) > MAX_IMAGE_BYTES or not image.startswith(_PNG_SIGNATURE):
            raise VisionProbeError("IMAGE_INVALID")
        encoded.append("data:image/png;base64," + base64.b64encode(image).decode("ascii"))
    return encoded


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VisionProbeError("INVALID_CONFIGURATION")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VisionProbeError("INVALID_CONFIGURATION")
    return value.rstrip("/")


def _role_config(role: str, values: Mapping[str, str]) -> tuple[str, str, str, bool] | None:
    base_key, api_key, model_key, responses = _ROLE_ENV[role]
    base = values.get(base_key, "").strip()
    key = values.get(api_key, "").strip()
    model = values.get(model_key, "").strip()
    if not base and not key and not model:
        return None
    if not base or not key or not model:
        raise VisionProbeError("INVALID_CONFIGURATION")
    return _safe_url(base), key, model, responses


def _endpoint(base_url: str, responses: bool) -> str:
    suffix = "/responses" if responses else "/chat/completions"
    return base_url if base_url.endswith(suffix) else base_url + suffix


def _payload(prompt: str, images: list[str], model: str, responses: bool) -> dict[str, object]:
    if len(prompt) > MAX_PROMPT_CHARS:
        raise VisionProbeError("PROMPT_TOO_LARGE")
    if responses:
        content: list[dict[str, object]] = [{"type": "input_text", "text": prompt}]
        content.extend({"type": "input_image", "image_url": image} for image in images)
        return {
            "model": model,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
    content = [{"type": "text", "text": prompt}]
    content.extend(
        {"type": "image_url", "image_url": {"url": image, "detail": "low"}} for image in images
    )
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
    }


def _response_text(data: object) -> tuple[str, int | None, int | None]:
    if not isinstance(data, dict):
        raise VisionProbeError("RESPONSE_SCHEMA")
    usage = data.get("usage")
    prompt_tokens = completion_tokens = None
    if isinstance(usage, dict):
        if isinstance(usage.get("prompt_tokens"), int):
            prompt_tokens = usage["prompt_tokens"]
        if isinstance(usage.get("completion_tokens"), int):
            completion_tokens = usage["completion_tokens"]
        if isinstance(usage.get("input_tokens"), int):
            prompt_tokens = usage["input_tokens"]
        if isinstance(usage.get("output_tokens"), int):
            completion_tokens = usage["output_tokens"]
    output = data.get("output_text")
    if isinstance(output, str) and output.strip():
        return output, prompt_tokens, completion_tokens
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            text = message["content"]
            if text.strip():
                return text, prompt_tokens, completion_tokens
    raise VisionProbeError("RESPONSE_CONTENT_MISSING")


def parse_vision_observation(text: str, image: bytes) -> VisionObservation:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as error:
        raise VisionProbeError("RESPONSE_SCHEMA") from error
    if not isinstance(data, dict) or set(data) != {
        "purchase_order",
        "supplier",
        "status",
        "currency",
        "complete",
    }:
        raise VisionProbeError("RESPONSE_SCHEMA")
    values: dict[str, str | None] = {}
    for field in ("purchase_order", "supplier", "status", "currency"):
        value = data[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 140:
            raise VisionProbeError("RESPONSE_SCHEMA")
        values[field] = value
    if data["complete"] is not True:
        raise VisionProbeError("RESPONSE_INCOMPLETE")
    return VisionObservation(
        purchase_order=values["purchase_order"],
        supplier=values["supplier"],
        status=values["status"],
        currency=values["currency"],
        complete=data["complete"],
        source_image_sha256=hashlib.sha256(image).hexdigest(),
    )


def _parse_observations(text: str, images: Sequence[bytes]) -> tuple[VisionObservation, ...]:
    if len(images) == 1:
        return (parse_vision_observation(text, images[0]),)
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as error:
        raise VisionProbeError("RESPONSE_SCHEMA") from error
    if not isinstance(data, dict) or set(data) != {"observations"}:
        raise VisionProbeError("RESPONSE_SCHEMA")
    raw = data.get("observations")
    if not isinstance(raw, list) or len(raw) != len(images):
        raise VisionProbeError("RESPONSE_SCHEMA")
    return tuple(
        parse_vision_observation(json.dumps(item, ensure_ascii=True), image)
        for item, image in zip(raw, images, strict=True)
    )


def _validate_expected(
    observations: Sequence[VisionObservation],
    expected: Sequence[Mapping[str, str]] | None,
) -> None:
    if expected is None:
        return
    if len(observations) != len(expected):
        raise VisionProbeError("RESPONSE_CONTENT_MISMATCH")
    fields = ("purchase_order", "supplier", "status", "currency")
    for observation, expected_fields in zip(observations, expected, strict=True):
        actual = {
            "purchase_order": observation.purchase_order,
            "supplier": observation.supplier,
            "status": observation.status,
            "currency": observation.currency,
        }
        if any(actual[field] != expected_fields.get(field) for field in fields):
            raise VisionProbeError("RESPONSE_CONTENT_MISMATCH")


def probe_vision(
    prompt: str,
    images: list[bytes] | tuple[bytes, ...],
    *,
    environ: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    expected_observations: Sequence[Mapping[str, str]] | None = None,
) -> VisionProbeResult:
    """Try configured roles in order, stopping at the first valid image read."""

    values = os.environ if environ is None else environ
    encoded = _image_data(images)
    attempts: list[VisionAttempt] = []
    for role in _ROLE_ENV:
        try:
            config = _role_config(role, values)
        except VisionProbeError as error:
            attempts.append(VisionAttempt(role, None, "FAILED", error.code))
            continue
        if config is None:
            continue
        base_url, api_key, model, responses = config
        try:
            request = _payload(prompt, encoded, model, responses)
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            headers["Authorization"] = f"Bearer {api_key}"
            proxy = values.get(MODEL_PROXY_ENV, "").strip() or None
            with httpx.Client(
                timeout=httpx.Timeout(30.0),
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                proxy=proxy,
                headers=headers,
            ) as client:
                response = client.post(_endpoint(base_url, responses), json=request)
            if len(response.content) > MAX_RESPONSE_BYTES or not response.is_success:
                raise VisionProbeError("UPSTREAM_UNAVAILABLE")
            text, prompt_tokens, completion_tokens = _response_text(response.json())
            observations = _parse_observations(text, images)
            _validate_expected(observations, expected_observations)
            observation = observations[0]
        except VisionProbeError as error:
            attempts.append(VisionAttempt(role, model, "FAILED", error.code))
            continue
        except httpx.HTTPError, ValueError, OSError:
            attempts.append(VisionAttempt(role, model, "FAILED", "TRANSPORT_ERROR"))
            continue
        attempts.append(VisionAttempt(role, model, "PASS"))
        return VisionProbeResult(
            status="PASS",
            observation=observation,
            attempts=tuple(attempts),
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            observations=observations,
        )
    return VisionProbeResult(
        status="VISION_PROVIDER_UNAVAILABLE",
        observation=None,
        attempts=tuple(attempts),
        model=None,
        prompt_tokens=None,
        completion_tokens=None,
    )


__all__ = [
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "VisionAttempt",
    "VisionObservation",
    "VisionProbeError",
    "VisionProbeResult",
    "parse_vision_observation",
    "probe_vision",
]
