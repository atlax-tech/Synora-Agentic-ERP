import json
import math
import os
from typing import Annotated, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, field_validator

GATEWAY_PATH = "/api/method/synora_agentic_erp.api.execute"
GATEWAY_ORIGIN_ENV = "SYNORA_GATEWAY_ORIGIN"
SCHEMA_VERSION: Literal["1"] = "1"
MAX_RESPONSE_BYTES = 2_000_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class PageInput(StrictModel):
    offset: int = Field(default=0, ge=0, le=10_000)
    limit: int = Field(default=20, ge=1, le=50)


class ItemLookupInput(PageInput):
    query: str | None = Field(default=None, min_length=1, max_length=140)


class SupplierLookupInput(PageInput):
    query: str | None = Field(default=None, min_length=1, max_length=140)


class ProjectedStockInput(PageInput):
    item_code: str = Field(min_length=1, max_length=140)
    warehouse: str | None = Field(default=None, min_length=1, max_length=140)


class OpenDemandInput(PageInput):
    item_code: str | None = Field(default=None, min_length=1, max_length=140)
    warehouse: str | None = Field(default=None, min_length=1, max_length=140)


class OpenMaterialRequestInput(PageInput):
    pass


class OpenPurchaseOrderInput(PageInput):
    supplier: str | None = Field(default=None, min_length=1, max_length=140)


class ItemLookupCall(StrictModel):
    name: Literal["item.lookup"]
    version: Literal["1"] = "1"
    input: ItemLookupInput


class SupplierLookupCall(StrictModel):
    name: Literal["supplier.lookup"]
    version: Literal["1"] = "1"
    input: SupplierLookupInput


class ProjectedStockCall(StrictModel):
    name: Literal["stock.projected"]
    version: Literal["1"] = "1"
    input: ProjectedStockInput


class OpenDemandCall(StrictModel):
    name: Literal["demand.open"]
    version: Literal["1"] = "1"
    input: OpenDemandInput


class OpenMaterialRequestCall(StrictModel):
    name: Literal["material_request.open"]
    version: Literal["1"] = "1"
    input: OpenMaterialRequestInput


class OpenPurchaseOrderCall(StrictModel):
    name: Literal["purchase_order.open"]
    version: Literal["1"] = "1"
    input: OpenPurchaseOrderInput


ToolCall = Annotated[
    ItemLookupCall
    | SupplierLookupCall
    | ProjectedStockCall
    | OpenDemandCall
    | OpenMaterialRequestCall
    | OpenPurchaseOrderCall,
    Field(discriminator="name"),
]


class GatewayRequest(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    run_id: UUID
    capability: SecretStr = Field(repr=False)
    correlation_id: UUID
    tool: ToolCall = Field(repr=False)

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if len(token) != 43 or any(
            not (character.isascii() and (character.isalnum() or character in "_-"))
            for character in token
        ):
            raise ValueError("capability is invalid")
        return value


class ToolMetadata(StrictModel):
    name: str
    version: str
    risk: Literal["READ"]
    caller_authorization: Literal["FRAPPE_PERMISSION_AND_RUN_SCOPE"]
    timeout_ms: int
    max_page_size: int


class AuthorizedScope(StrictModel):
    company: str
    warehouse: str | None


class Snapshot(StrictModel):
    captured_at: str
    source_modified_at: str | None
    frappe_revision: str
    erpnext_revision: str


class Completeness(StrictModel):
    status: Literal["COMPLETE", "PARTIAL"]
    omissions: dict[str, int]


class Page(StrictModel):
    offset: int
    limit: int
    returned: int
    has_more: bool


JsonScalar = str | int | float | bool | None
WireUUID = Annotated[UUID, Field(strict=False)]


class GatewaySuccess(StrictModel):
    ok: Literal[True]
    schema_version: Literal["1"]
    run_id: WireUUID
    state_version: int
    correlation_id: WireUUID
    tool: ToolMetadata
    authorized_scope: AuthorizedScope
    snapshot: Snapshot
    completeness: Completeness
    page: Page
    data: list[dict[str, JsonScalar]]


type GatewayErrorCode = Literal[
    "AUTHENTICATION_REJECTED",
    "AUTHENTICATION_REQUIRED",
    "CONFLICT",
    "ERP_ERROR",
    "INVALID_INPUT",
    "NOT_FOUND",
    "PERMISSION_DENIED",
    "RESULT_LIMIT",
    "RUN_REJECTED",
    "SCOPE_DENIED",
    "TIMEOUT",
    "TOOL_NOT_ALLOWED",
    "UNSUPPORTED_VERSION",
]


class GatewayErrorDetails(StrictModel):
    code: GatewayErrorCode
    message: str
    retryable: bool


class GatewayFailure(StrictModel):
    ok: Literal[False]
    schema_version: Literal["1"]
    correlation_id: WireUUID | None
    error: GatewayErrorDetails


GatewayEnvelope = Annotated[GatewaySuccess | GatewayFailure, Field(discriminator="ok")]


class GatewayClientError(Exception):
    pass


class GatewayTransportError(GatewayClientError):
    pass


class GatewayTimeoutError(GatewayTransportError):
    pass


class GatewayProtocolError(GatewayClientError):
    pass


class GatewayRejected(GatewayClientError):
    def __init__(self, code: GatewayErrorCode, retryable: bool) -> None:
        super().__init__(f"gateway rejected request ({code})")
        self.code = code
        self.retryable = retryable


_ENVELOPE_ADAPTER: TypeAdapter[GatewayEnvelope] = TypeAdapter(GatewayEnvelope)


def _contains_secret(value: object, secret: str) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str) and secret in current:
            return True
        if isinstance(current, dict):
            pending.extend(current)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class GatewayClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = os.environ.get(GATEWAY_ORIGIN_ENV, "")
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError(f"{GATEWAY_ORIGIN_ENV} must be an HTTP(S) origin")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("gateway timeout must be positive")
        self._client = httpx.AsyncClient(
            base_url=str(url.copy_with(path="/")),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=False,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    async def __aenter__(self) -> GatewayClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def execute(self, request: GatewayRequest) -> GatewaySuccess:
        if _contains_secret(
            request.tool.input.model_dump(mode="json"),
            request.capability.get_secret_value(),
        ):
            raise GatewayProtocolError("tool input contains a request secret")
        payload = request.model_dump(mode="json", exclude={"capability"})
        payload["capability"] = request.capability.get_secret_value()
        transport_fault: GatewayTransportError | None = None
        body = bytearray()
        response_success = False
        stream_completed = False
        http_request: httpx.Request | None = self._client.build_request(
            "POST", GATEWAY_PATH, json=payload
        )
        assert http_request is not None
        http_request.headers.pop("cookie", None)
        response: httpx.Response | None = None
        try:
            response = await self._client.send(http_request, stream=True)
            http_request = None
            payload.pop("capability", None)
            self._client.cookies.clear()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    body.clear()
                    chunk = b""
                    raise GatewayProtocolError("gateway response exceeded size limit")
                body.extend(chunk)
                chunk = b""
            response_success = response.is_success
            stream_completed = True
        except httpx.TimeoutException:
            body.clear()
            transport_fault = GatewayTimeoutError("gateway request timed out")
        except httpx.HTTPError:
            body.clear()
            transport_fault = GatewayTransportError("gateway request failed")
        except GatewayClientError:
            raise
        except Exception:
            body.clear()
            transport_fault = GatewayTransportError("gateway request failed")
        finally:
            payload.pop("capability", None)
            http_request = None
            try:
                if response is not None:
                    await response.aclose()
            except Exception:
                stream_completed = False
                transport_fault = GatewayTransportError("gateway request failed")
            except BaseException:
                stream_completed = False
                raise
            finally:
                response = None
                self._client.cookies.clear()
                if not stream_completed:
                    body.clear()

        if transport_fault:
            raise transport_fault

        if request.capability.get_secret_value().encode() in body:
            body.clear()
            raise GatewayProtocolError("gateway response reflected a request secret")

        response_payload: object = None
        envelope: GatewayEnvelope | None = None
        parse_failed = False
        try:
            response_payload = json.loads(
                body,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
            if _contains_secret(response_payload, request.capability.get_secret_value()):
                body.clear()
                if isinstance(response_payload, (dict, list)):
                    response_payload.clear()
                response_payload = None
                raise GatewayProtocolError("gateway response reflected a request secret")
            if not isinstance(response_payload, dict) or set(response_payload) != {"message"}:
                raise ValueError
            envelope = _ENVELOPE_ADAPTER.validate_python(response_payload["message"])
        except ValueError, TypeError, RecursionError:
            body.clear()
            if isinstance(response_payload, (dict, list)):
                response_payload.clear()
            response_payload = None
            parse_failed = True
        if parse_failed:
            raise GatewayProtocolError("gateway returned an invalid response")
        body.clear()
        assert isinstance(response_payload, dict)
        response_payload.clear()
        assert envelope is not None

        if isinstance(envelope, GatewayFailure):
            if envelope.correlation_id not in {None, request.correlation_id}:
                raise GatewayProtocolError("gateway response does not match request")
            raise GatewayRejected(envelope.error.code, envelope.error.retryable)
        if not response_success:
            raise GatewayProtocolError("gateway returned an invalid HTTP status")
        if (
            envelope.run_id != request.run_id
            or envelope.correlation_id != request.correlation_id
            or envelope.tool.name != request.tool.name
            or envelope.tool.version != request.tool.version
        ):
            raise GatewayProtocolError("gateway response does not match request")
        return envelope
