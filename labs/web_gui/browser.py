"""Bounded DOM browser tasks for the Phase 11 synthetic page.

The browser is deliberately driven by a small allowlisted policy.  A future
model adapter may propose the same typed actions, but it cannot add selectors,
JavaScript, URLs, or arbitrary browser commands.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from labs.web_gui.contracts import (
    ActionProposal,
    ActionReceipt,
    Observation,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from labs.web_gui.recovery import RecoveryFailure, wait_for_ready
from labs.web_gui.security import BrowserSecurityPolicy


class BrowserPolicyError(ValueError):
    """The proposed browser action is outside the experiment allowlist."""


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed in the optional lab dependency group."""


@dataclass(frozen=True)
class DomSnapshot:
    observation: Observation
    targets: frozenset[str]


@dataclass(frozen=True)
class DomRun:
    result: TaskResult
    observations: tuple[Observation, ...]
    security_violations: tuple[str, ...] = ()


def _terminal_page_state(page: Any) -> tuple[TaskStatus, str] | None:
    if page.locator('[data-state="auth-required"]').count() == 1:
        return "AUTH_REQUIRED", "AUTH_REQUIRED"
    if page.locator('[data-state="permission-denied"]').count() == 1:
        return "PERMISSION_DENIED", "PERMISSION_DENIED"
    return None


def _playwright_sync() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:  # pragma: no cover - exercised without optional extras
        raise BrowserUnavailable("install the web-gui-lab dependency group") from error
    return sync_playwright


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise BrowserPolicyError("browser tasks only allow loopback http origins")
    if parsed.username or parsed.password or parsed.fragment:
        raise BrowserPolicyError("credentials and fragments are not allowed in browser URLs")
    return f"{parsed.scheme}://{parsed.netloc}"


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot(page: Any, spec: TaskSpec, mode: str = "dom") -> DomSnapshot:
    if spec.data_source != "synthetic":
        raise BrowserPolicyError(
            "generic DOM/ARIA runner accepts synthetic data only; use the ERP readonly adapter"
        )
    body = page.locator("body")
    version = body.get_attribute("data-page-version") or "unknown"
    timeout = spec.budget.action_timeout_seconds * 1000
    text = body.inner_text(timeout=timeout)
    if len(text) > 50_000:
        raise BrowserPolicyError("observation is too large")
    targets: set[str] = set()
    if mode == "aria":
        try:
            aria = body.aria_snapshot(timeout=timeout)
        except Exception as error:
            raise BrowserPolicyError("accessibility observation failed") from error
        if not isinstance(aria, str) or len(aria) > 50_000:
            raise BrowserPolicyError("accessibility observation is too large")
        content = aria
        if page.get_by_role("textbox", name="Purchase order number").count() == 1:
            targets.add("search-input")
        if page.get_by_role("button", name="Search").count() == 1:
            targets.add("search-submit")
        if page.get_by_role("link", name="Back to search").count() == 1:
            targets.add("back")
        for label in page.get_by_role("link").all_inner_texts():
            match = re.fullmatch(r"View (PUR-[A-Z0-9-]+) details", label.strip())
            if match:
                targets.add(f"order:{match.group(1)}")
    else:
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
            {"page_version": version, "text": text, "targets": sorted(targets)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return DomSnapshot(
        observation=Observation(
            page_version=version,
            source=spec.data_source,
            mode=mode,  # type: ignore[arg-type]
            content=content,
        ),
        targets=frozenset(targets),
    )


def _validate_action(proposal: ActionProposal, snapshot: DomSnapshot) -> None:
    if proposal.observation_id != snapshot.observation.observation_id:
        raise BrowserPolicyError("action references a stale observation")
    if proposal.action_type == "search":
        if proposal.target_ref != "search-input" or not proposal.text:
            raise BrowserPolicyError("search must target the observed search input")
        return
    if proposal.action_type == "click":
        if proposal.target_ref not in snapshot.targets:
            raise BrowserPolicyError("click target was not present in the observation")
        if proposal.x is not None or proposal.y is not None:
            raise BrowserPolicyError("DOM mode does not accept coordinate clicks")
        return
    if proposal.action_type in {"open", "scroll", "wait", "finish"}:
        return
    raise BrowserPolicyError("action type is not allowed")


def _locator_for(page: Any, target_ref: str, mode: str = "dom") -> Any:
    if mode == "aria":
        if target_ref == "search-input":
            return page.get_by_role("textbox", name="Purchase order number")
        if target_ref == "search-submit":
            return page.get_by_role("button", name="Search")
        if target_ref == "back":
            return page.get_by_role("link", name="Back to search")
        if target_ref.startswith("order:"):
            name = target_ref.removeprefix("order:")
            return page.get_by_role("link", name=f"View {name} details")
    if target_ref == "search-input":
        return page.locator("#order-search")
    if target_ref == "search-submit":
        return page.locator('[data-action="search"]')
    if target_ref == "back":
        return page.locator('[data-action="back"]')
    if target_ref.startswith("order:"):
        name = target_ref.removeprefix("order:")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,140}", name):
            raise BrowserPolicyError("order target contains unsafe characters")
        return page.locator(
            f'[data-order-name="{name}"] [data-order-link], '
            f'[data-order-id="{name}"] [data-order-link]'
        )
    raise BrowserPolicyError("unknown observed target")


def _apply_action(
    page: Any, proposal: ActionProposal, snapshot: DomSnapshot, spec: TaskSpec
) -> ActionReceipt:
    before = snapshot.observation
    try:
        _validate_action(proposal, snapshot)
        if proposal.action_type == "search":
            locator = _locator_for(page, "search-input", snapshot.observation.mode)
            locator.fill(proposal.text or "", timeout=spec.budget.action_timeout_seconds * 1000)
            _locator_for(page, "search-submit", snapshot.observation.mode).click(
                timeout=spec.budget.action_timeout_seconds * 1000
            )
            page.wait_for_load_state(
                "domcontentloaded", timeout=spec.budget.action_timeout_seconds * 1000
            )
        elif proposal.action_type == "click":
            locator = _locator_for(page, proposal.target_ref or "", snapshot.observation.mode)
            if locator.count() != 1:
                raise BrowserPolicyError("observed target is not unique")
            locator.click(timeout=spec.budget.action_timeout_seconds * 1000)
            page.wait_for_load_state(
                "domcontentloaded", timeout=spec.budget.action_timeout_seconds * 1000
            )
        elif proposal.action_type == "scroll":
            page.mouse.wheel(0, 500)
        elif proposal.action_type == "wait":
            page.wait_for_timeout(min(100, int(spec.budget.action_timeout_seconds * 1000)))
        elif proposal.action_type == "open":
            raise BrowserPolicyError("open is only valid as the initial session action")
        elif proposal.action_type == "finish":
            return ActionReceipt(
                action_id=proposal.action_id,
                observation_id=proposal.observation_id,
                result="APPLIED",
                before_observation_id=before.observation_id,
                stop_reason="agent_finished",
            )
    except BrowserPolicyError as error:
        code = "STALE_OBSERVATION" if "stale" in str(error) else "ACTION_REJECTED"
        return ActionReceipt(
            action_id=proposal.action_id,
            observation_id=proposal.observation_id,
            result="REJECTED",
            error_code=code,
            before_observation_id=before.observation_id,
            stop_reason=code,
        )
    except Exception as error:
        return ActionReceipt(
            action_id=proposal.action_id,
            observation_id=proposal.observation_id,
            result="FAILED",
            error_code=type(error).__name__.upper(),
            before_observation_id=before.observation_id,
            stop_reason="browser_action_failed",
        )
    return ActionReceipt(
        action_id=proposal.action_id,
        observation_id=proposal.observation_id,
        result="APPLIED",
        before_observation_id=before.observation_id,
    )


def _fields(page: Any) -> dict[str, str | None]:
    return {
        field: page.locator(f'[data-field="{field}"]').inner_text()
        if page.locator(f'[data-field="{field}"]').count() == 1
        else None
        for field in ("purchase_order", "supplier", "status", "currency")
    }


def run_dom_task(base_url: str, spec: TaskSpec) -> DomRun:
    """Run one deterministic DOM task in a fresh Playwright browser context."""

    if spec.mode not in {"dom", "aria"}:
        raise ValueError("run_dom_task requires a DOM or ARIA TaskSpec")
    if spec.data_source != "synthetic":
        raise BrowserPolicyError(
            "generic DOM/ARIA runner accepts synthetic data only; use the ERP readonly adapter"
        )
    origin = _origin(base_url)
    started = monotonic()
    sync_playwright = _playwright_sync()
    observations: list[Observation] = []
    receipts: list[ActionReceipt] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        policy = BrowserSecurityPolicy(origin=origin)
        context = browser.new_context(service_workers="block", accept_downloads=False)

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

        def on_dialog(dialog: Any) -> None:
            browser_events.append("DIALOG_DISMISSED")
            dialog.dismiss()

        context.on("page", on_popup)
        page.on("download", on_download)
        page.on("dialog", on_dialog)
        try:
            page.goto(
                f"{origin}/?scenario={spec.scenario}",
                wait_until="domcontentloaded",
                timeout=int(spec.budget.action_timeout_seconds * 1000),
            )
            snapshot = _snapshot(page, spec, spec.mode)
            observations.append(snapshot.observation)
            terminal = _terminal_page_state(page)
            if terminal is not None:
                status, stop_reason = terminal
                security_violations = tuple(policy.violations + browser_events)
                result = TaskResult(
                    case_id=spec.case_id,
                    status=status,
                    evidence_refs=tuple(item.observation_id for item in observations),
                    actions=tuple(receipts),
                    stop_reason=stop_reason,
                )
                return DomRun(result, tuple(observations), security_violations)
            try:
                wait_for_ready(
                    page,
                    timeout_ms=spec.budget.action_timeout_seconds * 1000,
                    scenario=spec.scenario,
                )
            except RecoveryFailure as failure:
                security_violations = tuple(policy.violations + browser_events)
                result = TaskResult(
                    case_id=spec.case_id,
                    status="FAILED",
                    evidence_refs=tuple(item.observation_id for item in observations),
                    actions=tuple(receipts),
                    stop_reason=failure.code,
                )
                return DomRun(result, tuple(observations), security_violations)
            if len(receipts) >= spec.budget.max_actions:
                raise BrowserPolicyError("action budget exceeded")
            search = ActionProposal(
                action_type="search",
                observation_id=snapshot.observation.observation_id,
                target_ref="search-input",
                text=spec.purchase_order,
            )
            search_receipt = _apply_action(page, search, snapshot, spec)
            receipts.append(search_receipt)
            if search_receipt.result != "APPLIED":
                security_violations = tuple(policy.violations + browser_events)
                result = TaskResult(
                    case_id=spec.case_id,
                    status="FAILED",
                    evidence_refs=tuple(item.observation_id for item in observations),
                    actions=tuple(receipts),
                    stop_reason=search_receipt.error_code,
                )
                return DomRun(result, tuple(observations), security_violations)
            try:
                wait_for_ready(
                    page,
                    timeout_ms=spec.budget.action_timeout_seconds * 1000,
                    scenario=spec.scenario,
                )
            except RecoveryFailure as failure:
                security_violations = tuple(policy.violations + browser_events)
                result = TaskResult(
                    case_id=spec.case_id,
                    status="FAILED",
                    evidence_refs=tuple(item.observation_id for item in observations),
                    actions=tuple(receipts),
                    stop_reason=failure.code,
                )
                return DomRun(result, tuple(observations), security_violations)
            snapshot = _snapshot(page, spec, spec.mode)
            observations.append(snapshot.observation)
            receipts[-1] = receipts[-1].model_copy(
                update={"after_observation_id": snapshot.observation.observation_id}
            )
            terminal = _terminal_page_state(page)
            if terminal is not None:
                status, stop_reason = terminal
                security_violations = tuple(policy.violations + browser_events)
                result = TaskResult(
                    case_id=spec.case_id,
                    status=status,
                    evidence_refs=tuple(item.observation_id for item in observations),
                    actions=tuple(receipts),
                    stop_reason=stop_reason,
                )
                return DomRun(result, tuple(observations), security_violations)
            target = f"order:{spec.purchase_order}"
            if target not in snapshot.targets:
                status = "NOT_FOUND"
            else:
                click = ActionProposal(
                    action_type="click",
                    observation_id=snapshot.observation.observation_id,
                    target_ref=target,
                )
                click_receipt = _apply_action(page, click, snapshot, spec)
                receipts.append(click_receipt)
                if click_receipt.result != "APPLIED":
                    security_violations = tuple(policy.violations + browser_events)
                    result = TaskResult(
                        case_id=spec.case_id,
                        status="FAILED",
                        evidence_refs=tuple(item.observation_id for item in observations),
                        actions=tuple(receipts),
                        stop_reason=click_receipt.error_code,
                    )
                    return DomRun(result, tuple(observations), security_violations)
                snapshot = _snapshot(page, spec, spec.mode)
                observations.append(snapshot.observation)
                receipts[-1] = receipts[-1].model_copy(
                    update={"after_observation_id": snapshot.observation.observation_id}
                )
                terminal = _terminal_page_state(page)
                if terminal is not None:
                    status, stop_reason = terminal
                    security_violations = tuple(policy.violations + browser_events)
                    result = TaskResult(
                        case_id=spec.case_id,
                        status=status,
                        evidence_refs=tuple(item.observation_id for item in observations),
                        actions=tuple(receipts),
                        stop_reason=stop_reason,
                    )
                    return DomRun(result, tuple(observations), security_violations)
                values = _fields(page)
                status = "SUCCEEDED" if all(values.values()) else "INCOMPLETE"
            elapsed = int((monotonic() - started) * 1000)
            if elapsed > spec.budget.wall_time_seconds * 1000:
                status = "BUDGET_EXCEEDED"
            security_violations = tuple(policy.violations + browser_events)
            if security_violations:
                status = "FAILED"
            result = TaskResult(
                case_id=spec.case_id,
                status=status,
                fields=_fields(page) if status == "SUCCEEDED" else {},
                evidence_refs=tuple(observation.observation_id for observation in observations),
                observation_complete=status == "SUCCEEDED",
                actions=tuple(receipts),
                stop_reason=None if status == "SUCCEEDED" else "target_not_found_or_incomplete",
            )
        finally:
            context.close()
            browser.close()
    return DomRun(
        result=result,
        observations=tuple(observations),
        security_violations=security_violations,
    )


def run_security_probe(base_url: str, scenario: str) -> tuple[str, ...]:
    """Exercise one fixture hazard and return recorded security events."""

    if scenario not in {"external", "popup", "download", "write", "confirm"}:
        raise ValueError("unknown security scenario")
    origin = _origin(base_url)
    sync_playwright = _playwright_sync()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        policy = BrowserSecurityPolicy(origin=origin)
        context = browser.new_context(service_workers="block", accept_downloads=False)

        def route_handler(route: Any) -> None:
            request = route.request
            if policy.permits(request.url, request.method):
                route.continue_()
            else:
                route.abort(error_code="blockedbyclient")

        context.route("**/*", route_handler)
        page = context.new_page()
        events: list[str] = []

        def on_popup(popup: Any) -> None:
            events.append("POPUP_BLOCKED")
            popup.close()

        def on_download(download: Any) -> None:
            events.append("DOWNLOAD_BLOCKED")

        def on_dialog(dialog: Any) -> None:
            events.append("DIALOG_DISMISSED")
            dialog.dismiss()

        context.on("page", on_popup)
        page.on("download", on_download)
        page.on("dialog", on_dialog)
        try:
            page.goto(
                f"{origin}/?scenario={scenario}",
                wait_until="domcontentloaded",
                timeout=10_000,
            )
            if scenario in {"external", "popup", "download", "confirm"}:
                try:
                    page.locator(f'[data-security="{scenario}"]').click(timeout=1_000)
                except Exception:
                    pass
            else:
                snapshot = _snapshot(page, TaskSpec(case_id="security", purchase_order="x"))
                proposal = ActionProposal(
                    action_type="click",
                    observation_id=snapshot.observation.observation_id,
                    target_ref="security:write",
                )
                try:
                    _validate_action(proposal, snapshot)
                except BrowserPolicyError:
                    events.append("WRITE_ACTION_REJECTED")
        finally:
            context.close()
            browser.close()
    return tuple(policy.violations + events)


def run_aria_task(base_url: str, spec: TaskSpec) -> DomRun:
    """Run one read task using the browser accessibility snapshot."""

    if spec.mode != "aria":
        raise ValueError("run_aria_task requires an ARIA TaskSpec")
    return run_dom_task(base_url, spec)


__all__ = [
    "BrowserPolicyError",
    "BrowserUnavailable",
    "DomRun",
    "DomSnapshot",
    "run_aria_task",
    "run_dom_task",
    "run_security_probe",
]
