from __future__ import annotations

from typing import Any, ClassVar

import pytest

from labs.web_gui.redaction import (
    REDACTION_CSS,
    REQUIRED_REDACTION_SELECTORS,
    capture_redacted_page,
)

PNG = b"\x89PNG\r\n\x1a\nsynthetic-redacted"


class _Locator:
    def __init__(self, count: int = 1, *, sensitive_visible: bool = False) -> None:
        self._count = count
        self._sensitive_visible = sensitive_visible

    def count(self) -> int:
        return self._count

    def evaluate_all(self, _script: str) -> bool:
        return self._sensitive_visible


class _TextLocator(_Locator):
    def __init__(self, text: str, *, timeout: bool = False) -> None:
        super().__init__()
        self._text = text
        self._timeout = timeout

    def inner_text(self, **_kwargs: Any) -> str:
        if self._timeout:
            raise TimeoutError("blocked inner_text")
        return self._text


class _Page:
    viewport_size: ClassVar[dict[str, int]] = {"width": 1024, "height": 768}

    def __init__(
        self,
        *,
        missing: str | None = None,
        text: str = "PO Supplier To Receive and Bill CNY",
        sensitive_visible: bool = False,
        timeout_on: str | None = None,
    ) -> None:
        self.missing = missing
        self.text = text
        self.sensitive_visible = sensitive_visible
        self.timeout_on = timeout_on
        self.style = ""

    def locator(self, selector: str) -> _Locator:
        if selector == "body":
            return _TextLocator(self.text, timeout=self.timeout_on == "inner_text")
        return _Locator(
            0 if selector == self.missing else 1,
            sensitive_visible=self.sensitive_visible,
        )

    def get_by_text(self, _value: str, *, exact: bool) -> _Locator:
        return _Locator(1)

    def add_style_tag(self, *, content: str) -> None:
        self.style = content

    def screenshot(self, **_kwargs: Any) -> bytes:
        if self.timeout_on == "screenshot":
            raise TimeoutError("blocked screenshot")
        return PNG

    def inner_text(self) -> str:
        return self.text


def test_redaction_requires_every_known_mask_target() -> None:
    result = capture_redacted_page(_Page(missing=REQUIRED_REDACTION_SELECTORS[0]), "PUR-ORD-0001")
    assert result.status == "BLOCKED"
    assert result.failure_code == "REDACTION_TARGET_MISSING"
    assert result.image is None


def test_redaction_rejects_visible_sensitive_markers() -> None:
    page = _Page(text="PO Supplier buyer@example.com")
    result = capture_redacted_page(page, "PUR-ORD-0001")
    assert result.status == "BLOCKED"
    assert result.failure_code == "SENSITIVE_MARKER_VISIBLE"
    assert page.style == REDACTION_CSS


def test_redaction_rejects_visible_sensitive_pixels() -> None:
    result = capture_redacted_page(_Page(sensitive_visible=True), "PUR-ORD-0001")
    assert result.status == "BLOCKED"
    assert result.failure_code == "REDACTION_PIXEL_REGION_VISIBLE"


def test_redaction_returns_bounded_png_digest() -> None:
    page = _Page()
    result = capture_redacted_page(page, "PUR-ORD-0001")
    assert result.status == "READY"
    assert result.image == PNG
    assert result.image_sha256 is not None
    assert result.viewport == (1024, 768)
    assert result.visible_text_sha256 is not None


@pytest.mark.parametrize("timeout_on", ["inner_text", "screenshot"])
def test_redaction_observation_timeout_is_explicit(timeout_on: str) -> None:
    result = capture_redacted_page(_Page(timeout_on=timeout_on), "PUR-ORD-0001", timeout_ms=5)

    assert result.status == "BLOCKED"
    assert result.failure_code == "OBSERVATION_TIMEOUT"
