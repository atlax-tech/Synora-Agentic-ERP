"""评测集加载器测试: 加载固定 case, 保留原始输入与期望输出。"""

from pathlib import Path

from agent_runtime.evaluation.loader import load_agent_cases, load_cases

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


def test_loads_phase4_agent_cases_with_strict_trajectory_expectations() -> None:
    evaluation = load_agent_cases(CASES_DIR)
    assert len(evaluation.cases) == 8
    assert {case.case_id for case in evaluation.cases} == {
        "P4-G01-observation-driven-second-tool",
        "P4-G02-repeated-same-call",
        "P4-G03-unknown-tool",
        "P4-G04-invalid-args",
        "P4-G05-tool-error",
        "P4-G06-no-progress",
        "P4-G07-output-budget",
        "P4-G08-malicious-observation",
    }
    assert all(case.schema_version == "1" for case in evaluation.cases)


def test_phase4_filter_tags_is_deterministic() -> None:
    evaluation = load_agent_cases(CASES_DIR)
    filtered = evaluation.filter_tags({"security"})
    assert filtered.cases
    assert all("security" in case.tags for case in filtered.cases)

# G02 的规则是：Agent 调用 stock.projected，第二次又用完全相同的参数调用它时，
# 应该因为重复而停止。
# 所以这不是实现“停止功能”，而是在写一张“期望结果清单”
def test_repeated_case_declares_stop_contract() -> None:
    # 定义一个测试函数。-> None 表示这个函数不反悔结果。
    evaluation = load_agent_cases(CASES_DIR) # 这里调用了已经存在的函数 load_agent_cases(...)

    # 这段写法采用了Python的生成器和next(),对新手不太友好
    # case = next(
    #     item
    #     for item in evaluation.cases
    #     if item.case_id == "P4-G02-repeated-same-call"
    # )

    # 传统写法：
    case = None # 准备一个空变量
    for candidate in evaluation.cases: # 遍历所有case，依次拿出每一个case称为candidate
        if candidate.case_id == "P4-G02-repeated-same-call": # 判断是不是G02
            case = candidate
            break
    assert case is not None

    # 含义：工具调用顺序
    # 表示期望的工具调用顺序必须是第一次 stock.projected，第二次还是 stock.projected
    #语法解释，使用元组的原因：定义一组固定顺序的数据
    # 这里有两个相同的字符串，所以表示两次调用。
    # JSON 里的数组：
    # ["stock.projected", "stock.projected"]
    # 被 Pydantic loader 读出来之后，会变成 Python 元组：
    # ("stock.projected", "stock.projected")
    # 所以测试里要用元组比较。
    assert case.expected.tool_sequence == (
        "stock.projected",
        "stock.projected",
    )

    # G02 这个 case 期望的停止原因必须是 REPEATED_CALL。
    assert case.expected.stop_reason == "REPEATED_CALL"
    # 在停止前，至少需要有 1 次 Observation。
    # 为什么不是 0？因为 Agent 必须先执行一次工具并得到结果，
    # 之后再次发起相同调用，系统才知道它是在重复调用。
    assert case.expected.min_observations == 1

    # tool_sequence 只有两次调用，表示第二次重复后就停止，
    # 不应该再出现第三次工具调用。
    assert len(case.expected.tool_sequence) == 2

    # 当前 G02 没有额外禁止调用的工具。
    assert case.expected.must_not_call == ()
