"""Static safety contract for the Phase 6 Runs governance panel."""

from pathlib import Path

RUNS_PAGE = (
    Path(__file__).parents[1]
    / "synora_agentic_erp"
    / "synora_agentic_erp"
    / "page"
    / "runs"
    / "runs.js"
)


def test_runs_page_surfaces_governance_evidence_and_identifier_only_actions() -> None:
    source = RUNS_PAGE.read_text(encoding="utf-8")

    for required in (
        "governance-panel",
        "治理动作与执行证据",
        "execute_purchase_order",
        "reconcile_purchase_order",
        "proposal_digest",
        "line_amounts",
        "total_amount",
        "金额依据",
        "evidence_refs",
        "calculation_refs",
        "api_failure_copy",
        "生成计划请求被拒绝。",
        "取消请求被拒绝。",
        'aria-live="polite"',
        'type="button"',
        "frappe.utils.escape_html",
    ):
        assert required in source
    assert "target.submit(" not in source
    assert "ignore_permissions" not in source
    assert "payload: payload" not in source
    assert ".error.message" not in source
    assert 'state === "APPROVED" && !reservation' in source
