"""Screenshot-only GUI actions for the Phase 11 experiment."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from labs.web_gui.browser import (
    BrowserPolicyError,
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
)
from labs.web_gui.security import BrowserSecurityPolicy

MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
VisualDecider = Callable[[bytes, Observation, TaskSpec], "VisualDecision"]


@dataclass(frozen=True)
class VisualDecision:
    proposal: ActionProposal
    fields: dict[str, str | None] | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class VisualRun:
    result: TaskResult
    observations: tuple[Observation, ...]
    screenshots: tuple[bytes, ...]
    security_violations: tuple[str, ...] = ()
    model_calls: int = 0
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _visual_observation(
    page: Any, source: str, *, timeout_ms: int | None = None
) -> tuple[Observation, bytes]:
    screenshot_options: dict[str, object] = {"type": "png", "animations": "disabled"}
    if timeout_ms is not None:
        screenshot_options["timeout"] = timeout_ms
    try:
        screenshot = page.screenshot(**screenshot_options)
    except Exception as error:
        if type(error).__name__ == "TimeoutError":
            raise RecoveryFailure("OBSERVATION_TIMEOUT") from error
        raise
    if len(screenshot) > MAX_SCREENSHOT_BYTES or not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BrowserPolicyError("screenshot is invalid or too large")
    digest = hashlib.sha256(screenshot).hexdigest()
    viewport = page.viewport_size or {}
    width = viewport.get("width")
    height = viewport.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise BrowserPolicyError("visual viewport is unavailable")
    content = json.dumps(
        {"screenshot_sha256": digest, "viewport": [width, height]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        Observation(
            page_version=f"screenshot:{digest}",
            source=source,  # type: ignore[arg-type]
            mode="vision",
            content=content,
            screenshot_sha256=digest,
            viewport_width=width,
            viewport_height=height,
        ),
        screenshot,
    )


def _validate_visual_action(proposal: ActionProposal, observation: Observation) -> None:
    if proposal.observation_id != observation.observation_id:
        raise BrowserPolicyError("action references a stale screenshot")
    if observation.mode != "vision":
        raise BrowserPolicyError("visual action requires a screenshot observation")
    if proposal.action_type == "click":
        if proposal.target_ref is not None or proposal.text is not None:
            raise BrowserPolicyError("visual clicks cannot use DOM targets or text answers")
        if proposal.x is None or proposal.y is None:
            raise BrowserPolicyError("visual clicks require x and y")
        if proposal.x >= (observation.viewport_width or 0) or proposal.y >= (
            observation.viewport_height or 0
        ):
            raise BrowserPolicyError("visual click is outside the viewport")
        return
    if proposal.action_type in {"scroll", "wait", "finish"}:
        if proposal.target_ref is not None or proposal.text is not None:
            raise BrowserPolicyError("visual action contains an unexpected target")
        return
    raise BrowserPolicyError("visual action type is not allowed")


def _fields_from_decision(decision: VisualDecision) -> dict[str, str | None]:
    fields = decision.fields or {}
    allowed = {"purchase_order", "supplier", "status", "currency"}
    if set(fields) - allowed:
        raise BrowserPolicyError("visual answer contains an unknown field")
    if any(
        value is not None and (not isinstance(value, str) or len(value) > 140)
        for value in fields.values()
    ):
        raise BrowserPolicyError("visual answer contains an invalid field")
    return {field: fields.get(field) for field in sorted(allowed)}


def _model_output_size(decision: VisualDecision) -> int:
    payload = {
        "proposal": decision.proposal.model_dump(mode="json"),
        "fields": decision.fields,
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


def run_visual_task(base_url: str, spec: TaskSpec, decider: VisualDecider) -> VisualRun:
    """Run a screenshot-only task with a bounded, injected visual decider."""

    if spec.mode != "vision":
        raise ValueError("run_visual_task requires a vision TaskSpec")
    if spec.data_source != "synthetic":
        raise BrowserPolicyError(
            "generic visual runner accepts synthetic data only; use redacted ERP runner"
        )
    origin = _origin(base_url)
    started = monotonic()
    sync_playwright = _playwright_sync()
    observations: list[Observation] = []
    screenshots: list[bytes] = []
    receipts: list[ActionReceipt] = []
    status: TaskStatus = "INCOMPLETE"
    fields: dict[str, str | None] = {}
    stop_reason: str | None = "visual_decider_stopped"
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
            service_workers="block", accept_downloads=False, viewport={"width": 1024, "height": 768}
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
            page.goto(
                f"{origin}/",
                wait_until="domcontentloaded",
                timeout=int(spec.budget.action_timeout_seconds * 1000),
            )
            try:
                observation, screenshot = _visual_observation(
                    page,
                    spec.data_source,
                    timeout_ms=remaining_timeout_ms(
                        started,
                        wall_time_seconds=spec.budget.wall_time_seconds,
                        action_timeout_seconds=spec.budget.action_timeout_seconds,
                    ),
                )
            except RecoveryFailure as failure:
                observation = None
                screenshot = b""
                status = "BUDGET_EXCEEDED"
                stop_reason = failure.code
            else:
                assert observation is not None
                observations.append(observation)
                screenshots.append(screenshot)
            for _ in range(spec.budget.max_actions):
                if observation is None:
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

                def invoke_visual(
                    current_screenshot: bytes = screenshot,
                    current_observation: Observation = observation,
                    current_spec: TaskSpec = spec,
                ) -> VisualDecision:
                    return decider(current_screenshot, current_observation, current_spec)

                try:
                    decision = run_with_deadline(
                        invoke_visual,
                        min(spec.budget.action_timeout_seconds, remaining),
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
                if not isinstance(decision, VisualDecision):
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
                if model_elapsed > spec.budget.action_timeout_seconds:
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
                    _validate_visual_action(proposal, observation)
                    progress.record(
                        observation.page_version,
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
                    receipts.append(_rejected_receipt(proposal, observation, code, code))
                    status = "BUDGET_EXCEEDED" if code == "ACTION_BUDGET" else "FAILED"
                    stop_reason = code
                    break
                if proposal.action_type != "finish":
                    try:
                        current_observation, current_screenshot = _visual_observation(
                            page,
                            spec.data_source,
                            timeout_ms=remaining_timeout_ms(
                                started,
                                wall_time_seconds=spec.budget.wall_time_seconds,
                                action_timeout_seconds=spec.budget.action_timeout_seconds,
                            ),
                        )
                    except RecoveryFailure as failure:
                        code = failure.code
                        receipts.append(_rejected_receipt(proposal, observation, code, code))
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
                                proposal, observation, "STALE_OBSERVATION", "stale_observation"
                            )
                        )
                        status = "FAILED"
                        stop_reason = "STALE_OBSERVATION"
                        break
                    if current_observation.page_version != observation.page_version:
                        observations.append(current_observation)
                        screenshots.append(current_screenshot)
                        receipts.append(
                            _rejected_receipt(
                                proposal, observation, "STALE_OBSERVATION", "stale_observation"
                            )
                        )
                        status = "FAILED"
                        stop_reason = "STALE_OBSERVATION"
                        break
                if proposal.action_type == "finish":
                    try:
                        fields = _fields_from_decision(decision)
                    except BrowserPolicyError:
                        receipts.append(
                            _rejected_receipt(
                                proposal, observation, "ANSWER_REJECTED", "answer_rejected"
                            )
                        )
                        status = "FAILED"
                        stop_reason = "answer_rejected"
                        break
                    trusted = fixture_fields(spec.purchase_order)
                    if trusted is None:
                        status = "NOT_FOUND"
                        stop_reason = "fixture_not_found"
                    elif fields != trusted:
                        status = "INCOMPLETE"
                        stop_reason = "visual_fields_mismatch"
                    else:
                        status = "SUCCEEDED"
                        stop_reason = None
                    receipts.append(
                        ActionReceipt(
                            action_id=proposal.action_id,
                            observation_id=proposal.observation_id,
                            result="APPLIED",
                            before_observation_id=observation.observation_id,
                            stop_reason=stop_reason,
                        )
                    )
                    break
                try:
                    if proposal.action_type == "click":
                        page.mouse.click(proposal.x or 0, proposal.y or 0)
                        page.wait_for_load_state(
                            "domcontentloaded",
                            timeout=int(spec.budget.action_timeout_seconds * 1000),
                        )
                    elif proposal.action_type == "scroll":
                        page.mouse.wheel(0, 500)
                    elif proposal.action_type == "wait":
                        page.wait_for_timeout(
                            min(100, int(spec.budget.action_timeout_seconds * 1000))
                        )
                except Exception as error:
                    receipts.append(
                        ActionReceipt(
                            action_id=proposal.action_id,
                            observation_id=proposal.observation_id,
                            result="FAILED",
                            error_code=type(error).__name__.upper(),
                            before_observation_id=observation.observation_id,
                            stop_reason="visual_action_failed",
                        )
                    )
                    status = "FAILED"
                    stop_reason = "visual_action_failed"
                    break
                before_observation = observation
                try:
                    observation, screenshot = _visual_observation(
                        page,
                        spec.data_source,
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
                            before_observation,
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
                observations.append(observation)
                screenshots.append(screenshot)
                unchanged_reobservations = (
                    unchanged_reobservations + 1
                    if observation.page_version == before_observation.page_version
                    else 0
                )
                receipt = ActionReceipt(
                    action_id=proposal.action_id,
                    observation_id=proposal.observation_id,
                    result="APPLIED",
                    before_observation_id=before_observation.observation_id,
                    after_observation_id=observation.observation_id,
                )
                receipts.append(receipt)
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
                evidence_refs=tuple(item.observation_id for item in observations),
                observation_complete=status == "SUCCEEDED",
                actions=tuple(receipts),
                stop_reason=stop_reason,
            )
        finally:
            context.close()
            browser.close()
    return VisualRun(
        result=result,
        observations=tuple(observations),
        screenshots=tuple(screenshots),
        security_violations=security_violations,
        model_calls=model_calls,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def model_visual_decider(client: LiveVisionModel) -> VisualDecider:
    """Adapt one image model to the existing screenshot executor."""

    def decide(image: bytes, observation: Observation, spec: TaskSpec) -> VisualDecision:
        decision: ModelDecision = decision_from_vision(
            client,
            spec,
            observation,
            image,
            spec.budget.max_actions,
        )
        return VisualDecision(
            proposal=decision.proposal,
            fields=decision.fields,
            model=decision.model,
            prompt_tokens=decision.prompt_tokens,
            completion_tokens=decision.completion_tokens,
        )

    return decide


__all__ = [
    "MAX_SCREENSHOT_BYTES",
    "VisualDecision",
    "VisualRun",
    "model_visual_decider",
    "run_visual_task",
]
