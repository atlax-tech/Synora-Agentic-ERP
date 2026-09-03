"""P3.5 模型自然语言增强 (验收门槛: 确定性计算 + 模型仅解释 + 失败回退)。

数量、金额、阈值、风险分类全部由确定性代码 (Frappe 侧 plan.py) 生成;
模型只负责把确定性计划转成一段通俗的自然语言解释。输出经过严格校验:

1. 文本非空;
2. 文本中出现的数字必须能在确定性计划数据中找到 (模型不得编造数量);
3. 文本不得反转风险结论 (缺货/重复采购等分类词由数据决定)。

校验失败或 provider 调用失败 -> 回退经过同一安全校验的确定性文案; 不安全或
损坏的摘要使用固定安全文案, 并记录 token、耗时、状态与固定回退原因证据。CI 使用
DeterministicProvider, 不依赖
付费真实模型。
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

from agent_runtime.agent.context import (
    CONTEXT_BUILDER_VERSION,
    ContextBuilder,
    ContextBuildError,
    ContextBuildResult,
    record_provider_prompt_tokens,
)
from agent_runtime.agent.contracts import canonical_json
from agent_runtime.agent.prompting import (
    PLAN_ENHANCEMENT_PROFILE_ID,
    PROMPT_REGISTRY,
    PROMPT_SCHEMA_VERSION,
    build_prompt_messages,
)
from agent_runtime.agent.safety import check_safe_text
from agent_runtime.providers import Provider, ProviderError, ProviderMessage

# 模型输出上限 (成本护栏; 解释文本 256 token 足够)。
ENHANCE_MAX_TOKENS = 256
ENHANCEMENT_TASK_PROFILE = "PLAN_ENHANCEMENT"
SAFE_ENHANCEMENT_FALLBACK = "无法生成计划解释，请人工核对确定性计划。"

_NUMBER_TOKEN = re.compile(r"-?\d+(?:\.\d+)?")

# 风险结论反转词 (keyword 级确定性校验, 防止模型把缺货说成充足、把重复采购
# 风险说成需要采购)。词表与 plan.py 的确定性 recommendation 文案核对过,
# 不包含"不建议重复采购/供应充足"等合法表达中的子串, 避免误伤。
_RISK_INVERTED_TERMS: dict[str, tuple[str, ...]] = {
    "SHORTAGE": ("充足", "足够", "充裕", "无需", "不必", "不需要", "不用补", "无缺口"),
    "DUPLICATE_RISK": (
        "建议补货",
        "需要采购",
        "应当补货",
        "建议采购",
        "立即下单",
        "急需补货",
        "需要立即",
        "必须采购",
    ),
}
# 计划中不存在缺货结论时, 文本声称缺货/断货 -> 编造风险。
_INVENTED_SHORTAGE_TERMS = ("已缺货", "目前缺货", "严重缺货", "发生短缺", "库存耗尽", "断货")
# 计划全部为缺货结论时, 文本声称供应过剩 -> 编造风险。
_INVENTED_SURPLUS_TERMS = ("供应过剩", "完全不需要采购", "过剩")


def build_prompt(plan: dict[str, Any]) -> list[ProviderMessage]:
    """构造增强 prompt: 系统规则 + 确定性计划数据 (只读上下文)。"""
    messages, _ = build_prompt_messages(
        PLAN_ENHANCEMENT_PROFILE_ID,
        user_content=f"确定性计划数据:\n{plan!r}\n\n请给出解释:",
    )
    return messages


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


def _risk_semantic_check(text: str, plan: dict[str, Any]) -> str | None:
    """风险结论反转/编造检查; 返回错误原因或 None。"""
    findings = plan.get("findings") or []
    risks = {str(finding.get("risk")) for finding in findings if isinstance(finding, dict)}
    for risk, terms in _RISK_INVERTED_TERMS.items():
        if risk in risks and any(term in text for term in terms):
            return f"inverted {risk} conclusion"
    if "SHORTAGE" not in risks and any(term in text for term in _INVENTED_SHORTAGE_TERMS):
        return "invented shortage"
    if risks and risks <= {"SHORTAGE"} and any(term in text for term in _INVENTED_SURPLUS_TERMS):
        return "invented surplus"
    return None


def validate_explanation(text: str, plan: dict[str, Any]) -> str | None:
    """严格校验模型解释; 通过返回原文, 失败返回 None (调用方回退确定性文案)。

    校验项: 非空; 文本中的数字必须存在于确定性计划数据 (模型不得编造数量);
    文本不得反转或编造风险结论 (语义 keyword 级确定性校验)。
    """
    if not text or not text.strip():
        return None
    allowed = _plan_numbers(plan)
    for number in _NUMBER_TOKEN.findall(text):
        if number not in allowed:
            # 模型编造了计划中不存在的数字 (违反"数量由确定性代码生成")。
            return None
    semantic_error = _risk_semantic_check(text, plan)
    if semantic_error is not None:
        return None
    try:
        check_safe_text(text, field_name="enhancement")
    except ValueError:
        return None
    return text


@dataclass(frozen=True)
class EnhancementEvidence:
    provider: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    elapsed_ms: int
    status: str  # "ok" | "fallback_validation" | "fallback_error"
    fallback_reason: str | None
    prompt_schema_version: str = PROMPT_SCHEMA_VERSION
    context_builder_version: str = CONTEXT_BUILDER_VERSION
    prompt_profile_id: str = PLAN_ENHANCEMENT_PROFILE_ID
    prompt_profile_hash: str = PROMPT_REGISTRY.resolve(PLAN_ENHANCEMENT_PROFILE_ID).profile_hash
    estimated_input_units_before: int = 0
    estimated_input_units_after: int = 0
    input_budget: int | None = None
    actual_prompt_tokens: int | None = None
    compression_reasons: tuple[str, ...] = ()
    dropped_fragment_ids: tuple[str, ...] = ()
    skill_refs: tuple[str, ...] = ()
    unauthorized_tool_calls: int = 0
    # Digest of the exact serialized provider messages; the message content
    # itself is never persisted or returned by the API.
    input_digest: str | None = None
    model_calls: int = 1


def safe_deterministic_fallback(plan: Mapping[str, Any]) -> str:
    """Return a deterministic summary only after normal output checks."""
    summary = plan.get("summary")
    if isinstance(summary, str) and validate_explanation(summary, dict(plan)) is not None:
        return summary
    return SAFE_ENHANCEMENT_FALLBACK


def build_context(
    plan: dict[str, Any],
    *,
    environ: Mapping[str, str] | None,
) -> ContextBuildResult:
    """Build the bounded enhancement context with an explicit input budget."""
    return ContextBuilder().build(
        profile_id=PLAN_ENHANCEMENT_PROFILE_ID,
        goal=canonical_json(plan),
        task_profile=ENHANCEMENT_TASK_PROFILE,
        tools=(),
        allowed_tools=frozenset(),
        environ=environ,
    )


def _context_evidence(
    context_result: ContextBuildResult | None,
    *,
    actual_prompt_tokens: int | None = None,
) -> dict[str, object]:
    if context_result is None:
        return {
            "actual_prompt_tokens": actual_prompt_tokens,
        }
    provenance = context_result.provenance
    return {
        "prompt_schema_version": provenance.prompt_schema_version,
        "context_builder_version": provenance.builder_version,
        "prompt_profile_id": provenance.prompt_profile_id,
        "prompt_profile_hash": provenance.prompt_profile_hash,
        "estimated_input_units_before": context_result.estimated_input_units_before,
        "estimated_input_units_after": context_result.estimated_input_units_after,
        "input_budget": context_result.input_budget,
        "actual_prompt_tokens": (
            actual_prompt_tokens
            if actual_prompt_tokens is not None
            else provenance.actual_prompt_tokens
        ),
        "compression_reasons": context_result.compression_reasons,
        "dropped_fragment_ids": context_result.dropped_fragment_ids,
        "skill_refs": provenance.skill_refs,
    }


def _make_evidence(
    *,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    reasoning_tokens: int,
    elapsed_ms: int,
    status: str,
    fallback_reason: str | None,
    context_result: ContextBuildResult | None,
    actual_prompt_tokens: int | None = None,
    unauthorized_tool_calls: int = 0,
    input_digest: str | None = None,
    model_calls: int = 1,
) -> EnhancementEvidence:
    metadata = _context_evidence(
        context_result,
        actual_prompt_tokens=actual_prompt_tokens,
    )

    def metadata_int(key: str, default: int = 0) -> int:
        value = metadata.get(key, default)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    def metadata_optional_int(key: str) -> int | None:
        value = metadata.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def metadata_strings(key: str) -> tuple[str, ...]:
        value = metadata.get(key, ())
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            return ()
        return tuple(value)

    if input_digest is None and context_result is not None:
        input_digest = hashlib.sha256(
            canonical_json(
                [message.model_dump(mode="json") for message in context_result.messages]
            ).encode("utf-8")
        ).hexdigest()

    return EnhancementEvidence(
        provider=provider,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        elapsed_ms=elapsed_ms,
        status=status,
        fallback_reason=fallback_reason,
        prompt_schema_version=str(metadata.get("prompt_schema_version", PROMPT_SCHEMA_VERSION)),
        context_builder_version=str(
            metadata.get("context_builder_version", CONTEXT_BUILDER_VERSION)
        ),
        prompt_profile_id=str(metadata.get("prompt_profile_id", PLAN_ENHANCEMENT_PROFILE_ID)),
        prompt_profile_hash=str(
            metadata.get(
                "prompt_profile_hash",
                PROMPT_REGISTRY.resolve(PLAN_ENHANCEMENT_PROFILE_ID).profile_hash,
            )
        ),
        estimated_input_units_before=metadata_int("estimated_input_units_before"),
        estimated_input_units_after=metadata_int("estimated_input_units_after"),
        input_budget=metadata_optional_int("input_budget"),
        actual_prompt_tokens=metadata_optional_int("actual_prompt_tokens"),
        compression_reasons=metadata_strings("compression_reasons"),
        dropped_fragment_ids=metadata_strings("dropped_fragment_ids"),
        skill_refs=metadata_strings("skill_refs"),
        unauthorized_tool_calls=max(0, unauthorized_tool_calls),
        input_digest=input_digest,
        model_calls=max(0, model_calls),
    )


def _provider_failure_code(error: ProviderError) -> str:
    """Map provider details to a fixed, non-secret evidence code."""
    if error.budget_code == "TOKEN_BUDGET":
        return "TOKEN_BUDGET"
    code = str(getattr(error, "failure_code", "PROVIDER_ERROR"))
    allowed = {
        "TIMEOUT",
        "CANCELLED",
        "TRANSPORT_ERROR",
        "RESPONSE_SCHEMA",
        "RESPONSE_NO_CHOICES",
        "RESPONSE_CONTENT_MISSING",
        "RESPONSE_TOO_LARGE",
        "HTTP_ERROR",
        "INVALID_REQUEST",
        "PROVIDER_ERROR",
    }
    return code if code in allowed else "PROVIDER_ERROR"


async def enhance_plan(
    plan: dict[str, Any],
    provider: Provider,
    provider_name: str = "unknown",
    *,
    context_environ: Mapping[str, str] | None = None,
) -> tuple[str, EnhancementEvidence]:
    """生成模型解释; 校验失败或调用失败回退确定性计划摘要并记录证据。"""
    started = monotonic()
    findings = plan.get("findings")
    risks = (
        {str(finding.get("risk")) for finding in findings if isinstance(finding, dict)}
        if isinstance(findings, list)
        else set()
    )
    deterministic_exception = (
        "RECONCILIATION_REQUIRED"
        if "RECONCILIATION_REQUIRED" in risks
        else "INPUT_REQUIRED"
        if "INPUT_REQUIRED" in risks
        else None
    )
    if deterministic_exception is not None:
        return (
            safe_deterministic_fallback(plan),
            _make_evidence(
                provider=provider_name,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=0,
                elapsed_ms=int((monotonic() - started) * 1000),
                status="fallback_deterministic",
                fallback_reason=f"deterministic exception: {deterministic_exception}",
                context_result=None,
                model_calls=0,
            ),
        )
    try:
        context_result = build_context(plan, environ=context_environ)
    except ContextBuildError as error:
        status = (
            "fallback_context_budget"
            if error.code == "CONTEXT_BUDGET"
            else "fallback_context_invalid"
        )
        return (
            safe_deterministic_fallback(plan),
            _make_evidence(
                provider=provider_name,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=0,
                elapsed_ms=int((monotonic() - started) * 1000),
                status=status,
                fallback_reason=f"context failure: {error.code}",
                context_result=error.result,
                model_calls=0,
            ),
        )
    try:
        response = await provider.complete(
            list(context_result.messages),
            tools=[],
            max_tokens=ENHANCE_MAX_TOKENS,
        )
    except ProviderError as error:
        elapsed_ms = int((monotonic() - started) * 1000)
        try:
            context_result = record_provider_prompt_tokens(context_result, error.prompt_tokens)
        except ContextBuildError as context_error:
            context_result = context_error.result or context_result
            status = (
                "fallback_context_budget"
                if context_error.code == "CONTEXT_BUDGET"
                else "fallback_context_invalid"
            )
        else:
            status = "fallback_error"
        return (
            safe_deterministic_fallback(plan),
            _make_evidence(
                provider=provider_name,
                prompt_tokens=error.prompt_tokens,
                completion_tokens=error.completion_tokens,
                reasoning_tokens=error.reasoning_tokens,
                elapsed_ms=elapsed_ms,
                status=status,
                fallback_reason=f"provider failure: {_provider_failure_code(error)}",
                context_result=context_result,
                actual_prompt_tokens=error.prompt_tokens,
            ),
        )
    elapsed_ms = int((monotonic() - started) * 1000)
    try:
        context_result = record_provider_prompt_tokens(context_result, response.prompt_tokens)
    except ContextBuildError as error:
        status = (
            "fallback_context_budget"
            if error.code == "CONTEXT_BUDGET"
            else "fallback_context_invalid"
        )
        return (
            safe_deterministic_fallback(plan),
            _make_evidence(
                provider=provider_name,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                reasoning_tokens=response.reasoning_tokens,
                elapsed_ms=elapsed_ms,
                status=status,
                fallback_reason=f"context failure: {error.code}",
                context_result=error.result or context_result,
                actual_prompt_tokens=response.prompt_tokens,
            ),
        )
    if response.tool_calls:
        return (
            safe_deterministic_fallback(plan),
            _make_evidence(
                provider=provider_name,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                reasoning_tokens=response.reasoning_tokens,
                elapsed_ms=elapsed_ms,
                status="fallback_validation",
                fallback_reason="model output failed validation (unauthorized tool calls)",
                context_result=context_result,
                actual_prompt_tokens=response.prompt_tokens,
                unauthorized_tool_calls=len(response.tool_calls),
            ),
        )
    explanation = validate_explanation(response.text, plan)
    if explanation is None:
        return (
            safe_deterministic_fallback(plan),
            _make_evidence(
                provider=provider_name,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                reasoning_tokens=response.reasoning_tokens,
                elapsed_ms=elapsed_ms,
                status="fallback_validation",
                fallback_reason="model output failed validation (numbers or structure)",
                context_result=context_result,
                actual_prompt_tokens=response.prompt_tokens,
            ),
        )
    return (
        explanation,
        _make_evidence(
            provider=provider_name,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            reasoning_tokens=response.reasoning_tokens,
            elapsed_ms=elapsed_ms,
            status="ok",
            fallback_reason=None,
            context_result=context_result,
            actual_prompt_tokens=response.prompt_tokens,
        ),
    )
