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
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

import httpx

try:
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
except ModuleNotFoundError:  # The workspace sidecar is not installed for CLI use.
    _runtime_source = Path(__file__).resolve().parents[2] / "services" / "agent_runtime" / "src"
    sys.path.insert(0, str(_runtime_source))
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
# Hybrid observations carry bounded DOM and ARIA text alongside the screenshot.
# Keep a finite request ceiling while leaving room for the action contract.
MAX_PROMPT_CHARS = 10_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_OUTPUT_TOKENS = 1_024
VISION_TIMEOUT_SECONDS = 60.0
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

    def __init__(self, code: str, *, diagnostic: VisionAttempt | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class VisionAttempt:
    role: str
    model: str | None
    status: str
    failure_code: str | None = None
    protocol: str | None = None
    failure_stage: str | None = None
    http_status: int | None = None
    response_content_type: str | None = None
    response_shape: str | None = None
    response_bytes: int | None = None
    elapsed_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    response_fields: tuple[str, ...] = ()
    observation_count: int | None = None
    declared_complete: bool | None = None
    mismatch_fields: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class VisionModelResponse:
    payload: dict[str, object]
    role: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    diagnostic: VisionAttempt | None = None


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


def _payload(
    prompt: str,
    images: list[str],
    model: str,
    responses: bool,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
) -> dict[str, object]:
    if len(prompt) > MAX_PROMPT_CHARS:
        raise VisionProbeError("PROMPT_TOO_LARGE")
    if max_output_tokens < 1 or max_output_tokens > MAX_OUTPUT_TOKENS:
        raise VisionProbeError("MODEL_OUTPUT_BUDGET")
    if responses:
        content: list[dict[str, object]] = [{"type": "input_text", "text": prompt}]
        content.extend({"type": "input_image", "image_url": image} for image in images)
        return {
            "model": model,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": max_output_tokens,
            "store": False,
            "text": {"format": {"type": "json_object"}},
        }
    content = [{"type": "text", "text": prompt}]
    content.extend(
        {"type": "image_url", "image_url": {"url": image, "detail": "high"}} for image in images
    )
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_output_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def _protocol_name(responses: bool) -> str:
    return "responses" if responses else "chat_completions"


def _response_content_type(response: httpx.Response) -> str | None:
    value = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return value[:80] or None


def _response_shape(data: object) -> str:
    if not isinstance(data, dict):
        return "non_object"
    for key in ("output_text", "output", "choices"):
        if key in data:
            return key
    return "unknown"


def _attempt(
    *,
    role: str,
    model: str | None,
    protocol: str,
    started: float,
    failure_code: str | None = None,
    failure_stage: str | None = None,
    http_status: int | None = None,
    response_content_type: str | None = None,
    response_shape: str | None = None,
    response_bytes: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    response_fields: tuple[str, ...] = (),
    observation_count: int | None = None,
    declared_complete: bool | None = None,
    mismatch_fields: tuple[str, ...] = (),
    status: str = "FAILED",
) -> VisionAttempt:
    return VisionAttempt(
        role=role,
        model=model,
        status=status,
        failure_code=failure_code,
        protocol=protocol,
        failure_stage=failure_stage,
        http_status=http_status,
        response_content_type=response_content_type,
        response_shape=response_shape,
        response_bytes=response_bytes,
        elapsed_ms=max(0, int((monotonic() - started) * 1000)),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        response_fields=response_fields,
        observation_count=observation_count,
        declared_complete=declared_complete,
        mismatch_fields=mismatch_fields,
    )


def _status_failure_code(status_code: int) -> str:
    if status_code == 408 or status_code == 504:
        return "TIMEOUT"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code in {401, 403}:
        return "AUTH_ERROR"
    if 500 <= status_code <= 599:
        return "UPSTREAM_UNAVAILABLE"
    return "HTTP_ERROR"


def _parse_failure_stage(code: str) -> str:
    if code == "RESPONSE_INCOMPLETE":
        return "observation_validation"
    if code == "RESPONSE_CONTENT_MISMATCH":
        return "trusted_content_validation"
    if code == "RESPONSE_CONTENT_MISSING":
        return "response_content"
    return "response_schema"


def _transport_failure(
    error: BaseException,
    *,
    role: str,
    model: str,
    protocol: str,
    started: float,
) -> VisionProbeError:
    if isinstance(error, httpx.ConnectTimeout):
        code, stage = "CONNECT_TIMEOUT", "connect"
    elif isinstance(error, httpx.ReadTimeout):
        code, stage = "READ_TIMEOUT", "read"
    elif isinstance(error, httpx.WriteTimeout):
        code, stage = "WRITE_TIMEOUT", "write"
    elif isinstance(error, httpx.PoolTimeout):
        code, stage = "POOL_TIMEOUT", "pool"
    elif isinstance(error, httpx.TimeoutException):
        code, stage = "TIMEOUT", "request"
    elif isinstance(error, httpx.ConnectError):
        code, stage = "TRANSPORT_ERROR", "connect"
    elif isinstance(error, (httpx.ReadError, httpx.WriteError)):
        code, stage = "TRANSPORT_ERROR", "read_write"
    elif isinstance(error, httpx.RemoteProtocolError):
        code, stage = "PROTOCOL_ERROR", "response_headers"
    else:
        code, stage = "TRANSPORT_ERROR", "request"
    return VisionProbeError(
        code,
        diagnostic=_attempt(
            role=role,
            model=model,
            protocol=protocol,
            started=started,
            failure_code=code,
            failure_stage=stage,
        ),
    )


def _request_text(
    prompt: str,
    images: list[str],
    *,
    role: str,
    model: str,
    api_key: str,
    base_url: str,
    responses: bool,
    proxy: str | None,
    transport: httpx.BaseTransport | None,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    timeout_seconds: float = VISION_TIMEOUT_SECONDS,
) -> tuple[str, VisionAttempt]:
    """Send one bounded request and retain only safe response diagnostics."""

    started = monotonic()
    protocol = _protocol_name(responses)
    if timeout_seconds <= 0 or timeout_seconds > VISION_TIMEOUT_SECONDS:
        raise VisionProbeError(
            "MODEL_TIMEOUT_CONFIGURATION",
            diagnostic=_attempt(
                role=role,
                model=model,
                protocol=protocol,
                started=started,
                failure_code="MODEL_TIMEOUT_CONFIGURATION",
                failure_stage="request_validation",
            ),
        )
    try:
        request = _payload(prompt, images, model, responses, max_output_tokens)
    except VisionProbeError as error:
        raise VisionProbeError(
            error.code,
            diagnostic=_attempt(
                role=role,
                model=model,
                protocol=protocol,
                started=started,
                failure_code=error.code,
                failure_stage="request_validation",
            ),
        ) from error
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            proxy=proxy,
            headers=headers,
        ) as client:
            response = client.post(_endpoint(base_url, responses), json=request)
    except (httpx.HTTPError, OSError) as error:
        raise _transport_failure(
            error,
            role=role,
            model=model,
            protocol=protocol,
            started=started,
        ) from error

    response_bytes = len(response.content)
    content_type = _response_content_type(response)
    if response_bytes > MAX_RESPONSE_BYTES:
        raise VisionProbeError(
            "RESPONSE_TOO_LARGE",
            diagnostic=_attempt(
                role=role,
                model=model,
                protocol=protocol,
                started=started,
                failure_code="RESPONSE_TOO_LARGE",
                failure_stage="response_body",
                http_status=response.status_code,
                response_content_type=content_type,
                response_bytes=response_bytes,
            ),
        )
    if not response.is_success:
        code = _status_failure_code(response.status_code)
        raise VisionProbeError(
            code,
            diagnostic=_attempt(
                role=role,
                model=model,
                protocol=protocol,
                started=started,
                failure_code=code,
                failure_stage="http_status",
                http_status=response.status_code,
                response_content_type=content_type,
                response_bytes=response_bytes,
            ),
        )
    try:
        data = response.json()
    except (TypeError, ValueError) as error:
        raise VisionProbeError(
            "RESPONSE_JSON_INVALID",
            diagnostic=_attempt(
                role=role,
                model=model,
                protocol=protocol,
                started=started,
                failure_code="RESPONSE_JSON_INVALID",
                failure_stage="json_decode",
                http_status=response.status_code,
                response_content_type=content_type,
                response_shape="invalid_json",
                response_bytes=response_bytes,
            ),
        ) from error
    shape = _response_shape(data)
    try:
        text, prompt_tokens, completion_tokens = _response_text(data)
    except VisionProbeError as error:
        raise VisionProbeError(
            error.code,
            diagnostic=_attempt(
                role=role,
                model=model,
                protocol=protocol,
                started=started,
                failure_code=error.code,
                failure_stage="response_content",
                http_status=response.status_code,
                response_content_type=content_type,
                response_shape=shape,
                response_bytes=response_bytes,
            ),
        ) from error
    return text, _attempt(
        role=role,
        model=model,
        protocol=protocol,
        started=started,
        status="PASS",
        http_status=response.status_code,
        response_content_type=content_type,
        response_shape=shape,
        response_bytes=response_bytes,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


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
    output_items = data.get("output")
    if isinstance(output_items, list):
        text_parts: list[str] = []
        for item in output_items:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                value = part.get("text")
                if isinstance(value, str) and value.strip():
                    text_parts.append(value)
        if text_parts:
            return "".join(text_parts), prompt_tokens, completion_tokens
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content, prompt_tokens, completion_tokens
            if isinstance(content, list):
                text_parts = [
                    part["text"]
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") in {"text", "output_text"}
                    and isinstance(part.get("text"), str)
                    and part["text"].strip()
                ]
                if text_parts:
                    return "".join(text_parts), prompt_tokens, completion_tokens
    raise VisionProbeError("RESPONSE_CONTENT_MISSING")


def _json_loads(text: str) -> object:
    candidate = text.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        first_line, _, body = candidate.partition("\n")
        if first_line.removeprefix("```").strip().casefold() in {"", "json"}:
            candidate = body[:-3].rstrip()
    try:
        return json.loads(candidate)
    except (TypeError, ValueError) as error:
        raise VisionProbeError("RESPONSE_SCHEMA") from error


def parse_vision_observation(text: str, image: bytes) -> VisionObservation:
    data = _json_loads(text)
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
    data = _json_loads(text)
    if not isinstance(data, dict) or set(data) != {"observations"}:
        raise VisionProbeError("RESPONSE_SCHEMA")
    raw = data.get("observations")
    if not isinstance(raw, list) or len(raw) != len(images):
        raise VisionProbeError("RESPONSE_SCHEMA")
    return tuple(
        parse_vision_observation(json.dumps(item, ensure_ascii=True), image)
        for item, image in zip(raw, images, strict=True)
    )


_OBSERVATION_FIELDS = frozenset({"purchase_order", "supplier", "status", "currency", "complete"})


def _response_observation_metadata(
    text: str,
    image_count: int,
    expected: Sequence[Mapping[str, str]] | None,
) -> tuple[tuple[str, ...], int | None, bool | None, tuple[str, ...]]:
    """Return field names and validation flags without retaining response values."""

    try:
        data = _json_loads(text)
    except VisionProbeError:
        return (), None, None, ()
    if image_count == 1:
        raw_items: object = [data] if isinstance(data, dict) else None
    else:
        raw_items = data.get("observations") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return (), None, None, ()
    fields = frozenset(
        key
        for item in raw_items
        if isinstance(item, dict)
        for key in item
        if key in _OBSERVATION_FIELDS
    )
    complete_values = [item.get("complete") for item in raw_items if isinstance(item, dict)]
    declared_complete = (
        all(value is True for value in complete_values)
        if complete_values and len(complete_values) == len(raw_items)
        else None
    )
    if any(value is False for value in complete_values):
        declared_complete = False
    mismatch: set[str] = set()
    if expected is not None and len(raw_items) == len(expected):
        for item, expected_fields in zip(raw_items, expected, strict=True):
            if not isinstance(item, dict):
                mismatch.update(expected_fields)
                continue
            for field in ("purchase_order", "supplier", "status", "currency"):
                if item.get(field) != expected_fields.get(field):
                    mismatch.add(field)
    return tuple(sorted(fields)), len(raw_items), declared_complete, tuple(sorted(mismatch))


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

    encoded = _image_data(images)
    if expected_observations is None:
        return VisionProbeResult(
            status="VISION_PROVIDER_UNAVAILABLE",
            observation=None,
            attempts=(),
            model=None,
            prompt_tokens=None,
            completion_tokens=None,
        )
    values = os.environ if environ is None else environ
    attempts: list[VisionAttempt] = []
    for role in _ROLE_ENV:
        try:
            config = _role_config(role, values)
        except VisionProbeError as error:
            attempts.append(
                _attempt(
                    role=role,
                    model=None,
                    protocol=_protocol_name(_ROLE_ENV[role][3]),
                    started=monotonic(),
                    failure_code=error.code,
                    failure_stage="configuration",
                )
            )
            continue
        if config is None:
            continue
        base_url, api_key, model, responses = config
        attempt: VisionAttempt | None = None
        response_metadata: tuple[tuple[str, ...], int | None, bool | None, tuple[str, ...]] = (
            (),
            None,
            None,
            (),
        )
        try:
            proxy = values.get(MODEL_PROXY_ENV, "").strip() or None
            text, attempt = _request_text(
                prompt,
                encoded,
                role=role,
                model=model,
                api_key=api_key,
                base_url=base_url,
                responses=responses,
                proxy=proxy,
                transport=transport,
            )
            response_metadata = _response_observation_metadata(
                text, len(images), expected_observations
            )
            observations = _parse_observations(text, images)
            _validate_expected(observations, expected_observations)
            observation = observations[0]
        except VisionProbeError as error:
            diagnostic = error.diagnostic
            if diagnostic is None:
                if attempt is None:  # pragma: no cover - defensive request contract
                    attempt = _attempt(
                        role=role,
                        model=model,
                        protocol=_protocol_name(responses),
                        started=monotonic(),
                    )
                diagnostic = replace(
                    attempt,
                    status="FAILED",
                    failure_code=error.code,
                    failure_stage=_parse_failure_stage(error.code),
                    response_fields=response_metadata[0],
                    observation_count=response_metadata[1],
                    declared_complete=response_metadata[2],
                    mismatch_fields=response_metadata[3],
                )
            else:
                diagnostic = replace(
                    diagnostic,
                    status="FAILED",
                    failure_code=error.code,
                    failure_stage=diagnostic.failure_stage or _parse_failure_stage(error.code),
                    response_fields=response_metadata[0] or diagnostic.response_fields,
                    observation_count=response_metadata[1] or diagnostic.observation_count,
                    declared_complete=(
                        response_metadata[2]
                        if response_metadata[2] is not None
                        else diagnostic.declared_complete
                    ),
                    mismatch_fields=response_metadata[3] or diagnostic.mismatch_fields,
                )
            attempts.append(diagnostic)
            continue
        assert attempt is not None
        attempt = replace(
            attempt,
            response_fields=response_metadata[0],
            observation_count=response_metadata[1],
            declared_complete=response_metadata[2],
        )
        attempts.append(attempt)
        return VisionProbeResult(
            status="PASS",
            observation=observation,
            attempts=tuple(attempts),
            model=model,
            prompt_tokens=attempt.prompt_tokens,
            completion_tokens=attempt.completion_tokens,
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


def request_vision_json(
    prompt: str,
    images: list[bytes] | tuple[bytes, ...],
    *,
    role: str,
    environ: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    timeout_seconds: float = VISION_TIMEOUT_SECONDS,
) -> VisionModelResponse:
    """Send one untrusted image decision request to one frozen role.

    Unlike :func:`probe_vision`, this returns an untrusted JSON decision without
    an oracle comparison. The browser executor must validate the decision before
    any action, and the final task result still needs independent verification.
    """

    encoded = _image_data(images)
    if role not in _ROLE_ENV:
        raise VisionProbeError("INVALID_CONFIGURATION")
    values = os.environ if environ is None else environ
    try:
        config = _role_config(role, values)
    except VisionProbeError:
        raise
    if config is None:
        raise VisionProbeError("VISION_PROVIDER_UNAVAILABLE")
    base_url, api_key, model, responses = config
    try:
        proxy = values.get(MODEL_PROXY_ENV, "").strip() or None
        text, attempt = _request_text(
            prompt,
            encoded,
            role=role,
            model=model,
            api_key=api_key,
            base_url=base_url,
            responses=responses,
            proxy=proxy,
            transport=transport,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        try:
            payload = _json_loads(text)
        except VisionProbeError as error:
            raise VisionProbeError(
                error.code,
                diagnostic=replace(
                    attempt,
                    status="FAILED",
                    failure_code=error.code,
                    failure_stage="json_payload",
                ),
            ) from error
        if not isinstance(payload, dict):
            raise VisionProbeError(
                "RESPONSE_SCHEMA",
                diagnostic=replace(
                    attempt,
                    status="FAILED",
                    failure_code="RESPONSE_SCHEMA",
                    failure_stage="json_payload",
                ),
            )
        return VisionModelResponse(
            payload,
            role,
            model,
            attempt.prompt_tokens,
            attempt.completion_tokens,
            attempt,
        )
    except VisionProbeError:
        raise


__all__ = [
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "VisionAttempt",
    "VisionModelResponse",
    "VisionObservation",
    "VisionProbeError",
    "VisionProbeResult",
    "parse_vision_observation",
    "probe_vision",
    "request_vision_json",
]
