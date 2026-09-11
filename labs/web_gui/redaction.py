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
    ".form-sidebar .form-name-container",
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
REDACTION_CSS = """\
.body-sidebar-container,
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


def capture_redacted_page(page: Any, purchase_order: str) -> RedactedCapture:
    """Mask known account/navigation regions and capture only a safe viewport."""

    for selector in REQUIRED_REDACTION_SELECTORS:
        if page.locator(selector).count() == 0:
            return _blocked("REDACTION_TARGET_MISSING")
    if page.get_by_text(purchase_order, exact=True).count() == 0:
        return _blocked("PURCHASE_ORDER_NOT_VISIBLE")
    try:
        page.add_style_tag(content=REDACTION_CSS)
        visible_text = page.locator("body").inner_text()
        if _EMAIL.search(visible_text) or _SECRET_WORD.search(visible_text):
            return _blocked("SENSITIVE_MARKER_VISIBLE")
        viewport = page.viewport_size or {}
        width, height = viewport.get("width"), viewport.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            return _blocked("VIEWPORT_UNAVAILABLE")
        image = page.screenshot(type="png", animations="disabled", full_page=False)
        if len(image) > MAX_REDACTED_SCREENSHOT_BYTES or not image.startswith(_PNG_SIGNATURE):
            return _blocked("REDACTED_IMAGE_INVALID")
        return RedactedCapture(
            status="READY",
            image=image,
            image_sha256=hashlib.sha256(image).hexdigest(),
            viewport=(width, height),
            visible_text_sha256=hashlib.sha256(visible_text.encode()).hexdigest(),
        )
    except Exception:
        return _blocked("REDACTION_CAPTURE_FAILED")


__all__ = [
    "MAX_REDACTED_SCREENSHOT_BYTES",
    "REDACTION_CSS",
    "REQUIRED_REDACTION_SELECTORS",
    "RedactedCapture",
    "capture_redacted_page",
]
