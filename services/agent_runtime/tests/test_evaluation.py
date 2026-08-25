"""评测集加载器测试: 加载固定 case, 保留原始输入与期望输出。"""

from pathlib import Path

from agent_runtime.evaluation.loader import load_cases

CASES_DIR = Path(__file__).parent.parent / "src" / "agent_runtime" / "evaluation" / "cases"


def test_loads_curated_cases() -> None:
    evaluation = load_cases(CASES_DIR)
    assert len(evaluation.cases) >= 1
    first = evaluation.cases[0]
    assert first.case_id
    assert first.goal
    assert first.expected.item_code
    assert first.expected.risk in {
        "SHORTAGE",
        "ADEQUATE",
        "DUPLICATE_RISK",
        "NO_DEMAND",
        "NEEDS_INPUT",
        "UNKNOWN",
    }


def test_filter_tags_is_deterministic() -> None:
    evaluation = load_cases(CASES_DIR)
    filtered = evaluation.filter_tags({"duplicate-risk"})
    assert filtered.cases
    assert all("duplicate-risk" in case.tags for case in filtered.cases)


def test_filter_tags_returns_empty_for_unknown_tag() -> None:
    evaluation = load_cases(CASES_DIR)
    assert evaluation.filter_tags({"never-tagged"}).cases == ()
