"""Network and browser side-effect policy for the Phase 11 lab."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlparse

_SAFE_PATH = re.compile(r"^/(?:purchase-orders|api/purchase-orders)/[A-Za-z0-9_-]{1,140}$")
_ALLOWED_PATHS = frozenset({"/", "/health", "/api/purchase-orders"})
_ALLOWED_QUERY_KEYS = frozenset({"q", "scenario", "ready"})


@dataclass
class BrowserSecurityPolicy:
    """Allow only read-only loopback requests needed by the fixture page."""

    origin: str
    violations: list[str] = field(default_factory=list)

    def permits(self, url: str, method: str = "GET") -> bool:
        parsed = urlparse(url)
        if method.upper() != "GET" or f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            self.violations.append(f"{method.upper()} {url}")
            return False
        if parsed.fragment or (
            parsed.path not in _ALLOWED_PATHS and not _SAFE_PATH.fullmatch(parsed.path)
        ):
            self.violations.append(f"GET {url}")
            return False
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if len(query) > 3 or any(key not in _ALLOWED_QUERY_KEYS for key, _ in query):
            self.violations.append(f"GET {url}")
            return False
        if any(len(key) > 20 or len(value) > 140 for key, value in query):
            self.violations.append(f"GET {url}")
            return False
        return True


__all__ = ["BrowserSecurityPolicy"]
