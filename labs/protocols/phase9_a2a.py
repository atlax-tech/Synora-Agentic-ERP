"""P9.7 localhost A2A Policy/Risk Reviewer laboratory.

The server is intentionally a deterministic protocol fixture.  It accepts one
typed JSON reviewer payload, binds the result to the A2A task/context envelope,
and exposes no ERP, provider, persistence, or outbound-network capability.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Final, Literal, cast
from urllib.parse import urlsplit

from a2a.helpers import get_message_text, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    CancelTaskRequest,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_ENDPOINT: Final = "http://127.0.0.1:8029/a2a"
SCHEMA_VERSION: Final = "1"
MAX_STORED_TASKS: Final = 32
HANDLER_EXCEPTION_SENTINEL: Final = "__phase9_handler_exception__"
_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PolicyRiskReviewRequest(_StrictWireModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    plan_digest: _DIGEST
    candidate_explanation: str = Field(min_length=1, max_length=4_000)
    unknowns: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("candidate_explanation")
    @classmethod
    def validate_candidate(cls, value: str) -> str:
        if _CONTROL_CHARS.search(value):
            raise ValueError("candidate_explanation contains a control character")
        return value

    @field_validator("unknowns")
    @classmethod
    def validate_unknowns(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 200 or _CONTROL_CHARS.search(item) for item in value):
            raise ValueError("unknowns must be bounded safe text")
        return value


class PolicyRiskReviewResponse(_StrictWireModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    task_id: str = Field(min_length=1, max_length=200)
    context_id: str = Field(min_length=1, max_length=200)
    reviewed_plan_digest: _DIGEST
    decision: Literal["ACCEPT", "REVISE"]
    issue_codes: list[Literal["MISSING_FACTS"]] = Field(default_factory=list, max_length=1)
    feedback: str = Field(default="", max_length=500)


def _assert_loopback_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("A2A endpoint must be HTTP loopback")
    if parsed.query or parsed.fragment or not parsed.path:
        raise ValueError("A2A endpoint must not contain query or fragment")
    return parsed.path


class BoundedInMemoryTaskStore(TaskStore):
    """Bounded, process-local task store; all data is lost on restart."""

    def __init__(self, max_tasks: int = MAX_STORED_TASKS) -> None:
        if max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        self._delegate = InMemoryTaskStore()
        self._max_tasks = max_tasks
        self._known_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def save(self, task: Task, context: ServerCallContext) -> None:
        async with self._lock:
            if task.id not in self._known_ids and len(self._known_ids) >= self._max_tasks:
                raise RuntimeError("bounded LAB_ONLY task store is full")
            self._known_ids.add(task.id)
        await self._delegate.save(task, context)

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        return await self._delegate.get(task_id, context)

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        return await self._delegate.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._delegate.delete(task_id, context)
        async with self._lock:
            self._known_ids.discard(task_id)


class PolicyRiskReviewerExecutor(AgentExecutor):
    """Deterministic reviewer with a small cancellation window for the lab."""

    def __init__(self, work_delay_seconds: float = 0.02) -> None:
        if not 0 <= work_delay_seconds <= 1:
            raise ValueError("work_delay_seconds must be between 0 and 1")
        self._work_delay_seconds = work_delay_seconds
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancelled: set[str] = set()
        self._terminal_locks: dict[str, asyncio.Lock] = {}
        self._terminal_states: dict[str, Literal["completed", "canceled"]] = {}
        self._lock = asyncio.Lock()

    async def _event_for(self, task_id: str) -> asyncio.Event:
        async with self._lock:
            event = self._cancel_events.get(task_id)
            if event is None:
                event = asyncio.Event()
                self._cancel_events[task_id] = event
            return event

    async def _terminal_lock_for(self, task_id: str) -> asyncio.Lock:
        async with self._lock:
            lock = self._terminal_locks.get(task_id)
            if lock is None:
                lock = asyncio.Lock()
                self._terminal_locks[task_id] = lock
            return lock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id or context.message is None:
            raise ValueError("A2A task and context bindings are required")

        cancel_event = await self._event_for(task_id)
        updater = TaskUpdater(event_queue, task_id, context_id)
        try:
            await event_queue.enqueue_event(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message],
                )
            )
            await updater.start_work()
            await asyncio.sleep(self._work_delay_seconds)
            if cancel_event.is_set():
                return

            raw_payload = get_message_text(context.message)
            if not raw_payload:
                raise ValueError("reviewer payload must be one JSON text part")
            payload = PolicyRiskReviewRequest.model_validate(json.loads(raw_payload))
            if cancel_event.is_set():
                return
            if payload.candidate_explanation == HANDLER_EXCEPTION_SENTINEL:
                raise RuntimeError("phase9 handler exception probe")

            response = PolicyRiskReviewResponse(
                task_id=task_id,
                context_id=context_id,
                reviewed_plan_digest=payload.plan_digest,
                decision="REVISE" if payload.unknowns else "ACCEPT",
                issue_codes=["MISSING_FACTS"] if payload.unknowns else [],
                feedback="Provide the missing facts before relying on this explanation."
                if payload.unknowns
                else "Typed policy and risk review accepted.",
            )
            terminal_lock = await self._terminal_lock_for(task_id)
            async with terminal_lock:
                async with self._lock:
                    if self._terminal_states.get(task_id) is not None:
                        return
                if cancel_event.is_set():
                    return
                await updater.add_artifact(
                    parts=[
                        new_text_part(
                            response.model_dump_json(),
                            media_type="application/json",
                        )
                    ],
                    artifact_id="policy-risk-review.v1",
                    name="policy-risk-review.v1",
                    last_chunk=True,
                )
                if cancel_event.is_set():
                    return
                await updater.complete()
                async with self._lock:
                    self._terminal_states[task_id] = "completed"
        finally:
            async with self._lock:
                self._cancel_events.pop(task_id, None)
                self._cancelled.discard(task_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise ValueError("A2A cancellation requires task and context bindings")

        terminal_lock = await self._terminal_lock_for(task_id)
        async with terminal_lock:
            async with self._lock:
                terminal_state = self._terminal_states.get(task_id)
                if terminal_state == "completed":
                    raise RuntimeError("A2A task already completed")
                if terminal_state == "canceled" or task_id in self._cancelled:
                    return
                self._cancelled.add(task_id)
                self._terminal_states[task_id] = "canceled"
                event = self._cancel_events.setdefault(task_id, asyncio.Event())
                event.set()
            await TaskUpdater(event_queue, task_id, context_id).cancel()


class IdempotentCancelRequestHandler(DefaultRequestHandler):
    """Preserve a canceled task on repeated cancel requests."""

    async def on_message_send(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> Task | Message:
        """Reject a continuation whose task and context IDs disagree."""

        task_id = params.message.task_id
        context_id = params.message.context_id
        if task_id and context_id:
            task = await self.task_store.get(task_id, context)
            if task is not None and task.context_id != context_id:
                raise InvalidParamsError("task and context IDs do not match")
        return cast("Task | Message", await super().on_message_send(params, context))

    async def on_cancel_task(
        self, params: CancelTaskRequest, context: ServerCallContext
    ) -> Task | None:
        task = await self.task_store.get(params.id, context)
        if task is None:
            raise TaskNotFoundError
        if task.status.state == TaskState.TASK_STATE_CANCELED:
            return task
        return cast("Task | None", await super().on_cancel_task(params, context))


def build_agent_card(endpoint: str = DEFAULT_ENDPOINT) -> AgentCard:
    _assert_loopback_endpoint(endpoint)
    return AgentCard(
        name="Synora Policy/Risk Reviewer",
        description="LAB_ONLY typed policy and procurement-risk review agent",
        supported_interfaces=[
            AgentInterface(
                url=endpoint,
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=PROTOCOL_VERSION_1_0,
            )
        ],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="policy-risk-review",
                name="Policy/Risk Review",
                description="Review a typed candidate explanation without changing ERP state.",
                tags=["policy", "risk", "read-only"],
                examples=["Review this typed procurement explanation."],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        ],
    )


def build_app(endpoint: str = DEFAULT_ENDPOINT, *, work_delay_seconds: float = 0.02) -> FastAPI:
    """Build the loopback-only A2A ASGI application."""

    rpc_path = _assert_loopback_endpoint(endpoint)
    executor = PolicyRiskReviewerExecutor(work_delay_seconds=work_delay_seconds)
    task_store = BoundedInMemoryTaskStore()
    card = build_agent_card(endpoint)
    handler = IdempotentCancelRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=card,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await handler.aclose()

    app = FastAPI(
        title="Synora Phase 9 A2A LAB_ONLY",
        version=SCHEMA_VERSION,
        lifespan=lifespan,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=rpc_path),
    )
    app.state.phase9_handler = handler
    app.state.phase9_task_store = task_store
    app.state.phase9_agent_card = card
    return app
