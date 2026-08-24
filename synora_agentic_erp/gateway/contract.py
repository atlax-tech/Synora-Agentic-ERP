from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

SCHEMA_VERSION = "1"
MAX_PAGE_SIZE = 50
type JsonScalar = str | int | float | bool | None
type ValueParser = Callable[[object], JsonScalar]


class GatewayFault(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ToolCall:
    name: str
    version: str
    input: dict[str, Any]


@dataclass(frozen=True)
class GatewayRequest:
    run_id: str
    capability: str
    correlation_id: str
    tool: ToolCall


@dataclass(frozen=True)
class InputField:
    parse: ValueParser
    required: bool = False
    default: JsonScalar = None


@dataclass(frozen=True)
class ToolResult:
    items: list[dict[str, JsonScalar]]
    source_modified_at: str | None = None


def _strict_object(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GatewayFault("INVALID_INPUT", f"{label} must be an object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown or missing:
        raise GatewayFault("INVALID_INPUT", f"{label} fields are invalid")
    return value


def _required_text(value: object, label: str, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return value


def bounded_text(value: object, label: str, max_length: int = 140) -> str:
    return _required_text(value, label, max_length)


def optional_text(value: object, label: str, max_length: int = 140) -> str | None:
    if value is None:
        return None
    return _required_text(value, label, max_length)


def canonical_uuid(value: object, label: str) -> str:
    text = _required_text(value, label, 36)
    try:
        parsed = UUID(text)
    except ValueError as error:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid") from error
    normalized = str(parsed)
    if text.lower() != normalized:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return normalized


def correlation_id(value: object) -> str:
    return canonical_uuid(value, "correlation_id")


def positive_int(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return value


def parse_tool_input(
    payload: dict[str, Any], fields: dict[str, InputField], max_page_size: int
) -> dict[str, JsonScalar]:
    all_fields = {
        **fields,
        "offset": InputField(lambda value: positive_int(value, "offset", 10_000), default=0),
        "limit": InputField(lambda value: positive_int(value, "limit", max_page_size), default=20),
    }
    unknown = set(payload) - set(all_fields)
    missing = {name for name, field in all_fields.items() if field.required and name not in payload}
    if unknown or missing:
        raise GatewayFault("INVALID_INPUT", "tool.input fields are invalid")
    parsed: dict[str, JsonScalar] = {}
    for name, field in all_fields.items():
        raw_value = payload[name] if name in payload else field.default
        parsed[name] = field.parse(raw_value) if name in payload or field.required else raw_value
    if parsed["limit"] == 0:
        raise GatewayFault("INVALID_INPUT", "limit is invalid")
    return parsed


def parse_request(payload: object) -> GatewayRequest:
    body = _strict_object(
        payload,
        {"schema_version", "run_id", "capability", "correlation_id", "tool"},
        "request",
    )
    if body["schema_version"] != SCHEMA_VERSION:
        raise GatewayFault("UNSUPPORTED_VERSION", "schema version is not supported")
    tool = _strict_object(body["tool"], {"name", "version", "input"}, "tool")
    if not isinstance(tool["input"], dict):
        raise GatewayFault("INVALID_INPUT", "tool.input must be an object")
    return GatewayRequest(
        run_id=canonical_uuid(body["run_id"], "run_id"),
        capability=_required_text(body["capability"], "capability", 512),
        correlation_id=correlation_id(body["correlation_id"]),
        tool=ToolCall(
            name=_required_text(tool["name"], "tool.name"),
            version=_required_text(tool["version"], "tool.version", 20),
            input=tool["input"],
        ),
    )


def error_response(fault: GatewayFault, correlation_id: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "correlation_id": correlation_id,
        "error": {
            "code": fault.code,
            "message": fault.message,
            "retryable": fault.code in {"TIMEOUT", "ERP_ERROR"},
        },
    }
