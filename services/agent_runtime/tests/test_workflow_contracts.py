"""P5-G01..G10 typed workflow contract and handwritten-engine checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime.agent.contracts import observation_from_summary
from agent_runtime.workflow import (
    ClarificationRequest,
    PlanStep,
    WorkflowEngine,
    WorkflowError,
    WorkflowState,
)
from agent_runtime.workflow.checkpoint import (
    CheckpointConflict,
    CheckpointIncompatible,
    SQLiteCheckpointStore,
)
from agent_runtime.workflow.engine import WorkflowClock
from agent_runtime.workflow.runtime import (
    WorkflowResumeRequest,
    WorkflowRuntime,
    WorkflowStatusRequest,
)
from pydantic import ValidationError


def _deadline(hours: int = 1) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _step(step_id: str, order: int, *, depends_on: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        order=order,
        type="TOOL",
        depends_on=depends_on,
        allowed_tools=("stock.projected",),
        tool_name="stock.projected",
        parameters={"item_code": "ITEM-1001"},
    )


def _state(*steps: PlanStep) -> WorkflowState:
    return WorkflowEngine.create_state(
        run_id=uuid4(),
        trace_id=uuid4(),
        steps=tuple(steps),
        deadline=_deadline(),
    )


def test_valid_dag_uses_stable_dependency_order() -> None:
    engine = WorkflowEngine()
    state = _state(_step("first", 1), _step("second", 2, depends_on=("first",)))
    running = engine.start(state)
    ready = engine.ready_step(running)
    assert ready is not None
    assert ready.step_id == "first"
    running = engine.begin_step(running, "first")
    observation = observation_from_summary(
        run_id=running.run_id,
        step=1,
        tool_name="stock.projected",
        ok=True,
        summary="recorded stock",
    )
    running = engine.complete_step(running, observation)
    ready = engine.ready_step(running)
    assert ready is not None
    assert ready.step_id == "second"


@pytest.mark.parametrize(
    "steps",
    [
        (_step("same", 1), _step("same", 2)),
        (_step("first", 1, depends_on=("missing",)),),
        (
            _step("first", 1),
            _step("second", 2, depends_on=("third",)),
            _step("third", 3, depends_on=("second",)),
        ),
        (_step("first", 2),),
    ],
)
def test_invalid_dag_fails_closed(steps: tuple[PlanStep, ...]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        _state(*steps)


def test_extra_fields_and_unknown_tool_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PlanStep.model_validate({"step_id": "one", "order": 1, "type": "TOOL", "extra": 1})
    with pytest.raises(ValidationError):
        PlanStep(
            step_id="one",
            order=1,
            type="TOOL",
            allowed_tools=("item.lookup",),
            tool_name="stock.projected",
        )


def test_clarification_interrupt_resume_is_single_use() -> None:
    engine = WorkflowEngine()
    state = engine.begin_step(engine.start(_state(_step("first", 1))), "first")
    interrupt = ClarificationRequest(
        interrupt_id=uuid4(),
        question="Which warehouse?",
        answer_type="TEXT",
        answer_max_length=40,
    )
    interrupted = engine.interrupt(state, interrupt)
    assert interrupted.status == "INTERRUPTED"
    resumed = engine.resume(interrupted, interrupt_id=interrupt.interrupt_id, answer="Stores")
    assert resumed.status == "RUNNING"
    assert resumed.replan_reason == "INPUT_CLARIFIED"
    with pytest.raises(WorkflowError, match="not waiting"):
        engine.resume(resumed, interrupt_id=interrupt.interrupt_id, answer="Stores")


def test_replan_preserves_completed_step_and_increments_version() -> None:
    engine = WorkflowEngine()
    state = engine.begin_step(engine.start(_state(_step("first", 1))), "first")
    observation = observation_from_summary(
        run_id=state.run_id,
        step=1,
        tool_name="stock.projected",
        ok=True,
        summary="recorded stock",
    )
    completed = engine.complete_step(state, observation)
    replacement = (
        completed.steps[0],
        _step("second", 2, depends_on=("first",)),
    )
    replanned = engine.replan(completed, replacement, "TOOL_ERROR")
    assert replanned.plan_version == completed.plan_version + 1
    assert replanned.steps[0] == completed.steps[0]
    changed = (
        completed.steps[0].model_copy(update={"parameters": {"item_code": "OTHER"}}),
        replacement[1],
    )
    with pytest.raises(WorkflowError, match="completed steps"):
        engine.replan(completed, changed, "STATE_DRIFT")


def test_replan_cannot_mark_an_unstarted_step_completed() -> None:
    engine = WorkflowEngine()
    state = engine.start(_state(_step("first", 1), _step("second", 2, depends_on=("first",))))
    replacement = (
        state.steps[0],
        state.steps[1].model_copy(
            update={
                "status": "SUCCEEDED",
                "observation_digest": "a" * 64,
                "completed_at": _deadline(),
            }
        ),
    )
    with pytest.raises(WorkflowError, match="unstarted steps"):
        engine.replan(state, replacement, "TOOL_ERROR")


def test_checkpoint_round_trip_cas_lease_and_no_secret(tmp_path: Path) -> None:
    store = SQLiteCheckpointStore(tmp_path / "workflow.sqlite")
    state = _state(_step("first", 1))
    store.create(state)
    loaded = store.load(state.run_id)
    assert loaded == state
    lease = store.acquire_lease(state.run_id, expected_revision=0)
    started = WorkflowEngine().start(loaded)
    store.save(started, expected_revision=0, lease_id=lease)
    with pytest.raises(CheckpointConflict):
        store.save(started, expected_revision=0)
    store.release_lease(state.run_id, lease)
    assert "capability" not in (tmp_path / "workflow.sqlite").read_text(errors="ignore")
    assert (tmp_path / "workflow.sqlite").stat().st_mode & 0o777 == 0o600


def test_checkpoint_unknown_version_fails_closed(tmp_path: Path) -> None:
    store = SQLiteCheckpointStore(tmp_path / "workflow.sqlite")
    state = _state(_step("first", 1))
    store.create(state)
    import sqlite3

    with sqlite3.connect(tmp_path / "workflow.sqlite") as connection:
        connection.execute(
            "UPDATE workflow_checkpoints SET schema_version = '99' WHERE run_id = ?",
            (str(state.run_id),),
        )
    with pytest.raises(CheckpointIncompatible):
        store.load(state.run_id)


def test_checkpoint_requires_explicit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNORA_WORKFLOW_DB_PATH", raising=False)
    with pytest.raises(Exception, match="SYNORA_WORKFLOW_DB_PATH"):
        SQLiteCheckpointStore.from_environment()


def test_terminal_cancel_and_expiry_are_monotonic() -> None:
    engine = WorkflowEngine()
    state = engine.start(_state(_step("first", 1)))
    cancelled = engine.cancel(state)
    assert cancelled.status == "CANCELLED"
    assert engine.expire(cancelled) == cancelled


def test_status_persists_expiry_at_the_deadline(tmp_path: Path) -> None:
    current = datetime.now(UTC)
    store = SQLiteCheckpointStore(tmp_path / "workflow.sqlite", clock=lambda: current)
    state = WorkflowEngine.create_state(
        run_id=uuid4(),
        trace_id=uuid4(),
        steps=(_step("first", 1),),
        deadline=(current + timedelta(seconds=1)).isoformat(),
    )
    store.create(state)
    runtime = WorkflowRuntime(store)
    runtime.engine = WorkflowEngine(clock=WorkflowClock(lambda: current + timedelta(seconds=1)))
    result = runtime.status(WorkflowStatusRequest(run_id=state.run_id))
    assert result.result.state.status == "EXPIRED"
    assert store.load(state.run_id).status == "EXPIRED"


def test_recover_marks_orphaned_running_step_as_manual_recovery(tmp_path: Path) -> None:
    store = SQLiteCheckpointStore(tmp_path / "workflow.sqlite")
    state = _state(_step("first", 1))
    store.create(state)
    lease = store.acquire_lease(state.run_id, expected_revision=state.revision)
    started = WorkflowEngine().start(state)
    store.save(started, expected_revision=state.revision, lease_id=lease)
    store.release_lease(state.run_id, lease)
    lease = store.acquire_lease(started.run_id, expected_revision=started.revision)
    running = WorkflowEngine().begin_step(started, "first")
    store.save(running, expected_revision=started.revision, lease_id=lease)
    store.release_lease(running.run_id, lease)

    runtime = WorkflowRuntime(store)
    import asyncio

    assert asyncio.run(runtime.recover()) == 1
    recovered = store.load(running.run_id)
    assert recovered.status == "INTERRUPTED"
    assert recovered.crash_recovered is True
    assert recovered.replan_reason == "STATE_DRIFT"


def test_manual_recovery_never_converts_uncertain_tool_to_success(tmp_path: Path) -> None:
    store = SQLiteCheckpointStore(tmp_path / "workflow.sqlite")
    state = _state(_step("first", 1))
    store.create(state)
    lease = store.acquire_lease(state.run_id, expected_revision=state.revision)
    started = WorkflowEngine().start(state)
    store.save(started, expected_revision=state.revision, lease_id=lease)
    store.release_lease(state.run_id, lease)
    lease = store.acquire_lease(started.run_id, expected_revision=started.revision)
    running = WorkflowEngine().begin_step(started, "first")
    store.save(running, expected_revision=started.revision, lease_id=lease)
    store.release_lease(running.run_id, lease)

    runtime = WorkflowRuntime(store)
    import asyncio

    assert asyncio.run(runtime.recover()) == 1
    interrupted = store.load(running.run_id)
    assert interrupted.clarification is not None
    failed = asyncio.run(
        runtime.resume(
            WorkflowResumeRequest(
                run_id=running.run_id,
                correlation_id=uuid4(),
                capability="A" * 43,
                workflow_revision=interrupted.revision,
                interrupt_id=interrupted.clarification.interrupt_id,
                answer="inspect",
            )
        )
    )
    assert failed.result.state.status == "FAILED"
    assert failed.result.state.steps[0].status == "FAILED"
    assert failed.result.state.steps[0].observation_digest is None
    assert "MANUAL_RECOVERY_REQUIRED" in (failed.result.state.stop_reason or "")
