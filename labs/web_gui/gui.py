"""Screenshot-only GUI actions for the Phase 11 experiment."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from labs.web_gui.browser import BrowserPolicyError, _origin, _playwright_sync
from labs.web_gui.contracts import (
    ActionProposal,
    ActionReceipt,
    Observation,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from labs.web_gui.security import BrowserSecurityPolicy

MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
VisualDecider = Callable[[bytes, Observation, TaskSpec], "VisualDecision"]


@dataclass(frozen=True)
class VisualDecision:
    proposal: ActionProposal
    fields: dict[str, str | None] | None = None


@dataclass(frozen=True)
class VisualRun:
    result: TaskResult
    observations: tuple[Observation, ...]
    screenshots: tuple[bytes, ...]
    security_violations: tuple[str, ...] = ()


def _visual_observation(page: Any, source: str) -> tuple[Observation, bytes]:
    screenshot = page.screenshot(type="png", animations="disabled")
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


def run_visual_task(base_url: str, spec: TaskSpec, decider: VisualDecider) -> VisualRun:
    """Run a screenshot-only task with a bounded, injected visual decider."""

    if spec.mode != "vision":
        raise ValueError("run_visual_task requires a vision TaskSpec")
    origin = _origin(base_url)
    started = monotonic()
    sync_playwright = _playwright_sync()
    observations: list[Observation] = []
    screenshots: list[bytes] = []
    receipts: list[ActionReceipt] = []
    status: TaskStatus = "INCOMPLETE"
    fields: dict[str, str | None] = {}
    stop_reason: str | None = "visual_decider_stopped"
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

        context.on("page", on_popup)
        try:
            page.goto(f"{origin}/", wait_until="domcontentloaded", timeout=10_000)
            observation, screenshot = _visual_observation(page, spec.data_source)
            observations.append(observation)
            screenshots.append(screenshot)
            for _ in range(spec.budget.max_actions):
                if monotonic() - started > spec.budget.wall_time_seconds:
                    status = "BUDGET_EXCEEDED"
                    stop_reason = "wall_time_budget"
                    break
                if not isinstance(
                    decision := decider(screenshot, observation, spec), VisualDecision
                ):
                    raise BrowserPolicyError("visual decider returned an invalid decision")
                _validate_visual_action(decision.proposal, observation)
                proposal = decision.proposal
                if proposal.action_type == "finish":
                    fields = _fields_from_decision(decision)
                    status = (
                        "SUCCEEDED"
                        if fields.get("purchase_order") == spec.purchase_order
                        and all(fields.values())
                        else "INCOMPLETE"
                    )
                    stop_reason = None if status == "SUCCEEDED" else "visual_fields_incomplete"
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
                        page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    elif proposal.action_type == "scroll":
                        page.mouse.wheel(0, 500)
                    elif proposal.action_type == "wait":
                        page.wait_for_timeout(100)
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
                receipts.append(
                    ActionReceipt(
                        action_id=proposal.action_id,
                        observation_id=proposal.observation_id,
                        result="APPLIED",
                        before_observation_id=observation.observation_id,
                    )
                )
                observation, screenshot = _visual_observation(page, spec.data_source)
                observations.append(observation)
                screenshots.append(screenshot)
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
    )


__all__ = [
    "MAX_SCREENSHOT_BYTES",
    "VisualDecision",
    "VisualRun",
    "run_visual_task",
]
