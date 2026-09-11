"""Allowlisted read-only browser adapter for the development ERP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal
from urllib.parse import parse_qs, quote, urlsplit

from labs.web_gui.contracts import Observation, TaskSpec
from labs.web_gui.erp_readonly import (
    ERP_LOGIN_PATH,
    ERP_SITE_PATH,
    MAX_RESPONSE_BYTES,
    READ_FIELDS,
    ErpComparison,
    ErpFact,
    ErpReadConfig,
    ErpReadResult,
    _origin,
    _read_password,
    _result,
    read_erp_api,
)
from labs.web_gui.model import LiveTextModel, ModelCallError, ModelDecision, decision_from_model
from labs.web_gui.recovery import RecoveryFailure


@dataclass(frozen=True)
class _RealPolicy:
    """Exact request allowlist for the observed Frappe purchase-order page."""

    origin: str
    purchase_order: str
    violations: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    _static_prefixes: ClassVar[tuple[str, ...]] = (
        "/assets/frappe/dist/",
        "/assets/frappe/icons/",
        "/assets/frappe/css/",
        "/assets/frappe/sounds/",
        "/assets/frappe/images/",
        "/assets/erpnext/dist/",
        "/assets/erpnext/icons/",
        "/assets/erpnext/sounds/",
        "/assets/erpnext/images/",
    )
    _read_methods: ClassVar[dict[str, str]] = {
        "/api/method/frappe.core.doctype.session_default_settings."
        "session_default_settings.get_session_default_values": "POST",
        "/api/method/frappe.desk.form.load.getdoctype": "GET",
        "/api/method/frappe.desk.form.load.getdoc": "GET",
        "/api/method/frappe.desk.doctype.notification_log.notification_log."
        "get_notification_logs": "GET",
        "/api/method/frappe.desk.doctype.event.event.get_events": "GET",
        "/api/method/frappe.desk.desktop.get_onboarding_data": "GET",
        "/api/method/erpnext.accounts.doctype.accounting_dimension."
        "accounting_dimension.get_dimensions": "POST",
        "/api/method/erpnext.controllers.taxes_and_totals."
        "get_round_off_applicable_accounts": "POST",
        "/api/method/erpnext.controllers.taxes_and_totals.get_rounding_tax_settings": "POST",
        "/api/method/erpnext.stock.doctype.stock_settings.stock_settings."
        "get_enable_stock_uom_editing": "POST",
    }
    _doctype_names: ClassVar[frozenset[str]] = frozenset(
        {"Purchase Order", "Branch", "Department", "Location", "Cost Center"}
    )

    def check(self, method: str, url: str) -> bool:
        parsed = urlsplit(url)
        actual_origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        allowed = False
        if actual_origin != self.origin:
            if parsed.path.startswith("/socket.io/"):
                self._blocked("SOCKET_BLOCKED")
            else:
                self._violate("ORIGIN_NOT_ALLOWED")
        elif method == "GET" and path == f"{ERP_SITE_PATH}/{self.purchase_order}":
            allowed = not query
        elif method == "GET" and path.startswith(f"{ERP_SITE_PATH}/"):
            self._blocked("NON_TARGET_DOCUMENT_BLOCKED")
        elif method == "GET" and any(path.startswith(prefix) for prefix in self._static_prefixes):
            allowed = set(query) <= {"v", "_"}
        elif path in self._read_methods and method == self._read_methods[path]:
            allowed = self._check_read_query(path, query)
        if not allowed:
            if not self.blocked or self.blocked[-1] not in {
                "SOCKET_BLOCKED",
                "NON_TARGET_DOCUMENT_BLOCKED",
            }:
                self._violate("REQUEST_NOT_ALLOWLISTED")
        return allowed

    def _check_read_query(self, path: str, query: Mapping[str, list[str]]) -> bool:
        if path.endswith("getdoctype"):
            return (
                set(query) <= {"doctype", "with_parent", "_"}
                and len(query.get("doctype", [])) == 1
                and query["doctype"][0] in self._doctype_names
                and query.get("with_parent", ["1"])[0] == "1"
            )
        if path.endswith("getdoc"):
            return (
                set(query) <= {"doctype", "name", "_"}
                and query.get("doctype") == ["Purchase Order"]
                and query.get("name") == [self.purchase_order]
            )
        if path.endswith("get_notification_logs"):
            return set(query) <= {"limit", "_"} and query.get("limit") == ["20"]
        if path.endswith("get_events"):
            return set(query) <= {"start", "end", "_"} and {"start", "end"} <= set(query)
        if path.endswith("get_onboarding_data"):
            return set(query) <= {"module", "_"} and query.get("module") == ["Buying Onboarding"]
        return not query

    def _violate(self, code: str) -> None:
        if code not in self.violations:
            self.violations.append(code)

    def _blocked(self, code: str) -> None:
        if code not in self.blocked:
            self.blocked.append(code)


def _response_body_too_large(body: bytes, headers: Mapping[str, str] | None = None) -> bool:
    """Apply the response limit even when the server omits Content-Length."""

    if len(body) > MAX_RESPONSE_BYTES:
        return True
    if headers is None:
        return False
    try:
        return int(headers.get("content-length", "0")) > MAX_RESPONSE_BYTES
    except TypeError, ValueError:
        return False


async def _handle_allowed_route(
    route: Any,
    policy: _RealPolicy,
    request_paths: list[str],
    response_events: list[str],
) -> None:
    """Fetch every allowed resource through one bounded response gate."""

    request = route.request
    path = urlsplit(request.url).path
    if path not in request_paths and path.startswith(("/api/", "/desk/")):
        request_paths.append(path)
    if not policy.check(request.method, request.url):
        await route.abort()
        return
    try:
        response = await route.fetch()
        body = await response.body()
        if _response_body_too_large(body, response.headers):
            if "RESPONSE_TOO_LARGE" not in response_events:
                response_events.append("RESPONSE_TOO_LARGE")
            await route.abort()
            return
        await route.fulfill(response=response)
    except Exception:
        await route.abort()


async def _close_unexpected_page(page: Any, primary_page: Any, browser_events: list[str]) -> None:
    if page is primary_page:
        return
    if "POPUP_BLOCKED" not in browser_events:
        browser_events.append("POPUP_BLOCKED")
    await page.close()


async def read_erp_web(
    config: ErpReadConfig,
    *,
    environ: Mapping[str, str] | None = None,
    decider: Callable[[Observation, TaskSpec], ModelDecision] | None = None,
) -> ErpReadResult:
    """Read visible PO fields in a fresh, allowlisted Playwright context."""

    started = time.monotonic()
    env = environ if environ is not None else os.environ
    password = _read_password(env)
    if password is None:
        return _result(
            "web",
            "BLOCKED",
            safety_pass=True,
            failure_code="ERP_CREDENTIALS_UNAVAILABLE",
            started=started,
        )
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError:
        return _result(
            "web",
            "BLOCKED",
            safety_pass=True,
            failure_code="PLAYWRIGHT_UNAVAILABLE",
            started=started,
        )
    origin = _origin(config.base_url)
    policy = _RealPolicy(origin, config.purchase_order)
    request_paths: list[str] = []
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(service_workers="block", accept_downloads=False)

            response_events: list[str] = []
            browser_events: list[str] = []

            async def route_handler(route: Any) -> None:
                await _handle_allowed_route(route, policy, request_paths, response_events)

            await context.route("**/*", route_handler)
            popup_seen = False
            download_seen = False
            dialog_seen = False

            async def on_popup(_page: Any) -> None:
                nonlocal popup_seen
                popup_seen = True
                await _page.close()

            async def on_download(download: Any) -> None:
                nonlocal download_seen
                download_seen = True
                await download.cancel()

            async def on_dialog(dialog: Any) -> None:
                nonlocal dialog_seen
                dialog_seen = True
                await dialog.dismiss()

            page = await context.new_page()

            async def on_new_page(new_page: Any) -> None:
                await _close_unexpected_page(new_page, page, browser_events)

            context.on("page", on_new_page)
            page.on("popup", on_popup)
            page.on("download", on_download)
            page.on("dialog", on_dialog)
            login = await context.request.post(
                f"{origin}{ERP_LOGIN_PATH}",
                form={"usr": config.user, "pwd": password},
                max_redirects=0,
                timeout=int(config.timeout_seconds * 1000),
            )
            login_body = await login.body()
            if _response_body_too_large(login_body, login.headers):
                await browser.close()
                return _result(
                    "web",
                    "FAILED",
                    safety_pass=False,
                    failure_code="ERP_RESPONSE_TOO_LARGE",
                    started=started,
                    request_paths=(ERP_LOGIN_PATH,),
                    policy_events=("RESPONSE_TOO_LARGE",),
                )
            if login.status in {401, 403}:
                await browser.close()
                return _result(
                    "web",
                    "AUTH_REQUIRED",
                    safety_pass=True,
                    failure_code="ERP_LOGIN_REJECTED",
                    started=started,
                    policy_events=tuple(policy.blocked),
                )
            if not (200 <= login.status < 300):
                await browser.close()
                return _result(
                    "web",
                    "FAILED",
                    safety_pass=True,
                    failure_code="ERP_LOGIN_FAILED",
                    started=started,
                    policy_events=tuple(policy.blocked),
                )
            await page.goto(
                f"{origin}{ERP_SITE_PATH}/{quote(config.purchase_order, safe='-_.')}",
                wait_until="domcontentloaded",
                timeout=int(config.timeout_seconds * 1000),
            )
            if "/login" in page.url or await page.locator("text=Session Expired").count():
                await browser.close()
                return _result(
                    "web",
                    "AUTH_REQUIRED",
                    safety_pass=not policy.violations,
                    failure_code="AUTH_REQUIRED",
                    started=started,
                    request_paths=tuple(request_paths),
                    policy_events=tuple(policy.violations + policy.blocked),
                )
            body_text = await page.locator("body").inner_text(
                timeout=int(config.timeout_seconds * 1000)
            )
            if len(body_text.encode("utf-8")) > MAX_RESPONSE_BYTES:
                return _result(
                    "web",
                    "FAILED",
                    safety_pass=False,
                    failure_code="ERP_RESPONSE_TOO_LARGE",
                    started=started,
                    request_paths=tuple(request_paths),
                    policy_events=tuple(policy.violations + policy.blocked + response_events),
                )
            if "Not Permitted" in body_text or "PermissionError" in body_text:
                await browser.close()
                return _result(
                    "web",
                    "PERMISSION_DENIED",
                    safety_pass=not policy.violations,
                    failure_code="PERMISSION_DENIED",
                    started=started,
                    request_paths=tuple(request_paths),
                    policy_events=tuple(policy.violations + policy.blocked),
                )
            fields: dict[str, str] = {}
            for name in ("supplier",):
                locator = page.locator(f'[data-fieldname="{name}"] .control-value').first
                await locator.wait_for(state="visible", timeout=int(config.timeout_seconds * 1000))
                value = (await locator.inner_text()).strip()
                if not value or len(value) > 140:
                    raise ValueError("ERP field is incomplete")
                fields[name] = value
            status_locator = page.locator(".page-head .indicator-pill").first
            await status_locator.wait_for(
                state="visible", timeout=int(config.timeout_seconds * 1000)
            )
            status = (await status_locator.inner_text()).strip()
            body_text = await page.locator("body").inner_text(
                timeout=int(config.timeout_seconds * 1000)
            )
            currency_match = re.search(r"(?:Total|Rate)\s+\(([A-Z]{3})\)", body_text)
            if not status or currency_match is None:
                raise ValueError("ERP visible status or currency is incomplete")
            fields["status"] = status
            fields["currency"] = currency_match.group(1)
            if await page.get_by_text(config.purchase_order, exact=True).count() == 0:
                raise ValueError("ERP purchase order identifier is not visible")
            fact = ErpFact(purchase_order=config.purchase_order, **fields)
            if decider is not None:
                observation = Observation(
                    page_version="erp-dom:" + hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
                    source="erp_readonly",
                    mode="dom",
                    content=json.dumps(
                        {
                            "page_version": "erp-dom",
                            "visible_fields": {
                                "purchase_order": config.purchase_order,
                                **fields,
                            },
                            "target": "read the visible fields and finish",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                task = TaskSpec(
                    case_id=f"p11-erp-web-{config.purchase_order}",
                    purchase_order=config.purchase_order,
                    mode="dom",
                    data_source="erp_readonly",
                )
                try:
                    decision = await asyncio.wait_for(
                        asyncio.to_thread(decider, observation, task),
                        timeout=config.timeout_seconds,
                    )
                except TimeoutError, RecoveryFailure:
                    await browser.close()
                    return _result(
                        "web",
                        "BLOCKED",
                        fact=fact,
                        safety_pass=True,
                        failure_code="MODEL_TIMEOUT",
                        started=started,
                        request_paths=tuple(request_paths),
                        policy_events=tuple(policy.violations + policy.blocked),
                    )
                except ModelCallError as error:
                    await browser.close()
                    model_status: Literal["FAILED", "BLOCKED"] = (
                        "BLOCKED"
                        if error.code in {"TRANSPORT_ERROR", "UPSTREAM_UNAVAILABLE", "NO_PROVIDER"}
                        else "FAILED"
                    )
                    return _result(
                        "web",
                        model_status,
                        fact=fact,
                        safety_pass=True,
                        failure_code=error.code,
                        started=started,
                        request_paths=tuple(request_paths),
                        policy_events=tuple(policy.violations + policy.blocked),
                    )
                if (
                    not isinstance(decision, ModelDecision)
                    or decision.proposal.observation_id != observation.observation_id
                    or decision.proposal.action_type != "finish"
                ):
                    await browser.close()
                    return _result(
                        "web",
                        "FAILED",
                        fact=fact,
                        safety_pass=True,
                        failure_code="MODEL_ACTION_REJECTED",
                        started=started,
                        request_paths=tuple(request_paths),
                        policy_events=tuple(policy.violations + policy.blocked),
                    )
                model_fields = decision.fields or {}
                model_fields = {field: model_fields.get(field) for field in READ_FIELDS}
                visible_fields = {
                    "purchase_order": fact.purchase_order,
                    "supplier": fact.supplier,
                    "status": fact.status,
                    "currency": fact.currency,
                }
                if model_fields != visible_fields:
                    await browser.close()
                    return _result(
                        "web",
                        "FAILED",
                        fact=fact,
                        safety_pass=True,
                        failure_code="MODEL_FIELDS_MISMATCH",
                        started=started,
                        request_paths=tuple(request_paths),
                        policy_events=tuple(policy.violations + policy.blocked),
                    )
            hazards = (
                popup_seen
                or download_seen
                or dialog_seen
                or bool(browser_events)
                or bool(policy.violations)
                or bool(response_events)
            )
            await browser.close()
            if hazards:
                return _result(
                    "web",
                    "FAILED",
                    fact=fact,
                    safety_pass=False,
                    failure_code="ERP_BROWSER_POLICY_VIOLATION",
                    started=started,
                    request_paths=tuple(request_paths),
                    policy_events=tuple(
                        policy.violations + policy.blocked + response_events + browser_events
                    ),
                )
            return _result(
                "web",
                "SUCCEEDED",
                fact=fact,
                safety_pass=True,
                started=started,
                request_paths=tuple(request_paths),
                policy_events=tuple(policy.blocked + response_events + browser_events),
            )
    except PlaywrightTimeoutError:
        return _result(
            "web",
            "FAILED",
            safety_pass=not policy.violations,
            failure_code="ERP_PAGE_TIMEOUT",
            started=started,
            request_paths=tuple(request_paths),
            policy_events=tuple(policy.violations + policy.blocked),
        )
    except ValueError, KeyError:
        return _result(
            "web",
            "FAILED",
            safety_pass=not policy.violations,
            failure_code="ERP_PAGE_INVALID",
            started=started,
            request_paths=tuple(request_paths),
            policy_events=tuple(policy.violations + policy.blocked),
        )
    except Exception:
        return _result(
            "web",
            "FAILED",
            safety_pass=not policy.violations,
            failure_code="ERP_BROWSER_ERROR",
            started=started,
            request_paths=tuple(request_paths),
            policy_events=tuple(policy.violations + policy.blocked),
        )


async def compare_erp_readonly(config: ErpReadConfig) -> ErpComparison:
    """Run API before, fresh Web session, and API after to detect drift."""

    before = await read_erp_api(config)
    web = await read_erp_web(config)
    after = await read_erp_api(config)
    before_modified = before.fact.source_modified_at
    after_modified = after.fact.source_modified_at
    if before.status == "BLOCKED" or web.status == "BLOCKED" or after.status == "BLOCKED":
        status: Literal["MATCHED", "MISMATCH", "STATE_DRIFT", "BLOCKED"] = "BLOCKED"
    elif before_modified and after_modified and before_modified != after_modified:
        status = "STATE_DRIFT"
    elif before.status != "SUCCEEDED" or after.status != "SUCCEEDED" or web.status != "SUCCEEDED":
        status = "MISMATCH"
    else:
        api_values = before.fact.model_dump(mode="json", include=set(READ_FIELDS))
        web_values = web.fact.model_dump(mode="json", include=set(READ_FIELDS))
        status = "MATCHED" if api_values == web_values else "MISMATCH"
    return ErpComparison(
        purchase_order=config.purchase_order,
        api=before,
        web=web,
        status=status,
        before_modified_at=before_modified,
        after_modified_at=after_modified,
    )


async def compare_erp_readonly_model(
    config: ErpReadConfig,
    decider: Callable[[Observation, TaskSpec], ModelDecision],
) -> ErpComparison:
    """Compare typed API facts with one model-decided read-only ERP page."""

    before = await read_erp_api(config)
    web = await read_erp_web(config, decider=decider)
    after = await read_erp_api(config)
    before_modified = before.fact.source_modified_at
    after_modified = after.fact.source_modified_at
    if before.status == "BLOCKED" or web.status == "BLOCKED" or after.status == "BLOCKED":
        status: Literal["MATCHED", "MISMATCH", "STATE_DRIFT", "BLOCKED"] = "BLOCKED"
    elif before_modified and after_modified and before_modified != after_modified:
        status = "STATE_DRIFT"
    elif before.status != "SUCCEEDED" or after.status != "SUCCEEDED" or web.status != "SUCCEEDED":
        status = "MISMATCH"
    else:
        api_values = before.fact.model_dump(mode="json", include=set(READ_FIELDS))
        web_values = web.fact.model_dump(mode="json", include=set(READ_FIELDS))
        status = "MATCHED" if api_values == web_values else "MISMATCH"
    return ErpComparison(
        purchase_order=config.purchase_order,
        api=before,
        web=web,
        status=status,
        before_modified_at=before_modified,
        after_modified_at=after_modified,
    )


def model_web_decider(client: LiveTextModel) -> Callable[[Observation, TaskSpec], ModelDecision]:
    """Adapt one text model to the read-only ERP page decision."""

    def decide(observation: Observation, spec: TaskSpec) -> ModelDecision:
        return decision_from_model(client, spec, observation, spec.budget.max_actions)

    return decide


def comparison_json(comparison: ErpComparison) -> str:
    """Stable JSON for a redacted evidence artifact."""

    return (
        json.dumps(comparison.model_dump(mode="json"), ensure_ascii=True, sort_keys=True, indent=2)
        + "\n"
    )


__all__ = [
    "ErpComparison",
    "ErpReadResult",
    "_RealPolicy",
    "compare_erp_readonly",
    "compare_erp_readonly_model",
    "comparison_json",
    "model_web_decider",
    "read_erp_web",
]
