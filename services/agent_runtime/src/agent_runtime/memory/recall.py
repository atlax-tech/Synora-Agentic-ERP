"""Exact-scope, lifecycle-gated Memory recall contracts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal, Protocol

from pydantic import ValidationError, field_validator

from agent_runtime.memory.contracts import MemoryRecord, MemoryScope, StrictModel, is_recallable

MemoryRecallErrorCode = Literal["INVALID_QUERY", "STORE_FAILURE"]


class MemoryRecallError(ValueError):
    """Bounded recall-domain error without transport or storage details."""

    def __init__(self, code: MemoryRecallErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class MemoryRecallQuery(StrictModel):
    """Immutable exact scope and decision time for one recall operation."""

    scope: MemoryScope
    now: datetime

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value


def _validate_recall_query(value: object) -> MemoryRecallQuery:
    """Revalidate a query at the recall boundary, including model-construct bypasses."""

    if not isinstance(value, MemoryRecallQuery):
        raise MemoryRecallError("INVALID_QUERY", "recall query is invalid")
    try:
        return MemoryRecallQuery(scope=value.scope, now=value.now)
    except (ValidationError, TypeError, ValueError) as exc:
        raise MemoryRecallError("INVALID_QUERY", "recall query is invalid") from exc


def filter_recallable(
    records: Iterable[MemoryRecord], query: MemoryRecallQuery
) -> tuple[MemoryRecord, ...]:
    """Return exact-scope eligible records in created-time/ID order.

    Ordering is deterministic only; this function does not perform relevance
    ranking or content search.  Eligibility is delegated to ``is_recallable``.
    """

    validated_query = _validate_recall_query(query)
    visible: list[MemoryRecord] = []
    for record in records:
        if not isinstance(record, MemoryRecord):
            raise MemoryRecallError("INVALID_QUERY", "memory record is invalid")
        if is_recallable(record, validated_query.scope, now=validated_query.now):
            visible.append(record)
    return tuple(sorted(visible, key=lambda record: (record.created_at, record.memory_id or "")))


class MemoryRecallPort(Protocol):
    """Narrow read boundary separate from durable Memory mutations."""

    async def recall_exact(self, query: MemoryRecallQuery) -> tuple[MemoryRecord, ...]:
        """Return only lifecycle- and exact-scope-eligible durable records."""


__all__ = [
    "MemoryRecallError",
    "MemoryRecallErrorCode",
    "MemoryRecallPort",
    "MemoryRecallQuery",
    "filter_recallable",
]
