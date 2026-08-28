"""Bounded Gather/Select/Structure/Compress context construction.

The builder deliberately keeps two measurements separate:
estimated_input_units is a conservative UTF-8 byte upper bound used before
the provider call, while actual_prompt_tokens is accepted only from the
provider response. Neither value is an authorization decision; the caller's
typed tool allowlist remains authoritative.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from agent_runtime.agent.budget import estimate_input_units
from agent_runtime.agent.contracts import Observation, StrictModel, canonical_json
from agent_runtime.agent.prompting import (
    PROMPT_REGISTRY,
    PROMPT_SCHEMA_VERSION,
    PromptVariant,
)
from agent_runtime.providers import ProviderMessage, ProviderToolSpec

CONTEXT_BUILDER_VERSION: Literal["1"] = "1"
CONTEXT_INPUT_TOKEN_BUDGET_ENV = "SYNORA_CONTEXT_INPUT_TOKEN_BUDGET"

ContextStopCode = Literal["CONTEXT_INVALID", "CONTEXT_BUDGET"]
ContextFragmentType = Literal[
    "prompt",
    "task_profile",
    "goal",
    "tool_schema",
    "skill",
    "skill_catalog",
    "reference",
    "observation",
]
ContextTrustLevel = Literal["SYSTEM", "CONTROLLED", "UNTRUSTED"]
ContextStage = Literal["gather", "select", "structure", "compress"]


def _tuple_from_json(value: object) -> object:
    """Keep strict immutable tuples while accepting decoded JSON arrays."""
    if isinstance(value, list):
        return tuple(value)
    return value


_TupleStrings = Annotated[tuple[str, ...], BeforeValidator(_tuple_from_json)]
_TupleMessages = Annotated[tuple[ProviderMessage, ...], BeforeValidator(_tuple_from_json)]
_TupleToolSpecs = Annotated[tuple[ProviderToolSpec, ...], BeforeValidator(_tuple_from_json)]


class ContextFragment(StrictModel):
    """One bounded, hashed input fragment used by the GSSC builder."""

    fragment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
    fragment_type: ContextFragmentType
    source: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:/-]{0,119}$")
    version: str = Field(min_length=1, max_length=40)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    trust_level: ContextTrustLevel
    priority: int = Field(ge=0, le=1_000)
    estimated_units: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=16_000)
    required: bool = False
    triggered: bool = True

    @model_validator(mode="after")
    def validate_integrity_and_source(self) -> ContextFragment:
        if (
            self.source.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:[\\/]", self.source)
            or "://" in self.source
        ):
            raise ValueError("context source must be a local logical source")
        if any(part == ".." for part in self.source.split("/")):
            raise ValueError("context source cannot traverse a path")
        expected_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.hash != expected_hash:
            raise ValueError("context fragment hash does not match content")
        expected_units = len(self.content.encode("utf-8"))
        if self.estimated_units != expected_units:
            raise ValueError("context fragment estimate does not match content")
        return self

    @classmethod
    def from_content(
        cls,
        *,
        fragment_id: str,
        fragment_type: ContextFragmentType | str,
        source: str,
        version: str,
        trust_level: ContextTrustLevel | str,
        priority: int,
        content: str,
        required: bool = False,
        triggered: bool = True,
    ) -> ContextFragment:
        return cls.model_validate(
            {
                "fragment_id": fragment_id,
                "fragment_type": fragment_type,
                "source": source,
                "version": version,
                "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "trust_level": trust_level,
                "priority": priority,
                "estimated_units": len(content.encode("utf-8")),
                "content": content,
                "required": required,
                "triggered": triggered,
            }
        )


class ContextStageDecision(StrictModel):
    """Metadata about one GSSC stage; it never contains fragment content."""

    stage: ContextStage
    selected_fragment_ids: _TupleStrings = ()
    dropped_fragment_ids: _TupleStrings = ()
    reason: str = Field(min_length=1, max_length=240)


_TupleStageDecisions = Annotated[
    tuple[ContextStageDecision, ...], BeforeValidator(_tuple_from_json)
]


class ContextProvenance(StrictModel):
    """Reproducibility metadata safe to persist in a Run evidence field."""

    builder_version: Literal["1"] = CONTEXT_BUILDER_VERSION
    prompt_schema_version: Literal["2"] = PROMPT_SCHEMA_VERSION
    prompt_profile_id: str = Field(min_length=1, max_length=120)
    prompt_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_refs: _TupleStrings = ()
    stage_decisions: _TupleStageDecisions = Field(min_length=4, max_length=4)
    actual_prompt_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_stage_order(self) -> ContextProvenance:
        stages = tuple(decision.stage for decision in self.stage_decisions)
        if stages != ("gather", "select", "structure", "compress"):
            raise ValueError("context stages must be recorded in GSSC order")
        return self


class ContextBuildResult(StrictModel):
    """Provider-ready messages plus bounded, non-secret context evidence."""

    messages: _TupleMessages = Field(min_length=2)
    effective_tools: _TupleToolSpecs = ()
    estimated_input_units_before: int = Field(ge=0)
    estimated_input_units_after: int = Field(ge=0)
    input_budget: int = Field(gt=0)
    selected_fragment_ids: _TupleStrings = ()
    dropped_fragment_ids: _TupleStrings = ()
    compression_reasons: _TupleStrings = ()
    compression_applied: bool = False
    provenance: ContextProvenance

    @model_validator(mode="after")
    def validate_estimates(self) -> ContextBuildResult:
        if self.estimated_input_units_after > self.estimated_input_units_before:
            raise ValueError("context compression cannot increase the estimate")
        if self.estimated_input_units_after > self.input_budget:
            raise ValueError("provider context exceeds the configured budget")
        return self


class ContextConfigurationError(ValueError):
    """The explicit input-budget environment value is missing or invalid."""


class ContextBuildError(ValueError):
    """Fail-closed context construction error with no raw context in its text."""

    def __init__(
        self,
        code: ContextStopCode,
        reason: str,
        *,
        result: ContextBuildResult | None = None,
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.result = result


def context_budget_from_environment(environ: Mapping[str, str] | None = None) -> int:
    """Parse the required positive input budget without a code default."""
    values = os.environ if environ is None else environ
    raw = values.get(CONTEXT_INPUT_TOKEN_BUDGET_ENV)
    if not isinstance(raw, str) or not re.fullmatch(r"[1-9][0-9]*", raw):
        raise ContextConfigurationError(
            f"{CONTEXT_INPUT_TOKEN_BUDGET_ENV} must be a positive integer"
        )
    try:
        budget = int(raw)
    except (TypeError, ValueError) as error:
        raise ContextConfigurationError(
            f"{CONTEXT_INPUT_TOKEN_BUDGET_ENV} must be a positive integer"
        ) from error
    if budget <= 0:
        raise ContextConfigurationError(
            f"{CONTEXT_INPUT_TOKEN_BUDGET_ENV} must be a positive integer"
        )
    return budget


def _fragment_payload(
    fragment: ContextFragment, *, include_content: bool = True
) -> dict[str, object]:
    payload: dict[str, object] = {
        "fragment_id": fragment.fragment_id,
        "fragment_type": fragment.fragment_type,
        "source": fragment.source,
        "version": fragment.version,
        "hash": fragment.hash,
        "trust_level": fragment.trust_level,
        "priority": fragment.priority,
    }
    if include_content:
        payload["content"] = fragment.content
    return payload


def _observation_payload(
    observation: Observation,
    *,
    summary_limit: int | None,
) -> dict[str, object]:
    summary = observation.summary
    if summary_limit is not None:
        summary = summary[:summary_limit]
    payload: dict[str, object] = {
        "step": observation.step,
        "tool_name": observation.tool_name,
        "status": "OK" if observation.ok else "ERROR",
        "digest": observation.digest,
        "trust_level": "UNTRUSTED",
        "summary": summary,
    }
    if observation.error_code is not None:
        payload["error_code"] = observation.error_code
    if summary != observation.summary:
        payload["excerpt_truncated"] = True
    return payload


def _structured_observations(
    observations: Sequence[Observation],
    *,
    mode: Literal["full", "structured", "summary"],
    old_excerpt: int,
    latest_excerpt: int,
) -> dict[str, object]:
    if mode == "full":
        return {
            "items": [
                _observation_payload(observation, summary_limit=None)
                for observation in observations
            ],
            "evidence_digests": [observation.digest for observation in observations],
        }
    older = observations[:-1]
    latest = observations[-1] if observations else None
    notes = [
        {
            "step": observation.step,
            "tool_name": observation.tool_name,
            "digest": observation.digest,
            "status": "OK" if observation.ok else "ERROR",
            "excerpt": observation.summary[:old_excerpt],
        }
        for observation in older
    ]
    result: dict[str, object] = {
        "structured_notes": notes,
        "evidence_digests": [observation.digest for observation in observations],
    }
    if latest is not None:
        result["latest_observation"] = _observation_payload(latest, summary_limit=latest_excerpt)
    if mode == "summary":
        result["summary_mode"] = "bounded"
    return result


def _user_payload(
    *,
    task_profile: str,
    goal: str,
    tool_fragments: Sequence[ContextFragment],
    skill_fragments: Sequence[ContextFragment],
    reference_fragments: Sequence[ContextFragment],
    observations: Sequence[Observation],
    observation_mode: Literal["full", "structured", "summary"],
    old_excerpt: int = 240,
    latest_excerpt: int = 4_000,
) -> str:
    payload = {
        "task_profile": {"trust_level": "CONTROLLED", "value": task_profile},
        "goal": {"trust_level": "UNTRUSTED", "value": goal},
        "available_tool_schemas": [
            _fragment_payload(fragment, include_content=False) for fragment in tool_fragments
        ],
        "skill_guidance": [_fragment_payload(fragment) for fragment in skill_fragments],
        "reference_resources": [_fragment_payload(fragment) for fragment in reference_fragments],
        "observations": _structured_observations(
            observations,
            mode=observation_mode,
            old_excerpt=old_excerpt,
            latest_excerpt=latest_excerpt,
        ),
    }
    return canonical_json(payload)


def _messages(
    *,
    profile_text: str,
    user_content: str,
) -> tuple[ProviderMessage, ...]:
    return (
        ProviderMessage(role="system", content=profile_text),
        ProviderMessage(role="user", content=user_content),
    )


class ContextBuilder:
    """Build a provider request through deterministic GSSC stages."""

    version: Literal["1"] = CONTEXT_BUILDER_VERSION

    def build(
        self,
        *,
        profile_id: str,
        goal: str,
        task_profile: str,
        tools: Sequence[ProviderToolSpec],
        allowed_tools: frozenset[str],
        observations: Sequence[Observation] = (),
        selected_skill_fragments: Sequence[ContextFragment] = (),
        skill_catalog_fragments: Sequence[ContextFragment] = (),
        reference_fragments: Sequence[ContextFragment] = (),
        prompt_variant: PromptVariant = "A",
        environ: Mapping[str, str] | None = None,
    ) -> ContextBuildResult:
        try:
            budget = context_budget_from_environment(environ)
        except ContextConfigurationError as error:
            raise ContextBuildError(
                "CONTEXT_BUDGET", "context input budget is unavailable"
            ) from error

        try:
            profile = PROMPT_REGISTRY.resolve(profile_id, variant=prompt_variant)
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", task_profile):
                raise ValueError("task profile is not a server task profile")
            effective_tools = self._validate_tools(tools, allowed_tools)
            selected_skill_fragments = tuple(
                sorted(selected_skill_fragments, key=lambda fragment: fragment.fragment_id)
            )
            skill_catalog_fragments = tuple(
                sorted(skill_catalog_fragments, key=lambda fragment: fragment.fragment_id)
            )
            reference_fragments = tuple(
                sorted(reference_fragments, key=lambda fragment: fragment.fragment_id)
            )
            observations = tuple(
                sorted(
                    observations,
                    key=lambda observation: (
                        observation.step,
                        observation.tool_name,
                        observation.digest,
                    ),
                )
            )
            (
                all_fragments,
                base_fragments,
                tool_fragments,
                observation_fragments,
            ) = self._gather(
                profile_text=profile.render(),
                profile_id=profile_id,
                prompt_variant=prompt_variant,
                task_profile=task_profile,
                goal=goal,
                tools=effective_tools,
                observations=observations,
                selected_skill_fragments=selected_skill_fragments,
                skill_catalog_fragments=skill_catalog_fragments,
                reference_fragments=reference_fragments,
            )
            self._validate_fragment_ids(all_fragments)
            self._validate_optional_fragments(skill_catalog_fragments, reference_fragments)
        except ContextBuildError:
            raise
        except Exception as error:
            raise ContextBuildError(
                "CONTEXT_INVALID", "context fragments or profile are invalid"
            ) from error

        all_ids = tuple(fragment.fragment_id for fragment in all_fragments)
        before_messages = _messages(
            profile_text=profile.render(),
            user_content=_user_payload(
                task_profile=task_profile,
                goal=goal,
                tool_fragments=tool_fragments,
                skill_fragments=(*selected_skill_fragments, *skill_catalog_fragments),
                reference_fragments=reference_fragments,
                observations=observations,
                observation_mode="full",
            ),
        )
        estimated_before = estimate_input_units(before_messages, effective_tools)

        selected_references = tuple(
            fragment for fragment in reference_fragments if fragment.triggered
        )
        selected_fragments = (
            *base_fragments,
            *selected_skill_fragments,
            *selected_references,
            *observation_fragments,
        )
        selected_ids = tuple(fragment.fragment_id for fragment in selected_fragments)
        dropped_ids = tuple(
            fragment.fragment_id
            for fragment in (*skill_catalog_fragments, *reference_fragments)
            if fragment.fragment_id not in selected_ids
        )
        reasons: list[str] = []
        if skill_catalog_fragments:
            reasons.append("removed unselected skill catalog")
        if any(not fragment.triggered for fragment in reference_fragments):
            reasons.append("removed untriggered reference")

        stage_decisions = [
            ContextStageDecision(
                stage="gather",
                selected_fragment_ids=all_ids,
                reason=(
                    "gathered boundary, task, caller, tool, skill, reference, and "
                    "observation inputs"
                ),
            ),
            ContextStageDecision(
                stage="select",
                selected_fragment_ids=selected_ids,
                dropped_fragment_ids=dropped_ids,
                reason="selected only server-authorized profile and triggered resources",
            ),
        ]

        def candidate(
            *,
            mode: Literal["full", "structured", "summary"],
            old_excerpt: int = 240,
            latest_excerpt: int = 4_000,
        ) -> tuple[tuple[ProviderMessage, ...], int]:
            messages = _messages(
                profile_text=profile.render(),
                user_content=_user_payload(
                    task_profile=task_profile,
                    goal=goal,
                    tool_fragments=tool_fragments,
                    skill_fragments=selected_skill_fragments,
                    reference_fragments=selected_references,
                    observations=observations,
                    observation_mode=mode,
                    old_excerpt=old_excerpt,
                    latest_excerpt=latest_excerpt,
                ),
            )
            return messages, estimate_input_units(messages, effective_tools)

        def result_for(
            messages: tuple[ProviderMessage, ...],
            estimated_after: int,
        ) -> ContextBuildResult:
            decisions = (
                *stage_decisions,
                ContextStageDecision(
                    stage="structure",
                    selected_fragment_ids=selected_ids,
                    reason="canonical JSON with explicit trust labels and stable field ordering",
                ),
                ContextStageDecision(
                    stage="compress",
                    selected_fragment_ids=selected_ids,
                    dropped_fragment_ids=dropped_ids,
                    reason="; ".join(reasons) if reasons else "context is within budget",
                ),
            )
            provenance = ContextProvenance(
                prompt_profile_id=profile.profile_id,
                prompt_profile_hash=profile.profile_hash,
                skill_refs=tuple(fragment.fragment_id for fragment in selected_skill_fragments),
                stage_decisions=decisions,
            )
            return ContextBuildResult(
                messages=messages,
                effective_tools=tuple(effective_tools),
                estimated_input_units_before=estimated_before,
                estimated_input_units_after=estimated_after,
                input_budget=budget,
                selected_fragment_ids=selected_ids,
                dropped_fragment_ids=dropped_ids,
                compression_reasons=tuple(reasons),
                compression_applied=bool(reasons)
                or len(messages[1].content) != len(before_messages[1].content),
                provenance=provenance,
            )

        messages, estimated_after = candidate(mode="full")
        if estimated_after <= budget:
            return result_for(messages, estimated_after)

        if len(observations) > 1:
            reasons.append("older observations converted to structured notes")
        messages, estimated_after = candidate(
            mode="structured", old_excerpt=240, latest_excerpt=4_000
        )
        if estimated_after <= budget:
            return result_for(messages, estimated_after)

        reasons.append("bounded observation excerpts applied")
        messages, estimated_after = candidate(
            mode="structured", old_excerpt=96, latest_excerpt=1_000
        )
        if estimated_after <= budget:
            return result_for(messages, estimated_after)

        reasons.append("bounded summary applied")
        messages, estimated_after = candidate(mode="summary", old_excerpt=0, latest_excerpt=256)
        if estimated_after <= budget:
            return result_for(messages, estimated_after)

        raise ContextBuildError("CONTEXT_BUDGET", "mandatory context exceeds the configured budget")

    @staticmethod
    def _validate_tools(
        tools: Sequence[ProviderToolSpec],
        allowed_tools: frozenset[str],
    ) -> tuple[ProviderToolSpec, ...]:
        seen: set[str] = set()
        validated: list[ProviderToolSpec] = []
        for tool in tools:
            if not tool.name or tool.name in seen or tool.name not in allowed_tools:
                raise ContextBuildError(
                    "CONTEXT_INVALID", "effective tools are not a caller allowlist subset"
                )
            seen.add(tool.name)
            validated.append(tool)
        return tuple(sorted(validated, key=lambda tool: tool.name))

    @staticmethod
    def _gather(
        *,
        profile_text: str,
        profile_id: str,
        prompt_variant: PromptVariant,
        task_profile: str,
        goal: str,
        tools: Sequence[ProviderToolSpec],
        observations: Sequence[Observation],
        selected_skill_fragments: Sequence[ContextFragment],
        skill_catalog_fragments: Sequence[ContextFragment],
        reference_fragments: Sequence[ContextFragment],
    ) -> tuple[
        tuple[ContextFragment, ...],
        tuple[ContextFragment, ...],
        tuple[ContextFragment, ...],
        tuple[ContextFragment, ...],
    ]:
        prompt = ContextFragment.from_content(
            fragment_id=f"prompt:{profile_id}:{prompt_variant.lower()}",
            fragment_type="prompt",
            source="registry:prompt",
            version=PROMPT_SCHEMA_VERSION,
            trust_level="SYSTEM",
            priority=1_000,
            content=profile_text,
            required=True,
        )
        task = ContextFragment.from_content(
            fragment_id=f"task:{task_profile.lower()}",
            fragment_type="task_profile",
            source="runtime:task-profile",
            version="1",
            trust_level="CONTROLLED",
            priority=900,
            content=task_profile,
            required=True,
        )
        goal_fragment = ContextFragment.from_content(
            fragment_id="goal:caller",
            fragment_type="goal",
            source="caller:goal",
            version="1",
            trust_level="UNTRUSTED",
            priority=800,
            content=goal,
            required=True,
        )
        tool_fragments = tuple(
            ContextFragment.from_content(
                fragment_id=f"tool:{tool.name}",
                fragment_type="tool_schema",
                source="caller:tool-allowlist",
                version="1",
                trust_level="CONTROLLED",
                priority=850,
                content=canonical_json(tool.model_dump(mode="json")),
                required=True,
            )
            for tool in tools
        )
        observation_fragments = tuple(
            ContextFragment.from_content(
                fragment_id=(
                    f"observation:{observation.step}:{observation.tool_name}:"
                    f"{observation.digest[:12]}"
                ),
                fragment_type="observation",
                source="caller:observation",
                version="1",
                trust_level="UNTRUSTED",
                priority=700 + observation.step,
                content=observation.summary,
                required=True,
            )
            for observation in observations
        )
        all_fragments = (
            prompt,
            task,
            goal_fragment,
            *tool_fragments,
            *selected_skill_fragments,
            *skill_catalog_fragments,
            *reference_fragments,
            *observation_fragments,
        )
        return (
            all_fragments,
            (prompt, task, goal_fragment, *tool_fragments),
            tool_fragments,
            observation_fragments,
        )

    @staticmethod
    def _validate_fragment_ids(fragments: Sequence[ContextFragment]) -> None:
        ids = [fragment.fragment_id for fragment in fragments]
        if len(set(ids)) != len(ids):
            raise ContextBuildError("CONTEXT_INVALID", "context fragment ids must be unique")

    @staticmethod
    def _validate_optional_fragments(
        skill_catalog_fragments: Sequence[ContextFragment],
        reference_fragments: Sequence[ContextFragment],
    ) -> None:
        if any(fragment.required for fragment in skill_catalog_fragments):
            raise ContextBuildError(
                "CONTEXT_INVALID", "an unselected skill catalog fragment is mandatory"
            )
        if any(not fragment.triggered and fragment.required for fragment in reference_fragments):
            raise ContextBuildError(
                "CONTEXT_INVALID", "an untriggered reference fragment is mandatory"
            )


def record_provider_prompt_tokens(
    result: ContextBuildResult,
    prompt_tokens: int,
) -> ContextBuildResult:
    """Attach provider-reported prompt usage and reject over-budget responses."""
    if isinstance(prompt_tokens, bool) or prompt_tokens < 0:
        raise ContextBuildError("CONTEXT_INVALID", "provider prompt token usage is invalid")
    provenance = result.provenance.model_copy(update={"actual_prompt_tokens": prompt_tokens})
    updated = result.model_copy(update={"provenance": provenance})
    if prompt_tokens > result.input_budget:
        raise ContextBuildError(
            "CONTEXT_BUDGET",
            "provider prompt token usage exceeded the configured budget",
            result=updated,
        )
    return updated
