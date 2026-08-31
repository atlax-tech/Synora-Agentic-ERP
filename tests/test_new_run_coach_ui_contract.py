"""Static safety contract for the P8.5 New Run Coach workflow."""

from pathlib import Path

PAGE = (
    Path(__file__).parents[1]
    / "synora_agentic_erp"
    / "synora_agentic_erp"
    / "page"
    / "new_run"
    / "new_run.js"
)


def test_new_run_exposes_a_separate_coach_purpose_and_optional_context() -> None:
    source = PAGE.read_text(encoding="utf-8")

    for required in (
        'options: "PROCUREMENT_ANALYSIS\\nERP_COACH"',
        'fieldname: "coach_question"',
        'fieldname: "coach_context_type"',
        'fieldname: "coach_context_name"',
        'fieldtype: "Link"',
        'method: "synora_agentic_erp.api.start_erp_coach"',
        "current_doctype",
        "current_name",
        "context_doctype && !context_name",
        'frappe.set_route("runs")',
    ):
        assert required in source

    assert "capability" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
