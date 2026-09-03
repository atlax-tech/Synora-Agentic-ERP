"""P9.8 ANP fixed-descriptor routing tests."""

from __future__ import annotations

import pytest

from labs.protocols.phase9_anp import (
    AgentDescriptor,
    AmbiguousRouteError,
    NoRouteError,
    RouteCycleError,
    RouteRequest,
    assert_acyclic_routes,
    select_route,
)


def _request() -> RouteRequest:
    return RouteRequest(
        protocol="A2A",
        protocol_version="1.0",
        input_schema="reviewer.v1",
        capability="policy.review",
        required_data_scopes=["procurement.read"],
        allowed_data_scopes=["procurement.read", "policy.read"],
        required_tools=[],
        allowed_tools=[],
    )


def _descriptor(
    agent_id: str,
    *,
    scopes: list[str] | None = None,
    tools: list[str] | None = None,
    endpoint: str = "http://127.0.0.1:8029/a2a",
) -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=agent_id,
        protocol="A2A",
        protocol_version="1.0",
        input_schema="reviewer.v1",
        output_schema="review.v1",
        endpoint_type="loopback_http",
        endpoint=endpoint,
        capabilities=["policy.review"],
        data_scopes=scopes or ["procurement.read"],
        tool_allowlist=tools or [],
    )


def test_select_route_chooses_least_permission_descriptor() -> None:
    decision = select_route(
        [
            _descriptor("reviewer-wide", scopes=["procurement.read", "policy.read"]),
            _descriptor("reviewer-minimal"),
        ],
        _request(),
    )

    assert decision.agent_id == "reviewer-minimal"
    assert decision.endpoint == "http://127.0.0.1:8029/a2a"


def test_route_rejects_external_endpoint_and_permission_expansion() -> None:
    with pytest.raises(ValueError):
        _descriptor("external", endpoint="https://reviewer.example/a2a")
    with pytest.raises(ValueError):
        _descriptor("credentialed", endpoint="http://user:secret@127.0.0.1:8029/a2a")

    request = _request()
    with pytest.raises(NoRouteError):
        select_route(
            [_descriptor("too-wide", scopes=["procurement.read", "finance.write"])], request
        )


def test_route_rejects_unknown_version_and_ambiguous_equal_rank() -> None:
    unknown_version = AgentDescriptor(
        agent_id="unknown-version",
        protocol="A2A",
        protocol_version="9.0",
        input_schema="reviewer.v1",
        output_schema="review.v1",
        endpoint_type="stdio",
        endpoint="stdio://reviewer",
        capabilities=["policy.review"],
    )
    with pytest.raises(NoRouteError):
        select_route([unknown_version], _request())
    with pytest.raises(AmbiguousRouteError):
        select_route([_descriptor("reviewer-a"), _descriptor("reviewer-b")], _request())


def test_static_route_graph_rejects_cycles_and_accepts_dag() -> None:
    assert_acyclic_routes({"planner": ["reviewer"], "reviewer": ["coach"], "coach": []})
    with pytest.raises(RouteCycleError):
        assert_acyclic_routes({"planner": ["reviewer"], "reviewer": ["planner"]})
