"""Read-only comparison adapters for the real development ERP.

The adapters are deliberately separate from the synthetic browser runner.  A
real ERP session is trusted only for initialization and ordinary page loading;
the page observation itself contains visible fields only.  No write endpoint,
clipboard, download, storage-state reuse, or arbitrary URL is exposed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

try:
    from agent_runtime.gateway import (
        CurrentPurchaseOrderCall,
        CurrentPurchaseOrderInput,
        GatewayClient,
        GatewayClientError,
        GatewayRejected,
        GatewayRequest,
    )
except ModuleNotFoundError:  # The workspace sidecar is package=false for CLI use.
    _runtime_source = Path(__file__).resolve().parents[2] / "services" / "agent_runtime" / "src"
    sys.path.insert(0, str(_runtime_source))
    from agent_runtime.gateway import (
        CurrentPurchaseOrderCall,
        CurrentPurchaseOrderInput,
        GatewayClient,
        GatewayClientError,
        GatewayRejected,
        GatewayRequest,
    )
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

ERP_ORIGIN_ENV = "SYNORA_GATEWAY_ORIGIN"
ERP_PASSWORD_ENV = "SYNORA_P2P_USER_PWD"
ERP_USER = "synora-p1-buyer@dev.localhost"
ERP_COMPANY = "SYNORA-P1 Test Company"
ERP_WAREHOUSE = "SYNORA-P1 Stores - SP1"
DEFAULT_ERP_ORIGIN = "http://127.0.0.1:8000"
ERP_SITE_PATH = "/desk/purchase-order"
ERP_LOGIN_PATH = "/api/method/login"
ERP_ISSUE_RUN_PATH = "/api/method/synora_agentic_erp.api.issue_run"
ERP_GATEWAY_PATH = "/api/method/synora_agentic_erp.api.execute"
MAX_RESPONSE_BYTES = 2_000_000
READ_FIELDS = ("purchase_order", "supplier", "status", "currency")
ReadStatus = Literal[
    "SUCCEEDED",
    "NOT_FOUND",
    "PERMISSION_DENIED",
    "AUTH_REQUIRED",
    "FAILED",
    "BLOCKED",
]


class ErpStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )


class ErpReadConfig(ErpStrictModel):
    """Non-secret inputs for one disposable ERP read session."""

    base_url: str = Field(default=DEFAULT_ERP_ORIGIN, min_length=1, max_length=300)
    purchase_order: str = Field(min_length=1, max_length=140)
    user: str = Field(default=ERP_USER, min_length=3, max_length=140)
    company: str = Field(default=ERP_COMPANY, min_length=1, max_length=140)
    warehouse: str | None = Field(default=ERP_WAREHOUSE, max_length=140)
    timeout_seconds: float = Field(default=10.0, gt=0, le=10.0)

    @model_validator(mode="after")
    def validate_origin(self) -> ErpReadConfig:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("base_url must be an HTTP(S) origin")
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("real ERP comparison is limited to loopback")
        return self


class ErpFact(ErpStrictModel):
    purchase_order: str | None = Field(default=None, max_length=140)
    supplier: str | None = Field(default=None, max_length=140)
    status: str | None = Field(default=None, max_length=140)
    currency: str | None = Field(default=None, max_length=20)
    source_modified_at: str | None = Field(default=None, max_length=80)
    frappe_revision: str | None = Field(default=None, max_length=120)
    erpnext_revision: str | None = Field(default=None, max_length=120)


class ErpReadResult(ErpStrictModel):
    method: Literal["api", "web"]
    status: ReadStatus
    fact: ErpFact = ErpFact()
    safety_pass: bool
    failure_code: str | None = Field(default=None, max_length=80)
    elapsed_ms: int = Field(ge=0)
    request_paths: tuple[str, ...] = ()
    policy_events: tuple[str, ...] = ()
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ErpComparison(ErpStrictModel):
    purchase_order: str = Field(min_length=1, max_length=140)
    api: ErpReadResult
    web: ErpReadResult
    status: Literal["MATCHED", "MISMATCH", "STATE_DRIFT", "BLOCKED"]
    compared_fields: tuple[str, ...] = READ_FIELDS
    before_modified_at: str | None = Field(default=None, max_length=80)
    after_modified_at: str | None = Field(default=None, max_length=80)


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _result(
    method: Literal["api", "web"],
    status: ReadStatus,
    *,
    fact: ErpFact | None = None,
    safety_pass: bool,
    failure_code: str | None = None,
    started: float,
    request_paths: tuple[str, ...] = (),
    policy_events: tuple[str, ...] = (),
) -> ErpReadResult:
    actual_fact = fact or ErpFact()
    return ErpReadResult(
        method=method,
        status=status,
        fact=actual_fact,
        safety_pass=safety_pass,
        failure_code=failure_code,
        elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
        request_paths=request_paths,
        policy_events=policy_events,
        evidence_digest=_digest(
            {
                "method": method,
                "status": status,
                "fact": actual_fact.model_dump(mode="json"),
                "failure_code": failure_code,
                "policy_events": policy_events,
            }
        ),
    )


def _failure_status(code: str) -> ReadStatus:
    if code == "NOT_FOUND":
        return "NOT_FOUND"
    if code in {"PERMISSION_DENIED", "SCOPE_DENIED"}:
        return "PERMISSION_DENIED"
    if code in {"AUTHENTICATION_REQUIRED", "AUTHENTICATION_REJECTED"}:
        return "AUTH_REQUIRED"
    return "FAILED"


def _read_password(environ: Mapping[str, str]) -> str | None:
    password = environ.get(ERP_PASSWORD_ENV)
    return password if password else None


def _parse_issue_run(body: object) -> tuple[str, str] | None:
    if not isinstance(body, dict) or set(body) != {"message"}:
        return None
    message = body.get("message")
    if not isinstance(message, dict) or message.get("ok") is not True:
        return None
    run = message.get("run")
    if not isinstance(run, dict):
        return None
    run_id = run.get("run_id")
    capability = run.get("capability")
    if not isinstance(run_id, str) or not isinstance(capability, str):
        return None
    return run_id, capability


def _fact_from_data(data: object, purchase_order: str, snapshot: object) -> ErpFact | None:
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    row = data[0]
    values: dict[str, str | None] = {}
    for key in READ_FIELDS:
        value = row.get(key)
        if value is not None and not isinstance(value, str):
            return None
        values[key] = value
    if values["purchase_order"] != purchase_order:
        return None
    source_modified_at: str | None = None
    frappe_revision: str | None = None
    erpnext_revision: str | None = None
    if isinstance(snapshot, dict):
        for key, target in (
            ("source_modified_at", "source_modified_at"),
            ("frappe_revision", "frappe_revision"),
            ("erpnext_revision", "erpnext_revision"),
        ):
            value = snapshot.get(key)
            if value is not None and isinstance(value, str):
                if target == "source_modified_at":
                    source_modified_at = value
                elif target == "frappe_revision":
                    frappe_revision = value
                else:
                    erpnext_revision = value
    return ErpFact(
        **values,
        source_modified_at=source_modified_at,
        frappe_revision=frappe_revision,
        erpnext_revision=erpnext_revision,
    )


async def read_erp_api(
    config: ErpReadConfig,
    *,
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ErpReadResult:
    """Read one PO via login -> issue_run -> capability-only typed Gateway."""

    started = time.monotonic()
    env = environ if environ is not None else os.environ
    password = _read_password(env)
    if password is None:
        return _result(
            "api",
            "BLOCKED",
            safety_pass=True,
            failure_code="ERP_CREDENTIALS_UNAVAILABLE",
            started=started,
        )
    paths = (ERP_LOGIN_PATH, ERP_ISSUE_RUN_PATH, ERP_GATEWAY_PATH)
    origin = _origin(config.base_url)
    try:
        async with httpx.AsyncClient(
            base_url=f"{origin}/",
            timeout=httpx.Timeout(config.timeout_seconds),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "application/json"},
        ) as session:
            login = await session.post(
                ERP_LOGIN_PATH,
                data={"usr": config.user, "pwd": password},
            )
            if login.status_code in {401, 403}:
                return _result(
                    "api",
                    "AUTH_REQUIRED",
                    safety_pass=True,
                    failure_code="ERP_LOGIN_REJECTED",
                    started=started,
                    request_paths=paths,
                )
            if len(login.content) > MAX_RESPONSE_BYTES:
                return _result(
                    "api",
                    "FAILED",
                    safety_pass=True,
                    failure_code="ERP_RESPONSE_TOO_LARGE",
                    started=started,
                    request_paths=paths,
                )
            if not login.is_success:
                return _result(
                    "api",
                    "FAILED",
                    safety_pass=True,
                    failure_code="ERP_LOGIN_FAILED",
                    started=started,
                    request_paths=paths,
                )
            issue = await session.post(
                ERP_ISSUE_RUN_PATH,
                data={
                    "company": config.company,
                    "goal": f"read purchase order {config.purchase_order}",
                    "warehouse": config.warehouse or "",
                    "execution_mode": "DETERMINISTIC",
                    "purpose": "ANALYSIS",
                },
            )
            if len(issue.content) > MAX_RESPONSE_BYTES:
                return _result(
                    "api",
                    "FAILED",
                    safety_pass=True,
                    failure_code="ERP_RESPONSE_TOO_LARGE",
                    started=started,
                    request_paths=paths,
                )
            issue_body = (
                issue.json()
                if issue.headers.get("content-type", "").startswith("application/json")
                else None
            )
            run = _parse_issue_run(issue_body)
            if run is None:
                if issue.status_code in {401, 403}:
                    status: ReadStatus = "AUTH_REQUIRED"
                    code = "ERP_RUN_AUTH_REJECTED"
                else:
                    status = "FAILED"
                    code = "ERP_RUN_INVALID_RESPONSE"
                return _result(
                    "api",
                    status,
                    safety_pass=True,
                    failure_code=code,
                    started=started,
                    request_paths=paths,
                )

        previous_origin = os.environ.get(ERP_ORIGIN_ENV)
        os.environ[ERP_ORIGIN_ENV] = origin
        try:
            request = GatewayRequest(
                run_id=UUID(run[0]),
                capability=SecretStr(run[1]),
                correlation_id=uuid4(),
                tool=CurrentPurchaseOrderCall(
                    name="purchase_order.current",
                    input=CurrentPurchaseOrderInput(name=config.purchase_order),
                ),
            )
            async with GatewayClient(
                timeout_seconds=config.timeout_seconds, transport=transport
            ) as client:
                response = await client.execute(request)
        finally:
            if previous_origin is None:
                os.environ.pop(ERP_ORIGIN_ENV, None)
            else:
                os.environ[ERP_ORIGIN_ENV] = previous_origin
        fact = _fact_from_data(
            response.data, config.purchase_order, response.snapshot.model_dump(mode="json")
        )
        if fact is None:
            return _result(
                "api",
                "FAILED",
                safety_pass=True,
                failure_code="ERP_RESPONSE_INVALID",
                started=started,
                request_paths=paths,
            )
        return _result(
            "api", "SUCCEEDED", fact=fact, safety_pass=True, started=started, request_paths=paths
        )
    except GatewayRejected as error:
        return _result(
            "api",
            _failure_status(error.code),
            safety_pass=True,
            failure_code=error.code,
            started=started,
            request_paths=paths,
        )
    except GatewayClientError, httpx.TimeoutException:
        return _result(
            "api",
            "FAILED",
            safety_pass=True,
            failure_code="ERP_TRANSPORT_ERROR",
            started=started,
            request_paths=paths,
        )
    except httpx.HTTPError, ValueError, TypeError, KeyError:
        return _result(
            "api",
            "FAILED",
            safety_pass=True,
            failure_code="ERP_PROTOCOL_ERROR",
            started=started,
            request_paths=paths,
        )


__all__ = [
    "DEFAULT_ERP_ORIGIN",
    "ERP_COMPANY",
    "ERP_GATEWAY_PATH",
    "ERP_PASSWORD_ENV",
    "ERP_USER",
    "ErpComparison",
    "ErpFact",
    "ErpReadConfig",
    "ErpReadResult",
    "read_erp_api",
]
