"""Static safety contract for the T08.3 contextual Coach form action."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

APP_ROOT = Path(__file__).parents[1] / "synora_agentic_erp"
HOOKS = APP_ROOT / "hooks.py"
SCRIPT = APP_ROOT / "public" / "js" / "contextual_coach.js"


def _doctype_js_mapping() -> dict[str, str]:
    tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "doctype_js" for target in node.targets
        )
    )
    return cast(dict[str, str], ast.literal_eval(assignment.value))


def test_hooks_register_only_the_two_contextual_coach_doctypes() -> None:
    assert _doctype_js_mapping() == {
        "Material Request": "public/js/contextual_coach.js",
        "Purchase Order": "public/js/contextual_coach.js",
    }


def test_contextual_coach_script_keeps_the_one_shot_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for required in (
        'frappe.ui.form.on("Material Request"',
        'frappe.ui.form.on("Purchase Order"',
        "synora_agentic_erp.api.issue_run",
        "synora_agentic_erp.api.ask_coach",
        'execution_mode: "DETERMINISTIC"',
        "current_doctype: frm.doctype",
        "current_name: frm.doc.name",
        "frm.is_new()",
        "frm.is_dirty()",
        "max_length: 1000",
        'maxlength", 1000',
        'prop("disabled", value)',
        "ERP_FACT",
        "RETRIEVED_KNOWLEDGE",
        "RECOMMENDATION",
        'answer_status === "CONFLICT"',
        'answer_status === "UNKNOWN"',
        'answer_status === "REFUSED"',
        "LIVE_ERP",
        "RETRIEVAL",
        "citation_refs",
        ".text(",
        "delete run.capability",
        "request_args.capability = null",
    ):
        assert required in source

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "frappe.route_options",
        "frappe.set_route",
        "console.log",
        "frappe.db",
        "frappe.model.set_value",
    ):
        assert forbidden not in source


def test_contextual_coach_requests_do_not_send_server_owned_fields() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    issue_start = source.index('method: "synora_agentic_erp.api.issue_run"')
    issue_end = source.index("callback:", issue_start)
    issue_request = source[issue_start:issue_end]
    for field in ("company:", "goal:", 'execution_mode: "DETERMINISTIC"', "time_window_days: 90"):
        assert field in issue_request
    for field in ("warehouse:", "correlation_id:", "facts:", "citations:", "retrieval:", "tools:"):
        assert field not in issue_request

    ask_start = source.index('method: "synora_agentic_erp.api.ask_coach"')
    ask_end = source.index("callback:", ask_start)
    ask_request = source[ask_start:ask_end]
    assert "args: request_args" in ask_request
    for field in (
        "run_id:",
        "capability:",
        "question:",
        "current_doctype:",
        "current_name:",
    ):
        assert (
            field
            in source[
                source.index("request_args = {") : source.index(
                    "};", source.index("request_args = {")
                )
            ]
        )
    for field in (
        "company:",
        "warehouse:",
        "correlation_id:",
        "facts:",
        "citations:",
        "retrieval:",
        "tools:",
    ):
        assert field not in ask_request
