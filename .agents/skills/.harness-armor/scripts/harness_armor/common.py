"""Safe repository traversal and stable JSON helpers."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Optional


DEFAULT_MAX_FILES = 10_000
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 * 1024

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "bower_components",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
}

SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "secrets.yml",
    "secrets.yaml",
}

SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}

DOCUMENT_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CODE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".dart", ".ex", ".exs", ".go",
    ".h", ".hpp", ".java", ".js", ".jsx", ".kt", ".kts", ".lua",
    ".m", ".mm", ".php", ".pl", ".py", ".rb", ".rs", ".scala",
    ".sh", ".swift", ".ts", ".tsx", ".vue", ".zig",
}
CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".xml"}


class HarnessError(Exception):
    """Operational error with a user-readable message."""


class LimitReached(HarnessError):
    """A configured safety limit stopped a complete scan."""


def stable_json(value: Any, *, pretty: bool = True) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def emit(value: Any, *, pretty: bool = True) -> None:
    sys.stdout.write(stable_json(value, pretty=pretty))


def normalize_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def safe_relative_path(value: str) -> bool:
    if not value or "\x00" in value or "\\" in value:
        return False
    pure = PurePosixPath(value)
    return not pure.is_absolute() and ".." not in pure.parts and "." not in pure.parts


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in DOCUMENT_SUFFIXES or name in {"license", "notice", "readme"}:
        return "document"
    if suffix in CODE_SUFFIXES:
        return "code"
    if suffix in CONFIG_SUFFIXES or name in {
        "dockerfile", "makefile", "procfile", "gradlew", "mvnw",
    }:
        return "configuration"
    if "test" in path.parts or "tests" in path.parts or "spec" in path.parts:
        return "test"
    return "other"


def is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name == ".env.example" or name.endswith(".example"):
        return False
    return name in SENSITIVE_NAMES or name.startswith(".env.") or path.suffix.lower() in SENSITIVE_SUFFIXES


def looks_binary(path: Path, *, sample_size: int = 8192) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(sample_size)
    except OSError:
        return True
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    control = sum(1 for byte in sample if byte < 9 or 13 < byte < 32)
    return control / len(sample) > 0.10


@dataclass
class IgnoreRule:
    pattern: str
    base: str = ""
    negated: bool = False
    directory_only: bool = False
    anchored: bool = False

    def matches(self, rel_path: str, *, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False
        pattern = self.pattern.rstrip("/")
        target = rel_path.rstrip("/")
        if self.base:
            if target == self.base:
                local = ""
            elif target.startswith(self.base + "/"):
                local = target[len(self.base) + 1:]
            else:
                return False
        else:
            local = target
        if self.anchored:
            return fnmatch.fnmatchcase(local, pattern)
        if "/" in pattern:
            return fnmatch.fnmatchcase(local, pattern) or fnmatch.fnmatchcase(local, f"**/{pattern}")
        return any(fnmatch.fnmatchcase(part, pattern) for part in PurePosixPath(local).parts)


class IgnoreMatcher:
    """Small, deterministic subset of gitignore semantics with git fallback."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.rules: list[IgnoreRule] = []
        self.loaded: set[Path] = set()
        self.load_directory(root)

    def load_directory(self, directory: Path) -> None:
        ignore_file = directory / ".gitignore"
        if ignore_file in self.loaded or not ignore_file.is_file() or ignore_file.is_symlink():
            return
        self.loaded.add(ignore_file)
        base = directory.relative_to(self.root).as_posix()
        self._load(ignore_file, "" if base == "." else base)

    def _load(self, ignore_file: Path, base: str) -> None:
        try:
            lines = ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            self.rules.append(
                IgnoreRule(
                    pattern=line,
                    base=base,
                    negated=negated,
                    directory_only=line.endswith("/"),
                    anchored=anchored,
                )
            )

    def ignored(self, rel_path: str, *, is_dir: bool) -> bool:
        ignored = False
        for rule in self.rules:
            if rule.matches(rel_path, is_dir=is_dir):
                ignored = not rule.negated
        return ignored


@dataclass
class ScanLimits:
    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES

    def as_dict(self) -> dict[str, int]:
        return {
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
        }


@dataclass
class ScanResult:
    root: Path
    files: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False
    truncation_reason: Optional[str] = None

    def as_dict(self, limits: ScanLimits) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.files:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        return {
            "schema_version": "1.0.0",
            "root": str(self.root),
            "files": self.files,
            "skipped": self.skipped,
            "summary": {
                "file_count": len(self.files),
                "total_bytes": self.total_bytes,
                "counts_by_kind": dict(sorted(counts.items())),
                "truncated": self.truncated,
                "truncation_reason": self.truncation_reason,
            },
            "limits": limits.as_dict(),
            "warnings": sorted(self.warnings),
        }


def resolve_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.exists():
        raise HarnessError(f"repository root does not exist: {root}")
    if not root.is_dir():
        raise HarnessError(f"repository root is not a directory: {root}")
    return root


def _is_reparse_point(path: Path) -> bool:
    """Detect Windows reparse points (junctions, mount points) that
    ``os.walk(followlinks=False)`` and ``Path.is_symlink()`` miss. On
    non-Windows systems this always returns False."""
    if os.name != "nt":
        return False
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400)
    except (OSError, AttributeError):
        return False


def scan_repository(
    root: Path,
    *,
    limits: Optional[ScanLimits] = None,
    include_hashes: bool = False,
) -> ScanResult:
    limits = limits or ScanLimits()
    result = ScanResult(root=root)
    matcher = IgnoreMatcher(root)

    def onerror(error: OSError) -> None:
        result.warnings.append(f"unreadable directory: {error.filename}: {error.strerror}")

    for current, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        current_path = Path(current)
        matcher.load_directory(current_path)
        safe_dirs: list[str] = []
        for name in sorted(dirs):
            candidate = current_path / name
            rel = normalize_relative(candidate, root)
            if candidate.is_symlink() or _is_reparse_point(candidate):
                result.skipped.append({"path": rel, "reason": "symlink-directory"})
                continue
            if name in EXCLUDED_DIRS or matcher.ignored(rel, is_dir=True):
                result.skipped.append({"path": rel, "reason": "excluded-directory"})
                continue
            safe_dirs.append(name)
        dirs[:] = safe_dirs

        for name in sorted(files):
            candidate = current_path / name
            rel = normalize_relative(candidate, root)
            if candidate.is_symlink() or _is_reparse_point(candidate):
                result.skipped.append({"path": rel, "reason": "symlink-file"})
                continue
            if matcher.ignored(rel, is_dir=False):
                result.skipped.append({"path": rel, "reason": "gitignore"})
                continue
            if is_sensitive(candidate):
                result.skipped.append({"path": rel, "reason": "sensitive-name"})
                continue
            try:
                stat = candidate.stat()
            except OSError as exc:
                result.warnings.append(f"unreadable file: {rel}: {exc.strerror}")
                continue
            if not candidate.is_file():
                result.skipped.append({"path": rel, "reason": "non-regular-file"})
                continue
            if stat.st_size > limits.max_file_bytes:
                result.skipped.append({"path": rel, "reason": "file-size-limit"})
                continue
            if len(result.files) >= limits.max_files:
                result.truncated = True
                result.truncation_reason = "max-files"
                return result
            if result.total_bytes + stat.st_size > limits.max_total_bytes:
                result.truncated = True
                result.truncation_reason = "max-total-bytes"
                return result

            item: dict[str, Any] = {
                "path": rel,
                "size": stat.st_size,
                "kind": classify_file(candidate),
                "binary": looks_binary(candidate),
            }
            if include_hashes:
                try:
                    item["sha256"] = sha256_file(candidate)
                except OSError as exc:
                    result.warnings.append(f"hash failed: {rel}: {exc.strerror}")
                    continue
            result.files.append(item)
            result.total_bytes += stat.st_size

    result.files.sort(key=lambda item: item["path"])
    result.skipped.sort(key=lambda item: (item["path"], item["reason"]))
    return result


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise HarnessError(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except OSError as exc:
        raise HarnessError(f"cannot read {path}: {exc.strerror}") from exc


def text_excerpt(path: Path, *, max_bytes: int = 128 * 1024) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""
