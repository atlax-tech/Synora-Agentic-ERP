"""Screenshot-only GUI execution on a redacted real ERP page."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

from labs.web_gui.browser import BrowserPolicyError, BrowserUnavailable, _origin, _playwright_sync
from labs.web_gui.contracts import ActionReceipt, Observation, TaskResult, TaskSpec
from labs.web_gui.erp_browser import _RealPolicy
from labs.web_gui.erp_readonly import MAX_RESPONSE_BYTES, ErpReadConfig
from labs.web_gui.gui import (
    VisualDecision,
    _fields_from_decision,
    _model_output_size,
    _validate_visual_action,
)
from labs.web_gui.recovery import ProgressGuard, RecoveryFailure, run_with_deadline
from labs.web_gui.redaction import RedactedCapture, capture_redacted_page

VisualDecider = Callable[[bytes, Observation, TaskSpec], VisualDecision]


@dataclass(frozen=True)
class ErpVisualRun:
    result: TaskResult
    observations: tuple[Observation, ...]
    screenshots: tuple[bytes, ...]
    safety_pass: bool
    policy_events: tuple[str, ...] = ()
    redaction: RedactedCapture | None = None


def _rejected_receipt(
    proposal: Any, observation: Observation, error_code: str, reason: str
) -> ActionReceipt:
    return ActionReceipt(
        action_id=proposal.action_id,
        observation_id=proposal.observation_id,
        result="REJECTED",
        error_code=error_code,
        before_observation_id=observation.observation_id,
        stop_reason=reason,
    )


def _task(config: ErpReadConfig) -> TaskSpec:
    return TaskSpec(
        case_id=f"p11-erp-visual-{config.purchase_order}",
        purchase_order=config.purchase_order,
        mode="vision",
        data_source="erp_readonly",
    )


def _blocked(config: ErpReadConfig, code: str) -> ErpVisualRun:
    task = _task(config)
    return ErpVisualRun(
        result=TaskResult(case_id=task.case_id, status="BLOCKED", stop_reason=code),
        observations=(),
        screenshots=(),
        safety_pass=True,
        policy_events=(),
    )


def _observation(capture: RedactedCapture) -> Observation:
    assert capture.image is not None and capture.image_sha256 and capture.viewport
    width, height = capture.viewport
    return Observation(
        page_version=f"erp-redacted:{capture.image_sha256}",
        source="erp_readonly",
        mode="vision",
        content=json.dumps(
            {"screenshot_sha256": capture.image_sha256, "viewport": [width, height]},
            sort_keys=True,
            separators=(",", ":"),
        ),
        screenshot_sha256=capture.image_sha256,
        viewport_width=width,
        viewport_height=height,
    )


def _result(
    task: TaskSpec,
    status: Any,
    fields: dict[str, str | None],
    observations: list[Observation],
    receipts: list[ActionReceipt],
    reason: str | None,
) -> TaskResult:
    return TaskResult(
        case_id=task.case_id,
        status=status,
        fields=fields if status == "SUCCEEDED" else {},
        evidence_refs=tuple(item.observation_id for item in observations),
        observation_complete=status == "SUCCEEDED",
        actions=tuple(receipts),
        stop_reason=reason,
    )


def run_erp_visual_task(
    config: ErpReadConfig,
    decider: VisualDecider,
    *,
    trusted_fields: Mapping[str, str] | None = None,
) -> ErpVisualRun:
    """Run a bounded coordinate loop; the decider receives no DOM or API data."""

    if not os.environ.get("SYNORA_P2P_USER_PWD"):
        return _blocked(config, "ERP_CREDENTIALS_UNAVAILABLE")
    task = _task(config)
    started = monotonic()
    try:
        sync_playwright = _playwright_sync()
    except BrowserUnavailable:
        return _blocked(config, "PLAYWRIGHT_UNAVAILABLE")
    observations: list[Observation] = []
    screenshots: list[bytes] = []
    receipts: list[ActionReceipt] = []
    events: list[str] = []
    policy = _RealPolicy(_origin(config.base_url), config.purchase_order)
    capture: RedactedCapture | None = None
    status: Any = "INCOMPLETE"
    reason: str | None = "visual_decider_stopped"
    fields: dict[str, str | None] = {}
    model_calls = 0
    progress = ProgressGuard(max_actions=task.budget.max_actions)
    unchanged_reobservations = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            service_workers="block", accept_downloads=False, viewport={"width": 1024, "height": 768}
        )

        def route_handler(route: Any) -> None:
            if policy.check(route.request.method, route.request.url):
                route.continue_()
            else:
                route.abort(error_code="blockedbyclient")

        context.route("**/*", route_handler)
        page = context.new_page()

        def on_popup(popup: Any) -> None:
            events.append("POPUP_BLOCKED")
            popup.close()

        def on_download(download: Any) -> None:
            events.append("DOWNLOAD_BLOCKED")
            download.cancel()

        def on_dialog(dialog: Any) -> None:
            events.append("DIALOG_DISMISSED")
            dialog.dismiss()

        def on_response(response: Any) -> None:
            response_path = str(response.url).split(policy.origin, 1)[-1].split("?", 1)[0]
            if not (
                response_path.startswith("/api/")
                or response_path == f"/desk/purchase-order/{config.purchase_order}"
            ):
                return
            try:
                content_length = int(response.headers.get("content-length", "0"))
            except TypeError, ValueError:
                content_length = 0
            if content_length > MAX_RESPONSE_BYTES:
                events.append("RESPONSE_TOO_LARGE")

        context.on("page", on_popup)
        page.on("download", on_download)
        page.on("dialog", on_dialog)
        page.on("response", on_response)

        try:
            login = context.request.post(
                f"{_origin(config.base_url)}/api/method/login",
                form={"usr": config.user, "pwd": os.environ["SYNORA_P2P_USER_PWD"]},
                max_redirects=0,
            )
            if login.status in {401, 403}:
                status, reason = "AUTH_REQUIRED", "ERP_LOGIN_REJECTED"
            elif not 200 <= login.status < 300:
                status, reason = "FAILED", "ERP_LOGIN_FAILED"
            else:
                page.goto(
                    f"{_origin(config.base_url)}/desk/purchase-order/{config.purchase_order}",
                    wait_until="domcontentloaded",
                    timeout=int(config.timeout_seconds * 1000),
                )
                if "/login" in page.url:
                    status, reason = "AUTH_REQUIRED", "AUTH_REQUIRED"
                else:
                    body_text = page.locator("body").inner_text(
                        timeout=int(config.timeout_seconds * 1000)
                    )
                    if len(body_text.encode("utf-8")) > MAX_RESPONSE_BYTES:
                        status, reason = "FAILED", "ERP_RESPONSE_TOO_LARGE"
                    elif "Not Permitted" in body_text:
                        status, reason = "PERMISSION_DENIED", "PERMISSION_DENIED"
                    else:
                        page.locator('[data-fieldname="supplier"] .control-value').wait_for(
                            state="visible", timeout=int(config.timeout_seconds * 1000)
                        )
                        page.locator(".page-head .indicator-pill").wait_for(
                            state="visible", timeout=int(config.timeout_seconds * 1000)
                        )
                        capture = capture_redacted_page(page, config.purchase_order)
                    if capture is None or capture.status != "READY":
                        status, reason = (
                            "BLOCKED",
                            capture.failure_code if capture else "REDACTION_FAILED",
                        )
                    else:
                        observation = _observation(capture)
                        observations.append(observation)
                        screenshots.append(capture.image or b"")
                        for _ in range(task.budget.max_actions):
                            if monotonic() - started > task.budget.wall_time_seconds:
                                status, reason = "BUDGET_EXCEEDED", "wall_time_budget"
                                break
                            if model_calls >= task.budget.max_model_calls:
                                status, reason = "BUDGET_EXCEEDED", "model_call_budget"
                                break
                            remaining = task.budget.wall_time_seconds - (monotonic() - started)
                            if remaining <= 0:
                                status, reason = "BUDGET_EXCEEDED", "wall_time_budget"
                                break
                            decision: VisualDecision | None = None
                            model_started = monotonic()
                            model_calls += 1

                            def invoke_visual(
                                image: bytes = screenshots[-1],
                                current_observation: Observation = observation,
                                current_task: TaskSpec = task,
                            ) -> VisualDecision:
                                return decider(image, current_observation, current_task)

                            try:
                                decision = run_with_deadline(
                                    invoke_visual,
                                    min(task.budget.action_timeout_seconds, remaining),
                                )
                            except RecoveryFailure as failure:
                                status, reason = (
                                    ("BUDGET_EXCEEDED", "model_timeout")
                                    if failure.code == "MODEL_TIMEOUT"
                                    else ("FAILED", "model_call_failed")
                                )
                                break
                            model_elapsed = monotonic() - model_started
                            if not isinstance(decision, VisualDecision):
                                status, reason = "FAILED", "invalid_model_decision"
                                break
                            if model_elapsed > task.budget.action_timeout_seconds:
                                status, reason = "BUDGET_EXCEEDED", "model_timeout"
                                break
                            if monotonic() - started > task.budget.wall_time_seconds:
                                status, reason = "BUDGET_EXCEEDED", "wall_time_budget"
                                break
                            if _model_output_size(decision) > task.budget.max_output_tokens * 4:
                                status, reason = "BUDGET_EXCEEDED", "model_output_budget"
                                break
                            proposal = decision.proposal
                            try:
                                _validate_visual_action(proposal, observation)
                                if proposal.action_type == "finish":
                                    fields = _fields_from_decision(decision)
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
                            except (BrowserPolicyError, RecoveryFailure) as error:
                                code = (
                                    error.code
                                    if isinstance(error, RecoveryFailure)
                                    else "ACTION_REJECTED"
                                )
                                receipts.append(
                                    _rejected_receipt(
                                        proposal,
                                        observation,
                                        code,
                                        code,
                                    )
                                )
                                status = "BUDGET_EXCEEDED" if code == "ACTION_BUDGET" else "FAILED"
                                reason = code
                                break
                            if proposal.action_type == "finish":
                                if trusted_fields is None:
                                    status, reason = "INCOMPLETE", "trusted_erp_fact_unavailable"
                                elif fields != dict(trusted_fields):
                                    status, reason = "INCOMPLETE", "visual_fields_mismatch"
                                else:
                                    status, reason = "SUCCEEDED", None
                                receipts.append(
                                    ActionReceipt(
                                        action_id=proposal.action_id,
                                        observation_id=proposal.observation_id,
                                        result="APPLIED",
                                        before_observation_id=observation.observation_id,
                                        stop_reason=reason,
                                    )
                                )
                                break
                            try:
                                if proposal.action_type == "click":
                                    fresh_capture = capture_redacted_page(
                                        page, config.purchase_order
                                    )
                                    if (
                                        fresh_capture.status != "READY"
                                        or fresh_capture.image_sha256
                                        != observation.screenshot_sha256
                                    ):
                                        if fresh_capture.status == "READY":
                                            fresh_observation = _observation(fresh_capture)
                                            observations.append(fresh_observation)
                                            screenshots.append(fresh_capture.image or b"")
                                        raise BrowserPolicyError("STALE_SCREENSHOT")
                                    safe_box = page.locator(".form-layout").bounding_box()
                                    if safe_box is None or not (
                                        safe_box[0]
                                        <= (proposal.x or 0)
                                        <= safe_box[0] + safe_box[2]
                                        and safe_box[1]
                                        <= (proposal.y or 0)
                                        <= safe_box[1] + safe_box[3]
                                    ):
                                        raise BrowserPolicyError(
                                            "visual click is outside the redacted task region"
                                        )
                                    page.mouse.click(proposal.x or 0, proposal.y or 0)
                                    page.wait_for_load_state(
                                        "domcontentloaded",
                                        timeout=int(task.budget.action_timeout_seconds * 1000),
                                    )
                                elif proposal.action_type == "scroll":
                                    page.mouse.wheel(0, 500)
                                else:
                                    page.wait_for_timeout(
                                        min(100, int(task.budget.action_timeout_seconds * 1000))
                                    )
                            except BrowserPolicyError as error:
                                code = (
                                    "STALE_SCREENSHOT"
                                    if str(error) == "STALE_SCREENSHOT"
                                    else "ACTION_REJECTED"
                                )
                                receipts.append(
                                    _rejected_receipt(proposal, observation, code, code)
                                )
                                status, reason = "FAILED", code
                                break
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
                                status, reason = "FAILED", "visual_action_failed"
                                break
                            if "/login" in page.url:
                                receipts.append(
                                    ActionReceipt(
                                        action_id=proposal.action_id,
                                        observation_id=proposal.observation_id,
                                        result="APPLIED",
                                        before_observation_id=observation.observation_id,
                                    )
                                )
                                status, reason = "AUTH_REQUIRED", "AUTH_REQUIRED"
                                break
                            before_observation = observation
                            capture = capture_redacted_page(page, config.purchase_order)
                            if capture.status != "READY":
                                receipts.append(
                                    ActionReceipt(
                                        action_id=proposal.action_id,
                                        observation_id=proposal.observation_id,
                                        result="APPLIED",
                                        before_observation_id=before_observation.observation_id,
                                    )
                                )
                                status, reason = "BLOCKED", capture.failure_code
                                break
                            observation = _observation(capture)
                            observations.append(observation)
                            screenshots.append(capture.image or b"")
                            unchanged_reobservations = (
                                unchanged_reobservations + 1
                                if observation.page_version == before_observation.page_version
                                else 0
                            )
                            receipts.append(
                                ActionReceipt(
                                    action_id=proposal.action_id,
                                    observation_id=proposal.observation_id,
                                    result="APPLIED",
                                    before_observation_id=before_observation.observation_id,
                                    after_observation_id=observation.observation_id,
                                )
                            )
                            if unchanged_reobservations > task.budget.max_reobservations:
                                status, reason = "FAILED", "NO_PROGRESS"
                                break
                        else:
                            status, reason = "BUDGET_EXCEEDED", "action_budget"
        except Exception as error:
            status, reason = "FAILED", type(error).__name__.upper()
        finally:
            context.close()
            browser.close()
    policy_events = tuple(policy.violations + policy.blocked + events)
    if policy.violations or events:
        status, reason = "FAILED", "browser_security_violation"
    result = _result(task, status, fields, observations, receipts, reason)
    return ErpVisualRun(
        result,
        tuple(observations),
        tuple(screenshots),
        not policy.violations and not events,
        policy_events,
        capture,
    )


__all__ = ["ErpVisualRun", "run_erp_visual_task"]
