"""Bounded DOM browser tasks for the Phase 11 synthetic page.

The browser is deliberately driven by a small allowlisted policy.  A future
model adapter may propose the same typed actions, but it cannot add selectors,
JavaScript, URLs, or arbitrary browser commands.
"""

from __future__ import annotations

import hashlib
import json
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


def _snapshot(page: Any, spec: TaskSpec) -> DomSnapshot:
    body = page.locator("body")
    version = body.get_attribute("data-page-version") or "unknown"
    text = body.inner_text(timeout=spec.budget.action_timeout_seconds * 1000)
    if len(text) > 50_000:
        raise BrowserPolicyError("observation is too large")
    targets: set[str] = set()
    if page.locator("#order-search").count() == 1:
        targets.add("search-input")
    if page.locator('[data-action="search"]').count() == 1:
        targets.add("search-submit")
    if page.locator('[data-action="back"]').count() == 1:
        targets.add("back")
    for name in page.locator("[data-order-name]").evaluate_all(
        "elements => elements.map(element => element.getAttribute('data-order-name'))"
    ):
        if isinstance(name, str) and name:
            targets.add(f"order:{name}")
    content = json.dumps(
        {"text": text, "targets": sorted(targets)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return DomSnapshot(
        observation=Observation(
            page_version=version,
            source=spec.data_source,
            mode="dom",
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


def _locator_for(page: Any, target_ref: str) -> Any:
    if target_ref == "search-input":
        return page.locator("#order-search")
    if target_ref == "search-submit":
        return page.locator('[data-action="search"]')
    if target_ref == "back":
        return page.locator('[data-action="back"]')
    if target_ref.startswith("order:"):
        name = target_ref.removeprefix("order:")
        return page.locator(f'[data-order-name="{name}"] [data-order-link]')
    raise BrowserPolicyError("unknown observed target")


def _apply_action(
    page: Any, proposal: ActionProposal, snapshot: DomSnapshot, spec: TaskSpec
) -> ActionReceipt:
    _validate_action(proposal, snapshot)
    before = snapshot.observation
    try:
        if proposal.action_type == "search":
            locator = _locator_for(page, "search-input")
            locator.fill(proposal.text or "", timeout=spec.budget.action_timeout_seconds * 1000)
            page.locator('[data-action="search"]').click(
                timeout=spec.budget.action_timeout_seconds * 1000
            )
            page.wait_for_load_state(
                "domcontentloaded", timeout=spec.budget.action_timeout_seconds * 1000
            )
        elif proposal.action_type == "click":
            locator = _locator_for(page, proposal.target_ref or "")
            if locator.count() != 1:
                raise BrowserPolicyError("observed target is not unique")
            locator.click(timeout=spec.budget.action_timeout_seconds * 1000)
            page.wait_for_load_state(
                "domcontentloaded", timeout=spec.budget.action_timeout_seconds * 1000
            )
        elif proposal.action_type == "scroll":
            page.mouse.wheel(0, 500)
        elif proposal.action_type == "wait":
            page.wait_for_timeout(100)
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
    except BrowserPolicyError:
        raise
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

    if spec.mode != "dom":
        raise ValueError("run_dom_task requires a DOM TaskSpec")
    origin = _origin(base_url)
    started = monotonic()
    sync_playwright = _playwright_sync()
    observations: list[Observation] = []
    receipts: list[ActionReceipt] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(service_workers="block")
        page = context.new_page()
        try:
            page.goto(f"{origin}/", wait_until="domcontentloaded", timeout=10_000)
            snapshot = _snapshot(page, spec)
            observations.append(snapshot.observation)
            if len(receipts) >= spec.budget.max_actions:
                raise BrowserPolicyError("action budget exceeded")
            search = ActionProposal(
                action_type="search",
                observation_id=snapshot.observation.observation_id,
                target_ref="search-input",
                text=spec.purchase_order,
            )
            receipts.append(_apply_action(page, search, snapshot, spec))
            snapshot = _snapshot(page, spec)
            observations.append(snapshot.observation)
            target = f"order:{spec.purchase_order}"
            if target not in snapshot.targets:
                status: TaskStatus = "NOT_FOUND"
            else:
                click = ActionProposal(
                    action_type="click",
                    observation_id=snapshot.observation.observation_id,
                    target_ref=target,
                )
                receipts.append(_apply_action(page, click, snapshot, spec))
                snapshot = _snapshot(page, spec)
                observations.append(snapshot.observation)
                values = _fields(page)
                status = "SUCCEEDED" if all(values.values()) else "INCOMPLETE"
            elapsed = int((monotonic() - started) * 1000)
            if elapsed > spec.budget.wall_time_seconds * 1000:
                status = "BUDGET_EXCEEDED"
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
    return DomRun(result=result, observations=tuple(observations))


__all__ = ["BrowserPolicyError", "BrowserUnavailable", "DomRun", "DomSnapshot", "run_dom_task"]
