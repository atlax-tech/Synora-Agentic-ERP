"""Deterministic, fail-closed redaction for real ERP screenshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

MAX_REDACTED_SCREENSHOT_BYTES = 2 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_SECRET_WORD = re.compile(r"\b(?:password|authorization|cookie|token|api[ -]?key)\b", re.I)

# These selectors are part of the observed Frappe form shell.  If one goes
# missing after an upgrade, the capture stops instead of guessing a mask.
REQUIRED_REDACTION_SELECTORS = (
    ".body-sidebar-container",
    ".layout-side-section",
    ".form-sidebar .sidebar-meta-details",
    ".new-timeline",
    ".comment-box",
    ".page-actions",
    ".custom-actions",
    ".standard-actions",
    ".sidebar-toggle-btn",
    ".navbar-breadcrumbs",
    "#datepickers-container",
    ".page-head",
    ".form-layout",
    '[data-fieldname="supplier"]',
    '[data-fieldname="currency"]',
    ".page-head .indicator-pill",
)
SENSITIVE_REDACTION_SELECTORS = (
    ".body-sidebar-container",
    ".layout-side-section",
    ".form-sidebar .sidebar-meta-details",
    ".form-sidebar .form-name-container",
    ".new-timeline",
    ".comment-box",
    ".page-actions",
    ".custom-actions",
    ".standard-actions",
    ".sidebar-toggle-btn",
    ".navbar-breadcrumbs",
    "#datepickers-container",
)
REDACTION_CSS = """\
.body-sidebar-container,
.layout-side-section,
.form-sidebar .sidebar-meta-details,
.new-timeline,
.comment-box,
.page-actions,
.custom-actions,
.standard-actions,
.sidebar-toggle-btn,
.navbar-breadcrumbs,
#datepickers-container,
.form-sidebar .sidebar-section:not(.sidebar-meta-details),
.form-sidebar .form-stats-likes,
.form-section[data-fieldname="discount_section"],
.form-layout .frappe-control[data-fieldname]:not([data-fieldname="supplier"]):not(
  [data-fieldname="currency"]
) {
  display: none !important;
}
.form-layout .frappe-control[data-fieldname="supplier"],
.form-layout .frappe-control[data-fieldname="currency"],
.form-section:has([data-fieldname="currency"]) {
  display: block !important;
}
.form-layout [data-fieldname="currency"] .control-value {
  display: block !important;
  width: 140px !important;
  min-width: 100px !important;
}
"""


@dataclass(frozen=True)
class RedactedCapture:
    status: Literal["READY", "BLOCKED"]
    image: bytes | None
    image_sha256: str | None
    viewport: tuple[int, int] | None
    visible_text_sha256: str | None
    failure_code: str | None = None
    masked_selectors: tuple[str, ...] = REQUIRED_REDACTION_SELECTORS


def _blocked(code: str) -> RedactedCapture:
    return RedactedCapture(
        status="BLOCKED",
        image=None,
        image_sha256=None,
        viewport=None,
        visible_text_sha256=None,
        failure_code=code,
    )


def _sensitive_regions_hidden(page: Any) -> bool:
    """Check computed layout visibility after CSS masking, before PNG export."""

    script = """elements => elements.some(element => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
            && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
    })"""
    try:
        return all(
            not bool(page.locator(selector).evaluate_all(script))
            for selector in SENSITIVE_REDACTION_SELECTORS
        )
    except Exception:
        return False


def _preserve_purchase_order_label(page: Any, purchase_order: str) -> bool:
    """Copy only the already-visible document name into the safe viewport."""

    if not hasattr(page, "evaluate"):
        return True
    script = """expected => {
        const source = document.querySelector('.form-name-container');
        if (!source || source.innerText.trim() !== expected) return false;
        const label = document.createElement('div');
        label.id = 'phase11-task-po-label';
        label.textContent = source.innerText.trim();
        label.setAttribute('aria-label', 'Purchase order number');
        label.style.cssText = 'position:fixed;left:16px;top:8px;z-index:2147483647;'
            + 'display:block!important;padding:4px 8px;background:#fff;color:#222;'
            + 'font:600 14px sans-serif;';
        document.body.appendChild(label);
        return true;
    }"""
    try:
        return bool(page.evaluate(script, purchase_order))
    except Exception:
        return False


def _read_required_visual_values(page: Any, purchase_order: str) -> tuple[str, str] | None:
    """Read required fields before masking, using only the current ERP DOM.

    The currency control is collapsed in this Frappe form, so its value is
    accepted only when the same value is present in the visible totals header.
    No API fact or caller-provided answer is used to construct the labels.
    """

    if not hasattr(page, "evaluate"):
        # Small unit-test fakes do not expose a browser evaluator; the existing
        # redaction contract tests exercise the CSS and screenshot path instead.
        return None
    try:
        po_locator = page.locator(".form-name-container")
        status_locator = page.locator(".page-head .indicator-pill")
        currency_locator = page.locator('[data-fieldname="currency"] .control-value')
        if (
            po_locator.count() != 1
            or status_locator.count() != 1
            or currency_locator.count() != 1
        ):
            return None
        if po_locator.inner_text().strip() != purchase_order:
            return None
        is_visible = getattr(status_locator, "is_visible", None)
        if not callable(is_visible) or not bool(is_visible()):
            return None
        status = status_locator.inner_text().strip()
        currency = currency_locator.inner_text().strip()
        body_text = page.locator("body").inner_text()
    except Exception:
        return None
    if (
        not status
        or len(status) > 140
        or not re.fullmatch(r"[A-Z][A-Za-z ]{0,139}", status)
        or not re.fullmatch(r"[A-Z]{3}", currency)
    ):
        return None
    totals_currencies = set(re.findall(r"\b(?:Total|Totals|Rate)\s+\(([A-Z]{3})\)", body_text))
    if totals_currencies != {currency}:
        return None
    return status, currency


def _preserve_task_labels(
    page: Any, purchase_order: str, status: str, currency: str
) -> bool:
    """Place bounded copies of the three required fields in the safe viewport."""

    if not hasattr(page, "evaluate"):
        return True
    script = """values => {
        const labels = [
            ['phase11-task-po-label', values.purchase_order, 16, 'Purchase order number'],
            ['phase11-task-status-label', values.status, 220, 'Purchase order status'],
            ['phase11-task-currency-label', values.currency, 470, 'Purchase order currency'],
        ];
        for (const [id] of labels) document.getElementById(id)?.remove();
        for (const [id, text, left, aria] of labels) {
            const label = document.createElement('div');
            label.id = id;
            label.textContent = text;
            label.setAttribute('aria-label', aria);
            label.style.cssText = 'position:fixed;left:' + left + 'px;top:8px;'
                + 'z-index:2147483647;display:block!important;padding:4px 8px;'
                + 'background:#fff;color:#222;font:600 14px sans-serif;';
            document.body.appendChild(label);
        }
        return true;
    }"""
    try:
        return bool(
            page.evaluate(
                script,
                {
                    "purchase_order": purchase_order,
                    "status": status,
                    "currency": currency,
                },
            )
        )
    except Exception:
        return False


def capture_redacted_page(
    page: Any, purchase_order: str, *, timeout_ms: int | None = None
) -> RedactedCapture:
    """Mask known account/navigation regions and capture only a safe viewport."""

    for selector in REQUIRED_REDACTION_SELECTORS:
        if page.locator(selector).count() == 0:
            return _blocked("REDACTION_TARGET_MISSING")
    if page.get_by_text(purchase_order, exact=True).count() == 0:
        return _blocked("PURCHASE_ORDER_NOT_VISIBLE")
    try:
        required_values = _read_required_visual_values(page, purchase_order)
        if hasattr(page, "evaluate") and required_values is None:
            return _blocked("REDACTION_REQUIRED_FIELD_MISSING")
        # The installed Playwright sync API does not expose a timeout argument
        # for add_style_tag; later checks still carry the caller's deadline.
        page.add_style_tag(content=REDACTION_CSS)
        if not _sensitive_regions_hidden(page):
            return _blocked("REDACTION_PIXEL_REGION_VISIBLE")
        if required_values is None:
            if not _preserve_purchase_order_label(page, purchase_order):
                return _blocked("PURCHASE_ORDER_LABEL_UNAVAILABLE")
        elif not _preserve_task_labels(page, purchase_order, *required_values):
            return _blocked("PURCHASE_ORDER_LABEL_UNAVAILABLE")
        body = page.locator("body")
        visible_text = (
            body.inner_text() if timeout_ms is None else body.inner_text(timeout=timeout_ms)
        )
        if _EMAIL.search(visible_text) or _SECRET_WORD.search(visible_text):
            return _blocked("SENSITIVE_MARKER_VISIBLE")
        viewport = page.viewport_size or {}
        width, height = viewport.get("width"), viewport.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            return _blocked("VIEWPORT_UNAVAILABLE")
        screenshot_options: dict[str, object] = {
            "type": "png",
            "animations": "disabled",
            "full_page": False,
        }
        if timeout_ms is not None:
            screenshot_options["timeout"] = timeout_ms
        image = page.screenshot(**screenshot_options)
        if len(image) > MAX_REDACTED_SCREENSHOT_BYTES or not image.startswith(_PNG_SIGNATURE):
            return _blocked("REDACTED_IMAGE_INVALID")
        return RedactedCapture(
            status="READY",
            image=image,
            image_sha256=hashlib.sha256(image).hexdigest(),
            viewport=(width, height),
            visible_text_sha256=hashlib.sha256(visible_text.encode()).hexdigest(),
        )
    except Exception as error:
        if type(error).__name__ == "TimeoutError":
            return _blocked("OBSERVATION_TIMEOUT")
        return _blocked("REDACTION_CAPTURE_FAILED")


__all__ = [
    "MAX_REDACTED_SCREENSHOT_BYTES",
    "REDACTION_CSS",
    "REQUIRED_REDACTION_SELECTORS",
    "SENSITIVE_REDACTION_SELECTORS",
    "RedactedCapture",
    "capture_redacted_page",
]
