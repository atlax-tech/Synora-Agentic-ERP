"""P9.4 same-task multi-agent pattern comparison tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import labs.agent_patterns.phase9_patterns as patterns


def test_fixed_pattern_set_and_case_order_share_the_p91_dataset() -> None:
    cases = patterns.load_phase9_pattern_cases()

    assert patterns.PATTERN_NAMES == (
        "supervisor",
        "peer_to_peer",
        "hierarchical",
        "managed_agent_tool",
        "explicit_graph_node",
    )
    assert tuple(case.case_id for case in cases) == patterns.EXPECTED_CASE_ORDER
    assert tuple(case.trajectory for case in cases) == (
        "NORMAL",
        "NORMAL",
        "NORMAL",
        "INVALID_OUTPUT",
        "CONFLICT",
        "CONFLICT",
        "NORMAL",
        "NORMAL",
        "NORMAL",
        "CONFLICT",
        "TIMEOUT",
        "CONFLICT",
    )


@pytest.mark.parametrize(
    "pattern_name",
    ["supervisor", "peer_to_peer", "hierarchical", "managed_agent_tool"],
)
def test_each_handwritten_pattern_handles_six_bounded_trajectories(pattern_name: str) -> None:
    cases = patterns.load_phase9_pattern_cases()
    trajectories = patterns._trajectory_cases(cases)
    outcomes = [patterns._run_pattern(case, pattern_name) for case in trajectories]  # type: ignore[arg-type]

    assert [item.stop_reason for item in outcomes] == [
        "ACCEPTED",
        "REVIEW_REJECTED",
        "TIMEOUT",
        "CANCELLED",
        "INVALID_OUTPUT",
        "LOOP_BLOCKED",
    ]
    assert [item.model_calls for item in outcomes] == [2, 2, 1, 1, 1, 3]
    assert outcomes[-1].revision_count == 1
    assert all(item.model_calls <= 3 for item in outcomes)
    assert all(item.unauthorized_tool_calls == 0 for item in outcomes)
    assert all(item.erp_business_writes == 0 for item in outcomes)
    assert all(item.scope_leaks == 0 for item in outcomes)
    assert all(item.secret_leaks == 0 for item in outcomes)


def test_route_traces_are_distinct_but_use_the_same_role_calls() -> None:
    case = patterns.load_phase9_pattern_cases()[0]
    outcomes = {
        name: patterns._run_pattern(case, name)  # type: ignore[arg-type]
        for name in patterns.PATTERN_NAMES[:-1]
    }

    assert all(item.stop_reason == "ACCEPTED" for item in outcomes.values())
    assert all(item.model_calls == 2 for item in outcomes.values())
    assert "supervisor.route" in outcomes["supervisor"].trace_event_types
    assert "handoff" in outcomes["peer_to_peer"].trace_event_types
    assert "manager.dispatch" in outcomes["hierarchical"].trace_event_types
    assert "manager.receive" in outcomes["hierarchical"].trace_event_types
    assert "agent.tool_call" in outcomes["managed_agent_tool"].trace_event_types
    assert len({item.trace_digest for item in outcomes.values()}) == 4


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="workflow-lab is optional in the default unit environment",
)
def test_explicit_graph_uses_orchestration_only_checkpoint() -> None:
    case = patterns.load_phase9_pattern_cases()[0]
    outcome = patterns._run_pattern(case, "explicit_graph_node")

    assert outcome.stop_reason == "ACCEPTED"
    assert outcome.checkpoint_keys
    assert set(outcome.checkpoint_keys) <= {
        "case_id",
        "decision",
        "plan_digest",
        "stage",
    }
    assert not {"facts", "permissions", "capability", "erp"}.intersection(outcome.checkpoint_keys)


def test_lab_source_has_no_runtime_or_environment_access() -> None:
    source = Path(patterns.__file__).read_text(encoding="utf-8")

    assert "import frappe" not in source.lower()
    assert "from frappe" not in source.lower()
    assert "load_dotenv" not in source
    assert "os.environ" not in source
