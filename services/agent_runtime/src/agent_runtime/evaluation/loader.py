"""P3.4 评测集: 固定输入/期望输出的同一数据集 (ARCHITECTURE Model access)。

任何候选 provider 或分析版本都使用同一数据集比较, 保留原始输入、
期望输出与运行环境, 结果可复跑; 原始数据禁止被当作营销声明。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

CASES_DIR = Path(__file__).parent / "cases"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expected(_Strict):
    item_code: str
    risk: str


class EvaluationCase(_Strict):
    case_id: str
    goal: str
    expected: Expected
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationSet:
    cases: tuple[EvaluationCase, ...]

    def filter_tags(self, tags: set[str]) -> EvaluationSet:
        return EvaluationSet(
            cases=tuple(case for case in self.cases if tags.issubset(set(case.tags)))
        )


def _load_case(path: Path) -> EvaluationCase:
    return EvaluationCase.model_validate_json(path.read_text(encoding="utf-8"))


def load_cases(directory: Path = CASES_DIR) -> EvaluationSet:
    paths = sorted(directory.glob("*.json"))
    return EvaluationSet(cases=tuple(_load_case(path) for path in paths))


def to_json(case: EvaluationCase) -> dict[str, Any]:
    return case.model_dump()
