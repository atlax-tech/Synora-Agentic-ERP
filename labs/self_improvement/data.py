"""Historical failure admission and deterministic grouped dataset creation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from .contracts import (
    CaseKind,
    DatasetCase,
    DatasetManifest,
    ReviewedCase,
    SplitName,
    digest_bytes,
    digest_json,
)

DEFAULT_HISTORICAL_SOURCES = (
    "output/phase11/phase11-live-dom-glm-2bb2aa2.json",
    "output/phase11/phase11-live-aria-glm-2bb2aa2.json",
    "output/phase11/phase11-live-aria-failure-925b96a.json",
    "output/phase11/phase11-live-hybrid-glm-4067f2d.json",
)
_SECRET = re.compile(
    r"(?i)([\"']?(authorization|cookie|api[_-]?key|secret)[\"']?\s*[:=]|bearer\s+[a-z0-9._-]+)"
)
_STATUS_CODES = {
    "TRANSPORT_ERROR": "TOOL_UNKNOWN",
    "ACTION_REJECTED": "TOOL_UNKNOWN",
    "RESPONSE_SCHEMA": "UNTRUSTED_INJECTION",
    "RESPONSE_CONTENT_MISSING": "TOOL_UNKNOWN",
    "VISION_PROVIDER_UNAVAILABLE": "TOOL_UNKNOWN",
}


def _classify(payload: dict[str, object]) -> tuple[CaseKind, str, str]:
    code = str(payload.get("failure_code") or payload.get("stop_reason") or "UNKNOWN")
    if code in {"RESPONSE_SCHEMA", "MODEL_RESPONSE_SCHEMA"}:
        return "UNTRUSTED_INJECTION", "FINISH", code
    return _STATUS_CODES.get(code, "TOOL_UNKNOWN"), "ASK_INPUT", code


def _safe_text(payload: dict[str, object]) -> str:
    mode = str(payload.get("mode") or payload.get("text_role") or "unknown")
    status = str(payload.get("status") or payload.get("observed_status") or "FAILED")
    return (
        f"Investigate a procurement read-only task in {mode} mode; preserve unknowns and "
        f"stop safely. observed_status={status}"
    )


def audit_historical_failures(
    root: Path,
    sources: Iterable[str] = DEFAULT_HISTORICAL_SOURCES,
) -> tuple[ReviewedCase, ...]:
    """Read only an explicit source list and return auditable, redacted records."""
    result: list[ReviewedCase] = []
    seen: set[str] = set()
    for relative in sources:
        path = root / relative
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("historical source must be a relative allowlisted path")
        if relative in seen:
            raise ValueError("duplicate historical source")
        seen.add(relative)
        if not path.is_file() or path.is_symlink():
            result.append(
                ReviewedCase(
                    case_id="phase12-missing-source-" + digest_bytes(relative.encode())[:12],
                    source_path=relative,
                    source_sha256="0" * 64,
                    source_kind="HISTORICAL_FAILURE",
                    group_id="phase12-historical-missing",
                    kind="TOOL_UNKNOWN",
                    review_status="REJECTED",
                    input_text="missing historical artifact",
                    expected_action="ASK_INPUT",
                    expected_status="REJECTED",
                    failure_code="SOURCE_MISSING",
                    review_reason="source is missing or is a symlink",
                )
            )
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        if _SECRET.search(text):
            result.append(
                ReviewedCase(
                    case_id="phase12-secret-source-" + digest_bytes(raw)[:12],
                    source_path=relative,
                    source_sha256=digest_bytes(raw),
                    source_kind="HISTORICAL_FAILURE",
                    group_id="phase12-historical-secret",
                    kind="TOOL_UNKNOWN",
                    review_status="REJECTED",
                    input_text="source rejected before export",
                    expected_action="ASK_INPUT",
                    expected_status="REJECTED",
                    failure_code="SECRET_PATTERN",
                    review_reason="source contains a secret-like field and cannot be exported",
                )
            )
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"historical source is not JSON: {relative}") from error
        if not isinstance(payload, dict):
            raise ValueError(f"historical source must contain a JSON object: {relative}")
        kind, action, code = _classify(payload)
        case_id = "phase12-historical-" + digest_bytes(raw)[:16]
        if case_id in {case.case_id for case in result}:
            raise ValueError("historical case id collision")
        result.append(
            ReviewedCase(
                case_id=case_id,
                source_path=relative,
                source_sha256=digest_bytes(raw),
                source_kind="HISTORICAL_FAILURE",
                group_id="phase12-historical-" + digest_bytes(relative.encode())[:12],
                kind=kind,
                review_status="BACKGROUND_ONLY",
                input_text=_safe_text(payload),
                expected_action=action,
                expected_status="FAILED",
                failure_code=code,
                review_reason=(
                    "real Phase 11 evidence is retained as redacted background; it is not "
                    "sent to a model"
                ),
            )
        )
    return tuple(result)


_KINDS: tuple[CaseKind, ...] = (
    "COMPLETE_READ",
    "MISSING_INPUT",
    "DUPLICATE_NO_PROGRESS",
    "TOOL_UNKNOWN",
    "STALE_CONFLICT",
    "UNTRUSTED_INJECTION",
)


def _synthetic_case(kind: CaseKind, group_number: int, variant: int) -> DatasetCase:
    group_id = f"phase12-{kind.lower().replace('_', '-')}-{group_number:02d}"
    case_id = f"{group_id}-v{variant}"
    actions = {
        "COMPLETE_READ": ("purchase_order.open", "SUCCEEDED"),
        "MISSING_INPUT": ("ASK_INPUT", "NEEDS_INPUT"),
        "DUPLICATE_NO_PROGRESS": ("FINISH", "NO_PROGRESS"),
        "TOOL_UNKNOWN": ("ASK_INPUT", "UNKNOWN"),
        "STALE_CONFLICT": ("ASK_INPUT", "CONFLICT"),
        "UNTRUSTED_INJECTION": ("FINISH", "REFUSED"),
    }
    action, status = actions[kind]
    input_text = (
        f"Synthetic procurement case group {group_number}, variant {variant}. "
        f"Scenario={kind}. Use only bounded read-only observations and preserve evidence."
    )
    oracle = {"scenario": kind, "variant": str(variant)}
    return DatasetCase(
        case_id=case_id,
        group_id=group_id,
        split="train",
        kind=kind,
        source_kind="SYNTHETIC",
        input_text=input_text,
        expected_action=action,
        expected_status=status,
        oracle=oracle,
    )


def build_synthetic_manifest(code_version: str, seed: int = 12) -> DatasetManifest:
    """Build 60 groups x 2 variants with deterministic 6/2/2 group splits."""
    cases: list[DatasetCase] = []
    for kind in _KINDS:
        for group_number in range(10):
            split: SplitName = (
                "train" if group_number < 6 else "dev" if group_number < 8 else "test"
            )
            for variant in (1, 2):
                case = _synthetic_case(kind, group_number, variant).model_copy(
                    update={"split": split}
                )
                cases.append(case)
    case_digests = {case.case_id: digest_json(case.model_dump(mode="json")) for case in cases}
    dataset_payload = {
        "code_version": code_version,
        "seed": seed,
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    split_counts = {
        split: sum(case.split == split for case in cases) for split in ("train", "dev", "test")
    }
    group_counts = {
        split: len({case.group_id for case in cases if case.split == split})
        for split in ("train", "dev", "test")
    }
    return DatasetManifest(
        dataset_id="phase12-synthetic-v1",
        code_version=code_version,
        seed=seed,
        case_count=len(cases),
        split_counts=split_counts,
        group_counts=group_counts,
        case_digests=case_digests,
        dataset_digest=digest_json(dataset_payload),
        cases=tuple(cases),
    )


def validate_grouped_splits(cases: Iterable[DatasetCase]) -> None:
    groups: dict[str, set[SplitName]] = {}
    ids: set[str] = set()
    for case in cases:
        if case.case_id in ids:
            raise ValueError("duplicate case id")
        ids.add(case.case_id)
        groups.setdefault(case.group_id, set()).add(case.split)
    leaked = {group: splits for group, splits in groups.items() if len(splits) != 1}
    if leaked:
        raise ValueError(f"groups cross splits: {sorted(leaked)}")
