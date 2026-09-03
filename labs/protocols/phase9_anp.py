"""P9.8 fixed-descriptor ANP discovery and routing concept.

This is deliberately a pure selection function.  It does not discover over a
network, resolve a DID, fetch a descriptor, or install a runtime dependency.
Descriptors are treated as untrusted input and must fit the local permission
and protocol policy before they can be selected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_IDENTIFIER = r"^[a-z][a-z0-9_.:-]{0,95}$"
_VERSION = r"^\d+\.\d+$"
_SCHEMA = r"^[a-z][a-z0-9_.-]{2,63}$"
_PERMISSION = r"^[a-z][a-z0-9_.:-]{0,95}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


ProtocolName = Literal["A2A", "MCP", "ANP"]


def _unique_bounded(values: list[str], *, field_name: str) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


def _validate_loopback_or_stdio(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme == "stdio":
        if (
            parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or not parsed.netloc
            or not re.fullmatch(_IDENTIFIER, parsed.hostname or "")
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("stdio descriptor endpoint must be a named local channel")
        return value
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("descriptor endpoint must be loopback HTTP or stdio")
    if parsed.username or parsed.password:
        raise ValueError("descriptor endpoint must not contain credentials")
    if parsed.query or parsed.fragment or not parsed.path.startswith("/"):
        raise ValueError("descriptor endpoint must not contain query or fragment")
    if parsed.port is not None and not 1024 <= parsed.port <= 65535:
        raise ValueError("descriptor endpoint port is outside the lab range")
    return value


class AgentDescriptor(_StrictModel):
    agent_id: str = Field(pattern=_IDENTIFIER, min_length=1, max_length=96)
    protocol: ProtocolName
    protocol_version: str = Field(pattern=_VERSION, min_length=3, max_length=20)
    input_schema: str = Field(pattern=_SCHEMA, min_length=3, max_length=64)
    output_schema: str = Field(pattern=_SCHEMA, min_length=3, max_length=64)
    endpoint_type: Literal["loopback_http", "stdio"]
    endpoint: str = Field(min_length=1, max_length=240)
    capabilities: list[str] = Field(min_length=1, max_length=32)
    data_scopes: list[str] = Field(default_factory=list, max_length=32)
    tool_allowlist: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _validate_loopback_or_stdio(value)

    @model_validator(mode="after")
    def validate_endpoint_type(self) -> AgentDescriptor:
        scheme = urlsplit(self.endpoint).scheme
        expected = "stdio" if scheme == "stdio" else "loopback_http"
        if self.endpoint_type != expected:
            raise ValueError("endpoint_type does not match endpoint")
        return self

    @field_validator("capabilities", "data_scopes", "tool_allowlist")
    @classmethod
    def validate_permissions(cls, value: list[str], info: object) -> list[str]:
        field_name = getattr(info, "field_name", "permission")
        if any(not re.fullmatch(_PERMISSION, item) for item in value):
            raise ValueError(f"{field_name} contains an invalid identifier")
        return _unique_bounded(value, field_name=field_name)


FIXED_DESCRIPTOR_SET: Final[tuple[AgentDescriptor, ...]] = (
    AgentDescriptor(
        agent_id="procurement-planner",
        protocol="A2A",
        protocol_version="1.0",
        input_schema="planner.v1",
        output_schema="planner.v1",
        endpoint_type="loopback_http",
        endpoint="http://127.0.0.1:8030/planner",
        capabilities=["procurement.plan"],
        data_scopes=["procurement.read"],
        tool_allowlist=[],
    ),
    AgentDescriptor(
        agent_id="policy-risk-reviewer",
        protocol="A2A",
        protocol_version="1.0",
        input_schema="reviewer.v1",
        output_schema="review.v1",
        endpoint_type="loopback_http",
        endpoint="http://127.0.0.1:8029/a2a",
        capabilities=["policy.review"],
        data_scopes=["procurement.read"],
        tool_allowlist=[],
    ),
    AgentDescriptor(
        agent_id="erp-coach",
        protocol="A2A",
        protocol_version="1.0",
        input_schema="coach.v1",
        output_schema="coach.v1",
        endpoint_type="loopback_http",
        endpoint="http://127.0.0.1:8031/coach",
        capabilities=["erp.coach"],
        data_scopes=["procurement.read"],
        tool_allowlist=[],
    ),
    AgentDescriptor(
        agent_id="reconciliation-agent",
        protocol="A2A",
        protocol_version="1.0",
        input_schema="reconciliation.v1",
        output_schema="reconciliation.v1",
        endpoint_type="loopback_http",
        endpoint="http://127.0.0.1:8032/reconciliation",
        capabilities=["procurement.reconcile"],
        data_scopes=["procurement.read"],
        tool_allowlist=[],
    ),
)


def fixed_descriptors() -> tuple[AgentDescriptor, ...]:
    """Return the immutable Phase 9 descriptor set used by the lab."""

    return tuple(descriptor.model_copy(deep=True) for descriptor in FIXED_DESCRIPTOR_SET)


class RouteRequest(_StrictModel):
    protocol: ProtocolName
    protocol_version: str = Field(pattern=_VERSION, min_length=3, max_length=20)
    input_schema: str = Field(pattern=_SCHEMA, min_length=3, max_length=64)
    capability: str = Field(pattern=_IDENTIFIER, min_length=1, max_length=96)
    required_data_scopes: list[str] = Field(default_factory=list, max_length=32)
    allowed_data_scopes: list[str] = Field(default_factory=list, max_length=32)
    required_tools: list[str] = Field(default_factory=list, max_length=32)
    allowed_tools: list[str] = Field(default_factory=list, max_length=32)

    @field_validator(
        "required_data_scopes",
        "allowed_data_scopes",
        "required_tools",
        "allowed_tools",
    )
    @classmethod
    def validate_request_permissions(cls, value: list[str], info: object) -> list[str]:
        field_name = getattr(info, "field_name", "permission")
        if any(not re.fullmatch(_PERMISSION, item) for item in value):
            raise ValueError(f"{field_name} contains an invalid identifier")
        return _unique_bounded(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_requested_bounds(self) -> RouteRequest:
        if not set(self.required_data_scopes).issubset(self.allowed_data_scopes):
            raise ValueError("required data scopes must be within the allowed scopes")
        if not set(self.required_tools).issubset(self.allowed_tools):
            raise ValueError("required tools must be within the allowed tools")
        return self


class RoutingDecision(_StrictModel):
    agent_id: str = Field(pattern=_IDENTIFIER, min_length=1, max_length=96)
    protocol: ProtocolName
    protocol_version: str = Field(pattern=_VERSION, min_length=3, max_length=20)
    endpoint: str = Field(min_length=1, max_length=240)
    data_scopes: list[str]
    tool_allowlist: list[str]


class NoRouteError(ValueError):
    """No descriptor satisfies the fixed routing policy."""


class AmbiguousRouteError(NoRouteError):
    """More than one least-permission descriptor has the same rank."""


class RouteCycleError(NoRouteError):
    """A static route graph contains a cycle."""


def _is_compatible(descriptor: AgentDescriptor, request: RouteRequest) -> bool:
    return (
        descriptor.protocol == request.protocol
        and descriptor.protocol_version == request.protocol_version
        and descriptor.input_schema == request.input_schema
        and request.capability in descriptor.capabilities
        and set(request.required_data_scopes).issubset(descriptor.data_scopes)
        and set(descriptor.data_scopes).issubset(request.allowed_data_scopes)
        and set(request.required_tools).issubset(descriptor.tool_allowlist)
        and set(descriptor.tool_allowlist).issubset(request.allowed_tools)
    )


def select_route(descriptors: Iterable[AgentDescriptor], request: RouteRequest) -> RoutingDecision:
    """Select one compatible least-permission descriptor without I/O."""

    candidates = [descriptor for descriptor in descriptors if _is_compatible(descriptor, request)]
    if not candidates:
        raise NoRouteError("no descriptor satisfies protocol, scope, and tool policy")

    candidates.sort(
        key=lambda descriptor: (
            len(descriptor.data_scopes),
            len(descriptor.tool_allowlist),
            descriptor.agent_id,
        )
    )
    best = candidates[0]
    best_rank = (len(best.data_scopes), len(best.tool_allowlist))
    if len(candidates) > 1 and best_rank == (
        len(candidates[1].data_scopes),
        len(candidates[1].tool_allowlist),
    ):
        raise AmbiguousRouteError("multiple descriptors have the same least-permission rank")

    return RoutingDecision(
        agent_id=best.agent_id,
        protocol=best.protocol,
        protocol_version=best.protocol_version,
        endpoint=best.endpoint,
        data_scopes=best.data_scopes,
        tool_allowlist=best.tool_allowlist,
    )


def select_fixed_route(request: RouteRequest) -> RoutingDecision:
    """Select only from the bounded Phase 9 descriptor set."""

    return select_route(FIXED_DESCRIPTOR_SET, request)


def assert_acyclic_routes(routes: Mapping[str, Iterable[str]]) -> None:
    """Validate a bounded static routing graph; never follows a network edge."""

    normalized = {source: tuple(targets) for source, targets in routes.items()}
    if len(normalized) > 64 or sum(len(targets) for targets in normalized.values()) > 128:
        raise RouteCycleError("route graph exceeds the LAB_ONLY bound")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise RouteCycleError("route graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for target in normalized.get(node, ()):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in normalized:
        visit(node)
