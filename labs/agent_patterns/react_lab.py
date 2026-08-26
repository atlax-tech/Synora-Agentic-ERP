"""Beginner-friendly recorded ReAct lab.

This module is deliberately safe to run offline.  It uses the shared public
contracts but never receives a production capability or calls ERPNext.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from agent_runtime.agent.contracts import Action, Observation, ToolName
from agent_runtime.agent.kernel import (
    KernelLimits,
    ModelAdapter,
    ToolAdapter,
    ToolExecutionFailure,
    run_bounded_react,
)
from agent_runtime.providers import ProviderMessage, ProviderToolSpec

READ_TOOL_SPECS: tuple[ProviderToolSpec, ...] = tuple(
    ProviderToolSpec(name=name, description="Phase 4 recorded read-only tool", parameters={})
    for name in (
        "item.lookup",
        "supplier.lookup",
        "stock.projected",
        "demand.open",
        "material_request.open",
        "purchase_order.open",
    )
)


class ScriptedModel:
    """Return pre-written untrusted dictionaries one step at a time."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def next(
        self,
        *,
        messages: tuple[ProviderMessage, ...],
        tools: tuple[ProviderToolSpec, ...],
        step: int,
    ) -> object:
        del messages, tools, step
        self.calls += 1
        if not self._responses:
            raise RuntimeError("scripted model has no response")
        return self._responses.pop(0)


class RecordedToolAdapter:
    """Return recorded observations and retain only typed action history."""

    def __init__(
        self,
        outcomes: Mapping[ToolName, Observation | ToolExecutionFailure],
    ) -> None:
        self._outcomes = dict(outcomes)
        self.calls: list[Action] = []

    async def execute(self, action: Action) -> Observation:
        self.calls.append(action)
        outcome = self._outcomes[action.tool_name]
        if isinstance(outcome, ToolExecutionFailure):
            raise outcome
        return outcome.model_copy(update={"step": action.step})


class LearningRepeatedCallGuard:
    """Assignment 2 completed: identify the second identical tool call.

    业务背景: 如果模型一直查询同一个工具和同一组参数, 系统会浪费
    token、时间和费用. 这个 class 只在离线 lab 使用, 生产 guards 由导师
    练习已通过测试; 生产 guards 仍由共享内核和后续 P4.4 门禁负责。

    输入: 一个已经通过 Action schema 的动作.
    输出: 第一次看到该动作返回 False; 第二次及以后应返回 True.

    回顾传统思路 (练习已完成):

        seen_keys = self._seen
        current_key = action.call_key()
        if current_key in seen_keys:
            return True
        seen_keys.add(current_key)
        return False

    高级写法以后可以用 set 的查找和更新合并, 但本练习先使用上面的
    `if -> add -> return` 三步传统写法, 便于逐行调试.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def check(self, action: Action) -> bool:
        # 先读取 canonical call key, 再判断并记录本次 key.
        seen_keys = self._seen
        key = action.call_key()
        if key in seen_keys:
            return True
        self._seen.add(key)
        return False


async def run_react_lab(
    *,
    run_id: UUID,
    correlation_id: UUID,
    model: ModelAdapter,
    tool_adapter: ToolAdapter,
    allowed_tools: frozenset[ToolName],
) -> Any:
    """Run the lab with the learner-owned guard."""
    return await run_bounded_react(
        run_id=run_id,
        correlation_id=correlation_id,
        model=model,
        tool_adapter=tool_adapter,
        allowed_tools=allowed_tools,
        repeat_guard=LearningRepeatedCallGuard(),
        tools=READ_TOOL_SPECS,
        limits=KernelLimits(max_steps=4),
    )
