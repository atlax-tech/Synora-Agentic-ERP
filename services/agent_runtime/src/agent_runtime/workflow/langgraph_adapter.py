"""Optional LangGraph lab adapter.

LangGraph is intentionally not imported at module import time and is not a
Runtime dependency in Phase 5.  The adapter exposes an explicit unavailable
result when the optional lab extra is absent; it never falls back to pickle or
silently widens the Synora workflow contract.
"""

from __future__ import annotations

import importlib.util
from typing import Any, TypedDict


class LangGraphUnavailable(RuntimeError):
    pass


class LabState(TypedDict, total=False):
    """Orchestration-only state used by the optional LangGraph lab."""

    lab_node_executed: bool


def langgraph_available() -> bool:
    return (
        importlib.util.find_spec("langgraph") is not None
        and importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
    )


def build_strict_lab_graph(*, checkpointer: Any) -> Any:
    """Build a tiny explicit graph only when the lab dependencies are installed."""
    if not langgraph_available():
        raise LangGraphUnavailable("LangGraph lab dependencies are not installed")
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # pragma: no cover - exercised by dependency spike
        raise LangGraphUnavailable("LangGraph lab imports are unavailable") from exc
    del SqliteSaver

    def execute_node(state: LabState) -> LabState:
        # The node contains no ERP side effect.  The real typed executor remains
        # outside the graph and must provide idempotent results before resuming.
        return {**state, "lab_node_executed": True}

    graph = StateGraph(LabState)
    graph.add_node("execute", execute_node)
    graph.add_edge(START, "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=checkpointer)
