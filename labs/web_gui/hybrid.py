"""Explicit DOM/ARIA/screenshot hybrid observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from labs.web_gui.browser import (
    BrowserPolicyError,
    _fields,
    _locator_for,
    _origin,
    _playwright_sync,
    _terminal_page_state,
)
from labs.web_gui.contracts import (
    ActionProposal,
    ActionReceipt,
    Observation,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from labs.web_gui.fixtures import fixture_fields
from labs.web_gui.model import LiveVisionModel, ModelDecision, decision_from_vision
from labs.web_gui.recovery import (
    ProgressGuard,
    RecoveryFailure,
    remaining_timeout_ms,
    run_with_deadline,
    wait_for_ready,
)
from labs.web_gui.security import BrowserSecurityPolicy


@dataclass(frozen=True)
class HybridFrame:
    observation: Observation
    dom_text: str
    aria_text: str
    screenshot: bytes
    targets: frozenset[str]


@dataclass(frozen=True)
class HybridDecision:
    proposal: ActionProposal
    fields: dict[str, str | None] | None = None
    visual_fields: dict[str, str | None] | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class HybridRun:
    result: TaskResult
    frames: tuple[HybridFrame, ...]
    security_violations: tuple[str, ...] = ()
    model_calls: int = 0
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


HybridDecider = Callable[[HybridFrame, TaskSpec], HybridDecision]


def _hybrid_frame(page: Any, spec: TaskSpec, *, timeout_ms: int | None = None) -> HybridFrame:
    deadline = monotonic() + (
        (timeout_ms if timeout_ms is not None else spec.budget.action_timeout_seconds * 1000) / 1000
    )

    def operation_timeout() -> int:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RecoveryFailure("WALL_TIME_BUDGET")
        return max(1, int(remaining * 1000))

    body = page.locator("body")
    try:
        dom_text = body.inner_text(timeout=operation_timeout())
        aria_text = body.aria_snapshot(timeout=operation_timeout())
        screenshot = page.screenshot(type="png", animations="disabled", timeout=operation_timeout())
    except Exception as error:
        if type(error).__name__ == "TimeoutError":
            raise RecoveryFailure("OBSERVATION_TIMEOUT") from error
        raise
    if not isinstance(aria_text, str) or not isinstance(dom_text, str):
        raise BrowserPolicyError("hybrid observation is unavailable")
    if len(dom_text) > 50_000 or len(aria_text) > 50_000 or len(screenshot) > 2 * 1024 * 1024:
        raise BrowserPolicyError("hybrid observation is too large")
    if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BrowserPolicyError("hybrid screenshot is invalid")
    digest = hashlib.sha256(
        screenshot + dom_text.encode("utf-8") + aria_text.encode("utf-8")
    ).hexdigest()
    targets: set[str] = set()
    if page.locator("#order-search").count() == 1:
        targets.add("search-input")
    if page.locator('[data-action="search"]').count() == 1:
        targets.add("search-submit")
    if page.locator('[data-action="back"]').count() == 1:
        targets.add("back")
    for name in page.locator("[data-order-name], [data-order-id]").evaluate_all(
        "elements => elements.map(element => "
        "element.getAttribute('data-order-name') || element.getAttribute('data-order-id'))"
    ):
        if isinstance(name, str) and name:
            targets.add(f"order:{name}")
    content = json.dumps(
        {
            "dom_text": dom_text,
            "aria": aria_text,
            "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
            "page_version": digest,
            "targets": sorted(targets),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    viewport = page.viewport_size or {}
    width = viewport.get("width")
    height = viewport.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise BrowserPolicyError("hybrid viewport is unavailable")
    return HybridFrame(
        observation=Observation(
            page_version=f"hybrid:{digest}",
            source=spec.data_source,
            mode="hybrid",
            content=content,
            screenshot_sha256=hashlib.sha256(screenshot).hexdigest(),
            viewport_width=width,
            viewport_height=height,
        ),
        dom_text=dom_text,
        aria_text=aria_text,
        screenshot=screenshot,
        targets=frozenset(targets),
    )


def _validate_hybrid_action(decision: HybridDecision, frame: HybridFrame) -> None:
    proposal = decision.proposal
    if proposal.observation_id != frame.observation.observation_id:
        raise BrowserPolicyError("action references a stale hybrid observation")
    if proposal.action_type in {"search", "click"}:
        if proposal.target_ref not in frame.targets:
            raise BrowserPolicyError("hybrid action target was not observed")
        if proposal.x is not None or proposal.y is not None:
            raise BrowserPolicyError("hybrid actions cannot use coordinates")
        if proposal.action_type == "search" and not proposal.text:
            raise BrowserPolicyError("hybrid search requires text")
        return
    if proposal.action_type in {"scroll", "wait", "finish"}:
        if proposal.target_ref is not None or proposal.text is not None:
            raise BrowserPolicyError("hybrid action contains an unexpected target")
        return
    raise BrowserPolicyError("hybrid action type is not allowed")


def _safe_fields(values: dict[str, str | None] | None) -> dict[str, str | None]:
    fields = values or {}
    allowed = {"purchase_order", "supplier", "status", "currency"}
    if set(fields) - allowed or any(
        value is not None and (not isinstance(value, str) or len(value) > 140)
        for value in fields.values()
    ):
        raise BrowserPolicyError("hybrid answer contains invalid fields")
    return {field: fields.get(field) for field in sorted(allowed)}


def _model_output_size(decision: HybridDecision) -> int:
    payload = {
        "proposal": decision.proposal.model_dump(mode="json"),
        "fields": decision.fields,
        "visual_fields": decision.visual_fields,
    }
    return len(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _rejected_receipt(
    proposal: ActionProposal, observation: Observation, error_code: str, reason: str
) -> ActionReceipt:
    return ActionReceipt(
        action_id=proposal.action_id,
        observation_id=proposal.observation_id,
        result="REJECTED",
        error_code=error_code,
        before_observation_id=observation.observation_id,
        stop_reason=reason,
    )


def run_hybrid_task(base_url: str, spec: TaskSpec, decider: HybridDecider) -> HybridRun:
    """Run a hybrid task where every decision sees one synchronized frame."""

    if spec.mode != "hybrid":
        raise ValueError("run_hybrid_task requires a hybrid TaskSpec")
    if spec.data_source != "synthetic":
        raise BrowserPolicyError(
            "generic hybrid runner accepts synthetic data only; use redacted ERP runner"
        )
    origin = _origin(base_url)
    started = monotonic()
    sync_playwright = _playwright_sync()
    frames: list[HybridFrame] = []
    receipts: list[ActionReceipt] = []
    status: TaskStatus = "INCOMPLETE"
    fields: dict[str, str | None] = {}
    stop_reason: str | None = "hybrid_decider_stopped"
    model_calls = 0
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    progress = ProgressGuard(max_actions=spec.budget.max_actions)
    unchanged_reobservations = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        policy = BrowserSecurityPolicy(origin=origin)
        context = browser.new_context(
            service_workers="block",
            accept_downloads=False,
            viewport={"width": 1024, "height": 768},
        )

        def route_handler(route: Any) -> None:
            request = route.request
            if policy.permits(request.url, request.method):
                route.continue_()
            else:
                route.abort(error_code="blockedbyclient")

        context.route("**/*", route_handler)
        page = context.new_page()
        browser_events: list[str] = []

        def on_popup(popup: Any) -> None:
            browser_events.append("POPUP_BLOCKED")
            popup.close()

        def on_download(download: Any) -> None:
            browser_events.append("DOWNLOAD_BLOCKED")
            download.cancel()

        def on_dialog(dialog: Any) -> None:
            browser_events.append("DIALOG_DISMISSED")
            dialog.dismiss()

        context.on("page", on_popup)
        page.on("download", on_download)
        page.on("dialog", on_dialog)
        try:
            frame: HybridFrame | None = None
            page.goto(
                f"{origin}/?scenario={spec.scenario}",
                wait_until="domcontentloaded",
                timeout=int(spec.budget.action_timeout_seconds * 1000),
            )
            terminal = _terminal_page_state(page)
            if terminal is not None:
                status, stop_reason = terminal
            else:
                try:
                    wait_for_ready(
                        page,
                        timeout_ms=remaining_timeout_ms(
                            started,
                            wall_time_seconds=spec.budget.wall_time_seconds,
                            action_timeout_seconds=spec.budget.action_timeout_seconds,
                        ),
                        scenario=spec.scenario,
                    )
                    frame = _hybrid_frame(
                        page,
                        spec,
                        timeout_ms=remaining_timeout_ms(
                            started,
                            wall_time_seconds=spec.budget.wall_time_seconds,
                            action_timeout_seconds=spec.budget.action_timeout_seconds,
                        ),
                    )
                except RecoveryFailure as failure:
                    status = (
                        "BUDGET_EXCEEDED"
                        if failure.code in {"OBSERVATION_TIMEOUT", "WALL_TIME_BUDGET"}
                        else "FAILED"
                    )
                    stop_reason = failure.code
                else:
                    assert frame is not None
                    frames.append(frame)
            for _ in range(spec.budget.max_actions):
                if frame is None:
                    break
                if monotonic() - started > spec.budget.wall_time_seconds:
                    status = "BUDGET_EXCEEDED"
                    stop_reason = "wall_time_budget"
                    break
                if model_calls >= spec.budget.max_model_calls:
                    status = "BUDGET_EXCEEDED"
                    stop_reason = "model_call_budget"
                    break
                remaining = spec.budget.wall_time_seconds - (monotonic() - started)
                if remaining <= 0:
                    status = "BUDGET_EXCEEDED"
                    stop_reason = "wall_time_budget"
                    break
                model_started = monotonic()
                model_calls += 1

                def invoke_hybrid(
                    current_frame: HybridFrame = frame,
                    current_spec: TaskSpec = spec,
                ) -> HybridDecision:
                    return decider(current_frame, current_spec)

                try:
                    decision = run_with_deadline(
                        invoke_hybrid,
                        min(spec.budget.model_timeout_seconds, remaining),
                    )
                except RecoveryFailure as failure:
                    status = (
                        "BLOCKED"
                        if failure.code
                        in {
                            "VISION_PROVIDER_UNAVAILABLE",
                            "TRANSPORT_ERROR",
                            "UPSTREAM_UNAVAILABLE",
                        }
                        else "BUDGET_EXCEEDED"
                        if failure.code == "MODEL_TIMEOUT"
                        else "FAILED"
                    )
                    stop_reason = (
                        "model_timeout" if failure.code == "MODEL_TIMEOUT" else failure.code
                    )
                    break
                model_elapsed = monotonic() - model_started
                if not isinstance(decision, HybridDecision):
                    status = "FAILED"
                    stop_reason = "invalid_model_decision"
                    break
                model = model or decision.model
                prompt_tokens = (
                    (prompt_tokens or 0) + decision.prompt_tokens
                    if decision.prompt_tokens is not None
                    else prompt_tokens
                )
                completion_tokens = (
                    (completion_tokens or 0) + decision.completion_tokens
                    if decision.completion_tokens is not None
                    else completion_tokens
                )
                if model_elapsed > spec.budget.model_timeout_seconds:
                    status = "BUDGET_EXCEEDED"
                    stop_reason = "model_timeout"
                    break
                if monotonic() - started > spec.budget.wall_time_seconds:
                    status = "BUDGET_EXCEEDED"
                    stop_reason = "wall_time_budget"
                    break
                if _model_output_size(decision) > spec.budget.max_output_tokens * 4:
                    status = "BUDGET_EXCEEDED"
                    stop_reason = "model_output_budget"
                    break
                proposal = decision.proposal
                try:
                    _validate_hybrid_action(decision, frame)
                    progress.record(
                        frame.observation.page_version,
                        json.dumps(
                            proposal.model_dump(
                                mode="json", exclude={"action_id", "observation_id"}
                            ),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                except (BrowserPolicyError, RecoveryFailure) as failure:
                    code = getattr(failure, "code", "ACTION_REJECTED")
                    receipts.append(_rejected_receipt(proposal, frame.observation, code, code))
                    status = "BUDGET_EXCEEDED" if code == "ACTION_BUDGET" else "FAILED"
                    stop_reason = code
                    break
                if proposal.action_type != "finish":
                    try:
                        current_frame = _hybrid_frame(
                            page,
                            spec,
                            timeout_ms=remaining_timeout_ms(
                                started,
                                wall_time_seconds=spec.budget.wall_time_seconds,
                                action_timeout_seconds=spec.budget.action_timeout_seconds,
                            ),
                        )
                    except RecoveryFailure as failure:
                        code = failure.code
                        receipts.append(_rejected_receipt(proposal, frame.observation, code, code))
                        status = (
                            "BUDGET_EXCEEDED"
                            if code in {"OBSERVATION_TIMEOUT", "WALL_TIME_BUDGET"}
                            else "FAILED"
                        )
                        stop_reason = code
                        break
                    except BrowserPolicyError:
                        receipts.append(
                            _rejected_receipt(
                                proposal,
                                frame.observation,
                                "STALE_OBSERVATION",
                                "stale_observation",
                            )
                        )
                        status = "FAILED"
                        stop_reason = "STALE_OBSERVATION"
                        break
                    if current_frame.observation.page_version != frame.observation.page_version:
                        frames.append(current_frame)
                        receipts.append(
                            _rejected_receipt(
                                proposal,
                                frame.observation,
                                "STALE_OBSERVATION",
                                "stale_observation",
                            )
                        )
                        status = "FAILED"
                        stop_reason = "STALE_OBSERVATION"
                        break
                if proposal.action_type == "finish":
                    try:
                        fields = _safe_fields(decision.fields)
                        visual_fields = _safe_fields(decision.visual_fields)
                    except BrowserPolicyError:
                        receipts.append(
                            _rejected_receipt(
                                proposal,
                                frame.observation,
                                "ANSWER_REJECTED",
                                "answer_rejected",
                            )
                        )
                        status = "FAILED"
                        stop_reason = "answer_rejected"
                        break
                    structural_fields = _safe_fields(_fields(page))
                    trusted = fixture_fields(spec.purchase_order)
                    if trusted is None:
                        status = "NOT_FOUND"
                        stop_reason = "fixture_not_found"
                    elif decision.visual_fields is None:
                        status = "OBSERVATION_CONFLICT"
                        stop_reason = "hybrid_visual_evidence_missing"
                    elif visual_fields != fields or visual_fields != structural_fields:
                        status = "OBSERVATION_CONFLICT"
                        stop_reason = "dom_and_visual_answers_conflict"
                    elif fields != trusted or structural_fields != trusted:
                        status = "INCOMPLETE"
                        stop_reason = "hybrid_trusted_fields_mismatch"
                    else:
                        status = "SUCCEEDED"
                        stop_reason = None
                    receipts.append(
                        ActionReceipt(
                            action_id=proposal.action_id,
                            observation_id=proposal.observation_id,
                            result="APPLIED",
                            before_observation_id=frame.observation.observation_id,
                            stop_reason=stop_reason,
                        )
                    )
                    break
                try:
                    if proposal.action_type == "search":
                        timeout_ms = int(spec.budget.action_timeout_seconds * 1000)
                        _locator_for(page, "search-input").fill(
                            proposal.text or "", timeout=timeout_ms
                        )
                        _locator_for(page, "search-submit").click(timeout=timeout_ms)
                    elif proposal.action_type == "click":
                        locator = _locator_for(page, proposal.target_ref or "")
                        if locator.count() != 1:
                            raise BrowserPolicyError("hybrid target is not unique")
                        locator.click(timeout=int(spec.budget.action_timeout_seconds * 1000))
                    elif proposal.action_type == "scroll":
                        page.mouse.wheel(0, 500)
                    elif proposal.action_type == "wait":
                        page.wait_for_timeout(
                            min(100, int(spec.budget.action_timeout_seconds * 1000))
                        )
                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=int(spec.budget.action_timeout_seconds * 1000),
                    )
                except BrowserPolicyError as error:
                    code = "STALE_OBSERVATION" if "stale" in str(error) else "ACTION_REJECTED"
                    receipts.append(
                        _rejected_receipt(
                            proposal,
                            frame.observation,
                            code,
                            code,
                        )
                    )
                    status = "FAILED"
                    stop_reason = code
                    break
                except Exception as error:
                    receipts.append(
                        ActionReceipt(
                            action_id=proposal.action_id,
                            observation_id=proposal.observation_id,
                            result="FAILED",
                            error_code=type(error).__name__.upper(),
                            before_observation_id=frame.observation.observation_id,
                            stop_reason="hybrid_action_failed",
                        )
                    )
                    status = "FAILED"
                    stop_reason = "hybrid_action_failed"
                    break
                terminal = _terminal_page_state(page)
                if terminal is not None:
                    receipts.append(
                        ActionReceipt(
                            action_id=proposal.action_id,
                            observation_id=proposal.observation_id,
                            result="APPLIED",
                            before_observation_id=frame.observation.observation_id,
                        )
                    )
                    status, stop_reason = terminal
                    break
                try:
                    wait_for_ready(
                        page,
                        timeout_ms=remaining_timeout_ms(
                            started,
                            wall_time_seconds=spec.budget.wall_time_seconds,
                            action_timeout_seconds=spec.budget.action_timeout_seconds,
                        ),
                        scenario=spec.scenario,
                    )
                except RecoveryFailure as failure:
                    receipts.append(
                        _rejected_receipt(
                            proposal,
                            frame.observation,
                            failure.code,
                            failure.code,
                        )
                    )
                    status = (
                        "BUDGET_EXCEEDED"
                        if failure.code in {"OBSERVATION_TIMEOUT", "WALL_TIME_BUDGET"}
                        else "FAILED"
                    )
                    stop_reason = failure.code
                    break
                before_frame = frame
                try:
                    frame = _hybrid_frame(
                        page,
                        spec,
                        timeout_ms=remaining_timeout_ms(
                            started,
                            wall_time_seconds=spec.budget.wall_time_seconds,
                            action_timeout_seconds=spec.budget.action_timeout_seconds,
                        ),
                    )
                except RecoveryFailure as failure:
                    receipts.append(
                        _rejected_receipt(
                            proposal,
                            before_frame.observation,
                            failure.code,
                            failure.code,
                        )
                    )
                    status = (
                        "BUDGET_EXCEEDED"
                        if failure.code in {"OBSERVATION_TIMEOUT", "WALL_TIME_BUDGET"}
                        else "FAILED"
                    )
                    stop_reason = failure.code
                    break
                frames.append(frame)
                unchanged_reobservations = (
                    unchanged_reobservations + 1
                    if frame.observation.page_version == before_frame.observation.page_version
                    else 0
                )
                receipts.append(
                    ActionReceipt(
                        action_id=proposal.action_id,
                        observation_id=proposal.observation_id,
                        result="APPLIED",
                        before_observation_id=before_frame.observation.observation_id,
                        after_observation_id=frame.observation.observation_id,
                    )
                )
                if unchanged_reobservations > spec.budget.max_reobservations:
                    status = "FAILED"
                    stop_reason = "NO_PROGRESS"
                    break
                terminal = _terminal_page_state(page)
                if terminal is not None:
                    status, stop_reason = terminal
                    break
            else:
                status = "BUDGET_EXCEEDED"
                stop_reason = "action_budget"
            security_violations = tuple(policy.violations + browser_events)
            if security_violations:
                status = "FAILED"
                fields = {}
                stop_reason = "browser_security_violation"
            result = TaskResult(
                case_id=spec.case_id,
                status=status,
                fields=fields if status == "SUCCEEDED" else {},
                evidence_refs=tuple(frame.observation.observation_id for frame in frames),
                observation_complete=status == "SUCCEEDED",
                actions=tuple(receipts),
                stop_reason=stop_reason,
            )
        finally:
            context.close()
            browser.close()
    return HybridRun(
        result=result,
        frames=tuple(frames),
        security_violations=security_violations,
        model_calls=model_calls,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def model_hybrid_decider(client: LiveVisionModel) -> HybridDecider:
    """Adapt one image model to the synchronized hybrid executor."""

    def decide(frame: HybridFrame, spec: TaskSpec) -> HybridDecision:
        decision: ModelDecision = decision_from_vision(
            client,
            spec,
            frame.observation,
            frame.screenshot,
            spec.budget.max_actions,
        )
        return HybridDecision(
            proposal=decision.proposal,
            fields=decision.fields,
            visual_fields=decision.visual_fields,
            model=decision.model,
            prompt_tokens=decision.prompt_tokens,
            completion_tokens=decision.completion_tokens,
        )

    return decide


__all__ = [
    "HybridDecision",
    "HybridFrame",
    "HybridRun",
    "model_hybrid_decider",
    "run_hybrid_task",
]
