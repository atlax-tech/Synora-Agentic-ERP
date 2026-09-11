"""Bounded Web/GUI and multimodal observations for Phase 11.

This package is a LAB_ONLY experiment.  It is intentionally not imported by
the ERP app or the Agent Runtime business path.
"""

from labs.web_gui.browser import run_aria_task, run_dom_task, run_security_probe
from labs.web_gui.contracts import (
    ActionProposal,
    ActionReceipt,
    Observation,
    ObservationMode,
    TaskResult,
    TaskSpec,
)
from labs.web_gui.erp_browser import compare_erp_readonly, read_erp_web
from labs.web_gui.erp_readonly import (
    ErpComparison,
    ErpFact,
    ErpReadConfig,
    ErpReadResult,
    read_erp_api,
)
from labs.web_gui.erp_visual import ErpVisualRun, run_erp_visual_task
from labs.web_gui.fixtures import FIXTURE_ORDERS, create_app
from labs.web_gui.gui import VisualDecision, VisualRun, run_visual_task
from labs.web_gui.redaction import RedactedCapture, capture_redacted_page
from labs.web_gui.security import BrowserSecurityPolicy

__all__ = [
    "FIXTURE_ORDERS",
    "ActionProposal",
    "ActionReceipt",
    "BrowserSecurityPolicy",
    "ErpComparison",
    "ErpFact",
    "ErpReadConfig",
    "ErpReadResult",
    "ErpVisualRun",
    "Observation",
    "ObservationMode",
    "RedactedCapture",
    "TaskResult",
    "TaskSpec",
    "VisualDecision",
    "VisualRun",
    "capture_redacted_page",
    "compare_erp_readonly",
    "create_app",
    "read_erp_api",
    "read_erp_web",
    "run_aria_task",
    "run_dom_task",
    "run_erp_visual_task",
    "run_security_probe",
    "run_visual_task",
]
