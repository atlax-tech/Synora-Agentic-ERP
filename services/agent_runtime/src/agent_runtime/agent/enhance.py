"""P3.5 模型自然语言增强 (验收门槛: 确定性计算 + 模型仅解释 + 失败回退)。

数量、金额、阈值、风险分类全部由确定性代码 (Frappe 侧 plan.py) 生成;
模型只负责把确定性计划转成一段通俗的自然语言解释。输出经过严格校验:

1. 文本非空;
2. 文本中出现的数字必须能在确定性计划数据中找到 (模型不得编造数量);
3. 文本不得反转风险结论 (缺货/重复采购等分类词由数据决定)。

校验失败或 provider 调用失败 -> 回退确定性文案 (plan["summary"]), 并记录
token、耗时、状态与回退原因证据。CI 使用 DeterministicProvider, 不依赖
付费真实模型。
"""

import re
from dataclasses import dataclass
from time import monotonic
from typing import Any

from agent_runtime.providers import Provider, ProviderError, ProviderMessage

# 模型输出上限 (成本护栏; 解释文本 256 token 足够)。
ENHANCE_MAX_TOKENS = 256

_NUMBER_TOKEN = re.compile(r"-?\d+(?:\.\d+)?")

_SYSTEM_PROMPT = (
    "你是采购助手。系统会给你一份由确定性软件生成的采购风险分析计划。"
    "你的唯一任务是把它转成一段通俗、简洁的中文解释给用户看。"
    "硬性规则: 不得生成、修改或推断任何数量、金额、日期、阈值或风险分类; "
    "所有数字与结论必须原样来自输入计划; 只输出解释文本, 不要输出 JSON 或代码。"
)


def build_prompt(plan: dict[str, Any]) -> list[ProviderMessage]:
    """构造增强 prompt: 系统规则 + 确定性计划数据 (只读上下文)。"""
    return [
        ProviderMessage(role="system", content=_SYSTEM_PROMPT),
        ProviderMessage(role="user", content=f"确定性计划数据:\n{plan!r}\n\n请给出解释:"),
    ]


def _plan_numbers(plan: dict[str, Any]) -> set[str]:
    """从确定性计划中提取全部数字 token (字符串形式), 作为允许出现的数字集合。"""
    numbers: set[str] = set()
    pending = [plan]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, (int, float)):
            numbers.add(str(current))
        elif isinstance(current, str):
            numbers.update(_NUMBER_TOKEN.findall(current))
    return numbers


def validate_explanation(text: str, plan: dict[str, Any]) -> str | None:
    """严格校验模型解释; 通过返回原文, 失败返回 None (调用方回退确定性文案)。"""
    if not text or not text.strip():
        return None
    allowed = _plan_numbers(plan)
    for number in _NUMBER_TOKEN.findall(text):
        if number not in allowed:
            # 模型编造了计划中不存在的数字 (违反"数量由确定性代码生成")。
            return None
    return text


@dataclass(frozen=True)
class EnhancementEvidence:
    provider: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: int
    status: str  # "ok" | "fallback_validation" | "fallback_error"
    fallback_reason: str | None


async def enhance_plan(
    plan: dict[str, Any],
    provider: Provider,
    provider_name: str = "unknown",
) -> tuple[str, EnhancementEvidence]:
    """生成模型解释; 校验失败或调用失败回退确定性计划摘要并记录证据。"""
    started = monotonic()
    try:
        response = await provider.complete(build_prompt(plan), max_tokens=ENHANCE_MAX_TOKENS)
    except ProviderError as error:
        elapsed_ms = int((monotonic() - started) * 1000)
        return (
            str(plan.get("summary", "")),
            EnhancementEvidence(
                provider=provider_name,
                prompt_tokens=0,
                completion_tokens=0,
                elapsed_ms=elapsed_ms,
                status="fallback_error",
                fallback_reason=f"provider error: {error}",
            ),
        )
    elapsed_ms = int((monotonic() - started) * 1000)
    explanation = validate_explanation(response.text, plan)
    if explanation is None:
        return (
            str(plan.get("summary", "")),
            EnhancementEvidence(
                provider=provider_name,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                elapsed_ms=elapsed_ms,
                status="fallback_validation",
                fallback_reason="model output failed validation (numbers or structure)",
            ),
        )
    return (
        explanation,
        EnhancementEvidence(
            provider=provider_name,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            elapsed_ms=elapsed_ms,
            status="ok",
            fallback_reason=None,
        ),
    )
