"""Bounded Web/GUI and multimodal observations for Phase 11.

This package is a LAB_ONLY experiment.  It is intentionally not imported by
the ERP app or the Agent Runtime business path.
"""

from labs.web_gui.browser import run_aria_task, run_dom_task
from labs.web_gui.contracts import (
    ActionProposal,
    ActionReceipt,
    Observation,
    ObservationMode,
    TaskResult,
    TaskSpec,
)
from labs.web_gui.fixtures import FIXTURE_ORDERS, create_app

__all__ = [
    "FIXTURE_ORDERS",
    "ActionProposal",
    "ActionReceipt",
    "Observation",
    "ObservationMode",
    "TaskResult",
    "TaskSpec",
    "create_app",
    "run_aria_task",
    "run_dom_task",
]
