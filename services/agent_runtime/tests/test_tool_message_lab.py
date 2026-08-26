"""Assignment 3 starter test: a redacted Observation becomes a tool message."""

from uuid import UUID

import pytest
from agent_runtime.agent.contracts import observation_from_summary

from labs.agent_patterns.tool_message_lab import build_learning_tool_message

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")


@pytest.mark.xfail(strict=True, reason="Assignment 3 starter: learner implements the helper")
def test_learning_helper_builds_serializable_tool_message() -> None:
    observation = observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name="item.lookup",
        ok=True,
        summary="1 bounded item observation",
    )

    message = build_learning_tool_message(
        provider_tool_call_id="call-1",
        tool_name="item.lookup",
        observation=observation,
    )

    assert message.role == "tool"
    assert message.tool_call_id == "call-1"
    assert message.name == "item.lookup"
    assert message.content == observation.summary
    assert message.model_dump(mode="json")["role"] == "tool"
