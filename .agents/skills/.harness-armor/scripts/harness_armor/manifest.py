"""Manifest validation without third-party schema dependencies."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .common import HarnessError, read_json, safe_relative_path


HASH_RE = re.compile(r"^[a-f0-9]{64}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
STATES = {
    "EMPTY", "DOCS_ONLY", "LEGACY_CODE", "MANAGED_HARNESS",
    "CUSTOM_HARNESS", "MIXED_OR_CONFLICTED",
}
MODES = {"managed", "managed-section", "observed", "user"}
SOURCE_KINDS = {"product", "architecture", "design", "development", "testing", "acceptance", "plan", "code", "configuration", "other"}
FACT_STATUSES = {"CONFIRMED", "INFERRED", "UNRESOLVED", "CONFLICTED"}
UNRESOLVED_STATUSES = {"UNRESOLVED", "CONFLICTED", "RESOLVED"}


def validate_manifest_data(data: Any, *, root: Optional[Path] = None) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    def warning(path: str, message: str) -> None:
        warnings.append({"path": path, "message": message})

    if not isinstance(data, dict):
        return {"valid": False, "errors": [{"path": "$", "message": "manifest must be an object"}], "warnings": []}

    required = {
        "schema_version", "spec_version", "generator", "repository_state",
        "managed_files", "source_index", "unresolved_index", "last_updated",
    }
    for key in sorted(required - set(data)):
        error(f"$.{key}", "required property is missing")
    allowed = required | {"$schema"}
    for key in sorted(set(data) - allowed):
        error(f"$.{key}", "unknown property")

    if data.get("schema_version") != "1.0.0":
        error("$.schema_version", "must equal 1.0.0")
    spec_version = data.get("spec_version")
    if not isinstance(spec_version, str) or not SEMVER_RE.fullmatch(spec_version):
        error("$.spec_version", "must be a semantic version")
    generator = data.get("generator")
    if not isinstance(generator, dict):
        error("$.generator", "must be an object")
    else:
        for key in sorted(set(generator) - {"name", "version"}):
            error(f"$.generator.{key}", "unknown property")
        if generator.get("name") != "harness-armor":
            error("$.generator.name", "must equal harness-armor")
        if not isinstance(generator.get("version"), str) or not generator.get("version"):
            error("$.generator.version", "must be a non-empty string")
    if data.get("repository_state") not in STATES:
        error("$.repository_state", "unknown repository state")

    managed = data.get("managed_files")
    seen: set[str] = set()
    if not isinstance(managed, list):
        error("$.managed_files", "must be an array")
    else:
        for index, item in enumerate(managed):
            base = f"$.managed_files[{index}]"
            if not isinstance(item, dict):
                error(base, "must be an object")
                continue
            for key in sorted(set(item) - {"path", "owner", "mode", "sha256"}):
                error(f"{base}.{key}", "unknown property")
            rel = item.get("path")
            if not isinstance(rel, str) or not safe_relative_path(rel):
                error(f"{base}.path", "must be a safe relative POSIX path")
            elif rel in seen:
                error(f"{base}.path", "duplicate managed path")
            else:
                seen.add(rel)
                if root is not None and not (root / PurePathCompat(rel)).exists():
                    warning(f"{base}.path", "managed path does not currently exist")
            if not isinstance(item.get("owner"), str) or not item.get("owner"):
                error(f"{base}.owner", "must be a non-empty string")
            if item.get("mode") not in MODES:
                error(f"{base}.mode", "unknown ownership mode")
            digest = item.get("sha256")
            if digest is not None and (not isinstance(digest, str) or not HASH_RE.fullmatch(digest)):
                error(f"{base}.sha256", "must be null or a lowercase SHA-256 digest")

    for key in ("source_index", "unresolved_index"):
        value = data.get(key)
        if not isinstance(value, str) or not safe_relative_path(value):
            error(f"$.{key}", "must be a safe relative POSIX path")
        elif root is not None and not (root / PurePathCompat(value)).is_file():
            error(f"$.{key}", "referenced state file does not exist")

    if not _valid_datetime(data.get("last_updated")):
        error("$.last_updated", "must be an ISO-8601 date-time string")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def PurePathCompat(value: str) -> Path:
    return Path(*value.split("/"))


def _valid_datetime(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_source_index_data(data: Any, *, root: Optional[Path] = None) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if not isinstance(data, dict):
        return {"valid": False, "errors": [{"path": "$", "message": "source index must be an object"}], "warnings": []}
    for key in sorted(set(data) - {"$schema", "schema_version", "sources"}):
        error(f"$.{key}", "unknown property")
    if data.get("schema_version") != "1.0.0":
        error("$.schema_version", "must equal 1.0.0")
    sources = data.get("sources")
    seen_ids: set[str] = set()
    if not isinstance(sources, list):
        error("$.sources", "must be an array")
    else:
        for index, item in enumerate(sources):
            base = f"$.sources[{index}]"
            if not isinstance(item, dict):
                error(base, "must be an object")
                continue
            for key in sorted(set(item) - {"id", "path", "kind", "status", "sha256", "locator"}):
                error(f"{base}.{key}", "unknown property")
            for key in ("id", "path", "kind", "status", "sha256"):
                if key not in item:
                    error(f"{base}.{key}", "required property is missing")
            identifier = item.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"^[a-z0-9][a-z0-9-]*$", identifier):
                error(f"{base}.id", "must be a lowercase kebab identifier")
            elif identifier in seen_ids:
                error(f"{base}.id", "duplicate source id")
            else:
                seen_ids.add(identifier)
            rel = item.get("path")
            if not isinstance(rel, str) or not safe_relative_path(rel):
                error(f"{base}.path", "must be a safe relative POSIX path")
            elif root is not None and not (root / PurePathCompat(rel)).is_file():
                warnings.append({"path": f"{base}.path", "message": "source path does not currently exist"})
            if item.get("kind") not in SOURCE_KINDS:
                error(f"{base}.kind", "unknown source kind")
            if item.get("status") not in FACT_STATUSES:
                error(f"{base}.status", "unknown fact status")
            digest = item.get("sha256")
            if digest is not None and (not isinstance(digest, str) or not HASH_RE.fullmatch(digest)):
                error(f"{base}.sha256", "must be null or a lowercase SHA-256 digest")
            if "locator" in item and not isinstance(item["locator"], str):
                error(f"{base}.locator", "must be a string")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def validate_unresolved_data(data: Any) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def error(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    if not isinstance(data, dict):
        return {"valid": False, "errors": [{"path": "$", "message": "unresolved index must be an object"}], "warnings": []}
    for key in sorted(set(data) - {"$schema", "schema_version", "items"}):
        error(f"$.{key}", "unknown property")
    if data.get("schema_version") != "1.0.0":
        error("$.schema_version", "must equal 1.0.0")
    items = data.get("items")
    if not isinstance(items, list):
        error("$.items", "must be an array")
    else:
        for index, item in enumerate(items):
            base = f"$.items[{index}]"
            if not isinstance(item, dict):
                error(base, "must be an object")
                continue
            for key in sorted(set(item) - {"id", "status", "question", "impact", "sources", "resolution"}):
                error(f"{base}.{key}", "unknown property")
            for key in ("id", "status", "question", "impact"):
                if not isinstance(item.get(key), str) or not item.get(key):
                    error(f"{base}.{key}", "must be a non-empty string")
            if item.get("status") not in UNRESOLVED_STATUSES:
                error(f"{base}.status", "unknown unresolved status")
            if "sources" in item and (not isinstance(item["sources"], list) or not all(isinstance(value, str) for value in item["sources"])):
                error(f"{base}.sources", "must be an array of strings")
            if "resolution" in item and not isinstance(item["resolution"], str):
                error(f"{base}.resolution", "must be a string")
    return {"valid": not errors, "errors": errors, "warnings": []}


def validate_manifest_file(path: Path, *, root: Optional[Path] = None) -> dict[str, Any]:
    data = read_json(path)
    result = validate_manifest_data(data, root=root)
    if root is not None and isinstance(data, dict):
        linked = (
            ("source_index", validate_source_index_data),
            ("unresolved_index", validate_unresolved_data),
        )
        for key, validator in linked:
            value = data.get(key)
            if not isinstance(value, str) or not safe_relative_path(value):
                continue
            linked_path = root / PurePathCompat(value)
            if not linked_path.is_file():
                continue
            try:
                linked_result = validator(read_json(linked_path), root=root) if key == "source_index" else validator(read_json(linked_path))
            except HarnessError as exc:
                result["errors"].append({"path": f"$.{key}", "message": str(exc)})
                continue
            result["errors"].extend({"path": f"$.{key}{item['path'][1:]}", "message": item["message"]} for item in linked_result["errors"])
            result["warnings"].extend({"path": f"$.{key}{item['path'][1:]}", "message": item["message"]} for item in linked_result["warnings"])
        result["valid"] = not result["errors"]
    result["manifest"] = str(path)
    result["schema_version"] = "1.0.0"
    return result
