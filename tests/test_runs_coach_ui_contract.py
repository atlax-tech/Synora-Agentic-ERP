"""Static safety contract for the P8.5 Runs Coach history panel."""

from pathlib import Path

PAGE = (
    Path(__file__).parents[1]
    / "synora_agentic_erp"
    / "synora_agentic_erp"
    / "page"
    / "runs"
    / "runs.js"
)


def test_runs_page_displays_safe_coach_result_history() -> None:
    source = PAGE.read_text(encoding="utf-8")

    for required in (
        'method: "synora_agentic_erp.api.coach_run_detail"',
        "coach-history",
        "coach-result",
        "ANSWERED",
        "CONFLICT",
        "UNKNOWN",
        "REFUSED",
        "逐条 Claim 与来源",
        'citation_type === "LIVE_ERP"',
        'citation_type === "RETRIEVAL"',
        "source_modified_at",
        "captured_at",
        "frappe_revision",
        "Trace 与运行元数据",
        "frappe.utils.escape_html",
    ):
        assert required in source

    for forbidden in (
        "run.capability",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "Provider raw",
    ):
        assert forbidden not in source
