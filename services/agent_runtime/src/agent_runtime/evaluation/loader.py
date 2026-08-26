"""Deterministic evaluation-set loaders for P3 and Phase 4.

P3 cases remain intentionally small and compatible.  Phase 4 cases have a
separate strict schema so trajectory expectations cannot silently change the
existing business-result loader.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.agent.contracts import StopCode, ToolName

CASES_DIR = Path(__file__).parent / "cases"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


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


class AgentExpected(_Strict):
    tool_sequence: tuple[ToolName, ...] = ()
    stop_reason: StopCode
    must_not_call: tuple[ToolName, ...] = ()
    min_observations: int = Field(default=0, ge=0, le=64)


class AgentEvaluationCase(_Strict):
    schema_version: Literal["1"] = "1"
    case_id: str
    goal: str
    allowed_tools: tuple[ToolName, ...]
    expected: AgentExpected
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentEvaluationSet:
    cases: tuple[AgentEvaluationCase, ...]

    def filter_tags(self, tags: set[str]) -> AgentEvaluationSet:
        return AgentEvaluationSet(
            cases=tuple(case for case in self.cases if tags.issubset(set(case.tags)))
        )


def _load_case(path: Path) -> EvaluationCase:
    return EvaluationCase.model_validate_json(path.read_text(encoding="utf-8"))


def load_cases(directory: Path = CASES_DIR) -> EvaluationSet:
    paths = sorted(directory.glob("p3-*.json"))
    return EvaluationSet(cases=tuple(_load_case(path) for path in paths))


def _load_agent_case(path: Path) -> AgentEvaluationCase:
    return AgentEvaluationCase.model_validate_json(path.read_text(encoding="utf-8"))


def load_agent_cases(directory: Path = CASES_DIR) -> AgentEvaluationSet:
    paths = sorted(directory.glob("p4-*.json"))
    return AgentEvaluationSet(cases=tuple(_load_agent_case(path) for path in paths))


def to_json(case: EvaluationCase | AgentEvaluationCase) -> dict[str, Any]:
    return case.model_dump()
