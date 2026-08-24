"""Repository state, references, drift, and health analysis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import unquote

from .common import (
    CODE_SUFFIXES,
    DOCUMENT_SUFFIXES,
    HarnessError,
    ScanLimits,
    read_json,
    safe_relative_path,
    scan_repository,
    sha256_file,
    text_excerpt,
)
from .manifest import validate_manifest_file


BASIC_FILES = {
    ".gitignore", ".gitattributes", ".editorconfig", ".gitkeep", "license", "license.md",
    "readme", "readme.md", "readme.txt", "notice", "notice.md",
}
DOC_SIGNAL_NAMES = {
    "prd.md", "product.md", "requirements.md", "architecture.md", "design.md",
    "roadmap.md", "spec.md", "specification.md", "user-stories.md",
}
CUSTOM_HARNESS_NAMES = {"agents.md", "claude.md", "copilot-instructions.md"}
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
ROLE_NAME_MARKERS = {
    "product": ("product", "prd", "requirement", "spec"),
    "architecture": ("architecture", "architecture-design", "system-design"),
    "development": ("development", "contributing", "developer-guide"),
    "testing": ("testing", "verification", "quality-gate", "quality_gate", "test-plan"),
    "acceptance": ("acceptance", "verification", "quality-gate", "quality_gate"),
    "state": ("task-state", "task_state", "roadmap", "status", "progress"),
    "harness": ("harness",),
    "design": ("design", "ui-ux", "ui_ux"),
}


def detect_state(root: Path, *, limits: Optional[ScanLimits] = None) -> dict[str, Any]:
    scan = scan_repository(root, limits=limits)
    paths = {item["path"] for item in scan.files}
    lower_paths = {path.lower() for path in paths}
    code_paths = sorted(
        path for path in paths
        if Path(path).suffix.lower() in CODE_SUFFIXES and not _is_tooling_path(path)
    )
    doc_paths = sorted(
        path for path in paths
        if Path(path).suffix.lower() in DOCUMENT_SUFFIXES
    )
    evidence: list[dict[str, Any]] = []
    uncertainties: list[str] = []

    manifest_path = root / ".harness" / "manifest.json"
    if manifest_path.is_file() and not manifest_path.is_symlink():
        validation = validate_manifest_file(manifest_path, root=root)
        if validation["valid"]:
            state = "MANAGED_HARNESS"
            confidence = 0.99
            evidence.append({"kind": "managed-manifest", "path": ".harness/manifest.json"})
        else:
            state = "MIXED_OR_CONFLICTED"
            confidence = 0.95
            evidence.append({"kind": "invalid-managed-manifest", "path": ".harness/manifest.json", "errors": validation["errors"]})
        return _state_result(root, state, confidence, evidence, uncertainties, scan)

    conflict_docs = []
    for rel in doc_paths:
        content = text_excerpt(root / rel)
        if all(marker in content for marker in CONFLICT_MARKERS):
            conflict_docs.append(rel)
    if conflict_docs:
        evidence.append({"kind": "unresolved-merge-markers", "paths": conflict_docs})
        return _state_result(root, "MIXED_OR_CONFLICTED", 0.98, evidence, uncertainties, scan)

    substantive = sorted(path for path in paths if path.lower() not in BASIC_FILES)
    if not substantive:
        evidence.append({"kind": "no-substantive-files", "basic_files": sorted(paths)})
        return _state_result(root, "EMPTY", 0.98, evidence, uncertainties, scan)

    has_doc_signal = any(
        path.lower().startswith("docs/")
        or Path(path).name.lower() in DOC_SIGNAL_NAMES
        or Path(path).name.lower().startswith(("prd", "requirements", "spec"))
        for path in doc_paths
    )
    custom_roles = _discover_harness_roles(paths)
    has_custom = bool(custom_roles["agents"]) or _has_harness_doc_set(lower_paths)
    has_coherent_custom = _has_coherent_custom_harness(custom_roles)

    if code_paths:
        evidence.append({"kind": "business-code", "sample": code_paths[:20], "count": len(code_paths)})
        if has_coherent_custom:
            evidence.append({
                "kind": "custom-harness-signals",
                "paths": _custom_harness_paths(paths),
                "roles": _role_evidence(custom_roles),
            })
            return _state_result(root, "CUSTOM_HARNESS", 0.9, evidence, uncertainties, scan)
        if has_custom:
            uncertainties.append("Custom agent instructions exist but a complete Harness structure was not established.")
        return _state_result(root, "LEGACY_CODE", 0.93, evidence, uncertainties, scan)

    if has_doc_signal:
        evidence.append({"kind": "product-documentation", "paths": doc_paths[:30]})
        return _state_result(root, "DOCS_ONLY", 0.94, evidence, uncertainties, scan)

    if has_custom:
        evidence.append({"kind": "custom-harness-signals", "paths": _custom_harness_paths(paths)})
        return _state_result(root, "CUSTOM_HARNESS", 0.82, evidence, uncertainties, scan)

    uncertainties.append("Files exist, but deterministic evidence cannot establish product documentation or business code.")
    evidence.append({"kind": "unclassified-files", "sample": substantive[:30]})
    return _state_result(root, "MIXED_OR_CONFLICTED", 0.62, evidence, uncertainties, scan)


def _state_result(root: Path, state: str, confidence: float, evidence: list[dict[str, Any]], uncertainties: list[str], scan: Any) -> dict[str, Any]:
    routes = {
        "EMPTY": "harness-init",
        "DOCS_ONLY": "harness-build",
        "LEGACY_CODE": "harness-promotion",
        "MANAGED_HARNESS": "harness-check",
        "CUSTOM_HARNESS": "harness-check",
        "MIXED_OR_CONFLICTED": "harness-check",
    }
    return {
        "schema_version": "1.0.0",
        "root": str(root),
        "state": state,
        "confidence": confidence,
        "evidence": evidence,
        "uncertainties": uncertainties,
        "recommended_skill": routes[state],
        "read_only": True,
        "scan_truncated": scan.truncated,
    }


def _is_tooling_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts and parts[0] in {"scripts", "tools", ".github"})


def _has_harness_doc_set(paths: set[str]) -> bool:
    required = {"docs/product.md", "docs/architecture.md", "docs/testing.md"}
    return "agents.md" in paths and len(required & paths) >= 2


def _discover_harness_roles(paths: Iterable[str]) -> dict[str, list[str]]:
    roles = {"agents": []}
    roles.update({role: [] for role in ROLE_NAME_MARKERS})
    for path in sorted(paths):
        pure = Path(path)
        name = pure.name.lower()
        if name in CUSTOM_HARNESS_NAMES:
            roles["agents"].append(path)
        if pure.suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        stem = pure.stem.lower().replace(" ", "-")
        parts = {part.lower() for part in pure.parts}
        for role, markers in ROLE_NAME_MARKERS.items():
            if any(stem == marker or stem.startswith(f"{marker}-") for marker in markers):
                roles[role].append(path)
            elif role == "product" and "product" in parts:
                roles[role].append(path)
    return roles


def _has_coherent_custom_harness(roles: dict[str, list[str]]) -> bool:
    if not roles["agents"] or not roles["architecture"] or not roles["testing"]:
        return False
    return any(roles[role] for role in ("product", "state", "harness"))


def _role_evidence(roles: dict[str, list[str]]) -> dict[str, list[str]]:
    return {role: paths[:5] for role, paths in roles.items() if paths}


def _custom_harness_paths(paths: Iterable[str]) -> list[str]:
    return sorted(
        path for path in paths
        if Path(path).name.lower() in CUSTOM_HARNESS_NAMES or path.lower().startswith(("docs/", ".cursor/", ".claude/", ".agents/"))
    )[:30]


MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_LINK_RE = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']")
RESOURCE_PATH_RE = re.compile(r"(?<![\w.-])((?:scripts|references|assets)/[A-Za-z0-9_./-]+)")
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
FENCED_CODE_RE = re.compile(r"(?:```|~~~)[\s\S]*?(?:```|~~~)")
HEADING_CLEAN_RE = re.compile(r"[^\w\s-]", re.UNICODE)
ROOT_REFERENCE_NAMES = {
    "agents.md", "claude.md", "contributing.md", "design.md", "license", "license.md",
    "makefile", "package.json", "pyproject.toml", "readme.md", "tsconfig.json",
}


def check_references(root: Path) -> dict[str, Any]:
    scan = scan_repository(root)
    broken: list[dict[str, str]] = []
    checked = 0
    explicit_checked = 0
    inline_existing_checked = 0
    heading_cache: dict[Path, set[str]] = {}
    for item in scan.files:
        rel = item["path"]
        if Path(rel).suffix.lower() not in {".md", ".mdx"} or item["binary"]:
            continue
        source = root / rel
        text = text_excerpt(source, max_bytes=2 * 1024 * 1024)
        targets = [(match.group(1).strip().split()[0].strip("<>\"'"), "explicit") for match in MARKDOWN_LINK_RE.finditer(text)]
        targets.extend((match.group(1).strip(), "explicit") for match in HTML_LINK_RE.finditer(text))
        if source.name == "SKILL.md":
            targets.extend((match.group(1).rstrip(".,:;)"), "explicit") for match in RESOURCE_PATH_RE.finditer(text))
        without_fences = FENCED_CODE_RE.sub("", text)
        targets.extend((target, "inline-existing") for target in _inline_repository_targets(without_fences))
        for raw_target, reference_kind in sorted(set(targets)):
            target = unquote(raw_target)
            if not target or "{{" in target or "}}" in target or target.startswith(("http://", "https://", "mailto:", "data:", "#")):
                continue
            path_part, _, anchor = target.partition("#")
            if reference_kind == "inline-existing":
                if path_part.startswith(("/", "~")):
                    continue
                candidates = [(root / path_part).resolve(), (source.parent / path_part).resolve()]
                resolved = next((candidate for candidate in candidates if candidate.exists()), None)
                if resolved is None:
                    continue
            else:
                resolved = (source.parent / path_part).resolve()
            checked += 1
            if reference_kind == "explicit":
                explicit_checked += 1
            else:
                inline_existing_checked += 1
            try:
                resolved.relative_to(root)
            except ValueError:
                broken.append({"source": rel, "target": raw_target, "reason": "escapes-root"})
                continue
            if not resolved.exists():
                broken.append({"source": rel, "target": raw_target, "reason": "missing"})
                continue
            if anchor and resolved.is_file() and resolved.suffix.lower() in {".md", ".mdx"}:
                headings = heading_cache.setdefault(resolved, _headings(resolved))
                if anchor.lower() not in headings:
                    broken.append({"source": rel, "target": raw_target, "reason": "missing-anchor"})
    if explicit_checked:
        coverage_status = "EXPLICIT_AND_INLINE_CHECKED" if inline_existing_checked else "EXPLICIT_CHECKED"
    elif inline_existing_checked:
        coverage_status = "INLINE_EXISTING_ONLY"
    else:
        coverage_status = "NO_LOCAL_REFERENCES_DETECTED"
    warnings = list(scan.warnings)
    if checked == 0:
        warnings.append("No local repository references were detected; validity does not prove reference coverage.")
    elif explicit_checked == 0:
        warnings.append("Only existing inline-code paths were counted; missing inline tokens are intentionally unassessed.")
    return {
        "schema_version": "1.0.0",
        "root": str(root),
        "valid": not broken,
        "checked_references": checked,
        "broken_references": broken,
        "coverage": {
            "status": coverage_status,
            "checked": checked,
            "explicit_checked": explicit_checked,
            "inline_existing_checked": inline_existing_checked,
        },
        "warnings": warnings,
        "scan_truncated": scan.truncated,
    }


def _inline_repository_targets(text: str) -> list[str]:
    targets: list[str] = []
    for match in INLINE_CODE_RE.finditer(text):
        candidate = match.group(1).strip().strip("<>\"'").rstrip(".,:;)")
        if not candidate or any(char.isspace() for char in candidate):
            continue
        if any(marker in candidate for marker in ("*", "{{", "}}", "$", "://")):
            continue
        path_part = candidate.partition("#")[0].rstrip("/")
        pure = Path(path_part)
        if pure.name.lower() in ROOT_REFERENCE_NAMES or "/" in path_part:
            if pure.suffix.lower() in DOCUMENT_SUFFIXES | CODE_SUFFIXES | {".json", ".toml", ".yaml", ".yml", ".xml"} or "/" in path_part:
                targets.append(candidate)
    return targets


def _headings(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in text_excerpt(path, max_bytes=2 * 1024 * 1024).splitlines():
        if not line.startswith("#"):
            continue
        title = line.lstrip("#").strip().lower()
        slug = HEADING_CLEAN_RE.sub("", title).strip().replace(" ", "-")
        suffix = counts.get(slug, 0)
        counts[slug] = suffix + 1
        anchors.add(slug if suffix == 0 else f"{slug}-{suffix}")
    return anchors


def detect_drift(root: Path, manifest_path: Optional[Path] = None) -> dict[str, Any]:
    manifest_path = manifest_path or root / ".harness" / "manifest.json"
    validation = validate_manifest_file(manifest_path, root=root)
    if not validation["valid"]:
        return {
            "schema_version": "1.0.0",
            "root": str(root),
            "valid_manifest": False,
            "drift": [{"category": "manifest-invalid", "path": str(manifest_path), "evidence": validation["errors"]}],
            "has_drift": True,
            "read_only": True,
        }
    manifest = read_json(manifest_path)
    events: list[dict[str, Any]] = []
    for item in manifest.get("managed_files", []):
        _compare_recorded(root, item, "managed-content", events)
    source_path = root / Path(*manifest["source_index"].split("/"))
    try:
        source_data = read_json(source_path)
        for item in source_data.get("sources", []):
            _compare_recorded(root, item, "source", events)
    except HarnessError as exc:
        events.append({"category": "source-index-invalid", "path": manifest["source_index"], "reason": str(exc)})
    events.sort(key=lambda item: (item.get("category", ""), item.get("path", "")))
    return {
        "schema_version": "1.0.0",
        "root": str(root),
        "valid_manifest": True,
        "has_drift": bool(events),
        "drift": events,
        "read_only": True,
    }


def _compare_recorded(root: Path, item: dict[str, Any], category: str, events: list[dict[str, Any]]) -> None:
    rel = item.get("path")
    if not isinstance(rel, str) or not safe_relative_path(rel):
        events.append({"category": f"{category}-invalid-path", "path": str(rel)})
        return
    path = root / Path(*rel.split("/"))
    recorded = item.get("sha256")
    if not path.is_file() or path.is_symlink():
        events.append({"category": f"{category}-missing", "path": rel})
        return
    if not recorded:
        events.append({"category": f"{category}-baseline-missing", "path": rel, "current_sha256": sha256_file(path)})
        return
    current = sha256_file(path)
    if current != recorded:
        events.append({"category": f"{category}-modified", "path": rel, "recorded_sha256": recorded, "current_sha256": current})


def validate_structure(root: Path) -> dict[str, Any]:
    required = [
        "AGENTS.md", ".harness/manifest.json", ".harness/source-index.json",
        ".harness/unresolved.json", "docs/PRD.md", "docs/ARCHITECTURE.md",
        "docs/DEVELOPMENT.md", "docs/TESTING.md", "docs/ACCEPTANCE.md",
    ]
    missing = [rel for rel in required if not (root / Path(*rel.split("/"))).is_file()]
    manifest_result: Optional[dict[str, Any]] = None
    if not missing or ".harness/manifest.json" not in missing:
        manifest_result = validate_manifest_file(root / ".harness" / "manifest.json", root=root)
    refs = check_references(root)
    issues: list[dict[str, Any]] = [{"kind": "missing-required-file", "path": rel} for rel in missing]
    if manifest_result and not manifest_result["valid"]:
        issues.append({"kind": "manifest-invalid", "evidence": manifest_result["errors"]})
    issues.extend({"kind": "broken-reference", **item} for item in refs["broken_references"])
    return {
        "schema_version": "1.0.0",
        "root": str(root),
        "valid": not issues,
        "issues": issues,
        "manifest": manifest_result,
        "reference_check": {"checked": refs["checked_references"], "broken": len(refs["broken_references"])},
        "read_only": True,
    }


HEALTH_WEIGHTS = {
    "understandability": 10,
    "agents-entry": 6,
    "product-architecture-implementation-consistency": 10,
    "instruction-conflicts": 7,
    "documentation-drift": 8,
    "command-veracity": 8,
    "change-boundaries": 7,
    "verification-loop": 8,
    "source-traceability": 7,
    "state-continuity": 5,
    "file-ownership": 6,
    "context-efficiency": 5,
    "cross-agent-compatibility": 4,
    "updateability": 5,
    "safety": 2,
    "nonfiction": 2,
}


def score_health(root: Path) -> dict[str, Any]:
    dimensions: list[dict[str, Any]] = []
    scan = scan_repository(root)
    paths = {item["path"] for item in scan.files}
    roles = _discover_harness_roles(paths)
    observed = {
        "agents": bool(roles["agents"]),
        "product": bool(roles["product"]),
        "architecture": bool(roles["architecture"]),
        "development": bool(roles["development"]),
        "testing": bool(roles["testing"]),
        "acceptance": bool(roles["acceptance"]),
        "state": bool(roles["state"]),
        "harness": bool(roles["harness"]),
        "manifest": ".harness/manifest.json" in paths,
        "source-index": ".harness/source-index.json" in paths,
        "unresolved": ".harness/unresolved.json" in paths,
    }
    agents_path = roles["agents"][0] if roles["agents"] else "AGENTS.md"
    agents_text = text_excerpt(root / agents_path) if observed["agents"] else ""
    agents_lines = len(agents_text.splitlines())
    refs = check_references(root)
    coherent_custom = _has_coherent_custom_harness(roles)
    layout = "managed" if observed["manifest"] else ("custom" if coherent_custom else "partial")
    preservation_boundary = _has_preservation_boundary(agents_text)
    authority_boundary = _has_authority_boundary(agents_text)
    drift = None
    if observed["manifest"]:
        try:
            drift = detect_drift(root)
        except HarnessError as exc:
            drift = {"has_drift": True, "drift": [{"category": "analysis-error", "reason": str(exc)}]}

    checks: dict[str, tuple[float, list[dict[str, Any]], str]] = {
        "understandability": (
            _ratio(observed, ["agents", "product", "architecture"]),
            _missing_role_evidence(observed, roles, ["agents", "product", "architecture"]),
            "role-equivalent structural evidence only",
        ),
        "agents-entry": (
            1.0 if observed["agents"] and 0 < agents_lines <= 120 else (0.5 if observed["agents"] else 0.0),
            [] if observed["agents"] and 0 < agents_lines <= 120 else [{"path": agents_path, "finding": "missing, empty, or longer than 120 lines", "lines": agents_lines}],
            "measured",
        ),
        "product-architecture-implementation-consistency": (
            0.5 if observed["product"] and observed["architecture"] else 0.0,
            _missing_role_evidence(observed, roles, ["product", "architecture"]),
            "semantic consistency requires host-agent review; score capped at 50%",
        ),
        "instruction-conflicts": (
            0.0 if any(marker in agents_text for marker in CONFLICT_MARKERS) else (1.0 if observed["agents"] else 0.0),
            [{"path": agents_path, "finding": "merge conflict markers"}] if any(marker in agents_text for marker in CONFLICT_MARKERS) else [],
            "marker-based evidence",
        ),
        "documentation-drift": (
            1.0 if drift is not None and not drift["has_drift"] else (0.0 if drift is not None else 0.25),
            drift.get("drift", []) if drift else [{"path": ".harness/manifest.json", "finding": "no managed baseline"}],
            "fingerprint evidence",
        ),
        "command-veracity": (
            0.5 if observed["development"] or observed["testing"] else 0.0,
            [] if observed["development"] or observed["testing"] else [{"role": "development-or-testing", "finding": "missing"}],
            "command existence can be checked; successful execution requires host evidence",
        ),
        "change-boundaries": (
            1.0 if preservation_boundary else 0.0,
            [] if preservation_boundary else [{"path": agents_path, "finding": "no explicit preservation boundary"}],
            "multilingual textual evidence",
        ),
        "verification-loop": (
            _ratio(observed, ["testing", "acceptance"]),
            _missing_role_evidence(observed, roles, ["testing", "acceptance"]),
            "role-equivalent structural evidence only",
        ),
        "source-traceability": (
            1.0 if observed["source-index"] else (0.5 if refs["checked_references"] and refs["valid"] else 0.0),
            [] if observed["source-index"] or (refs["checked_references"] and refs["valid"]) else [{"role": "source-index-or-valid-references", "finding": "missing"}],
            "managed source index or repository-reference evidence; semantic traceability remains unassessed",
        ),
        "state-continuity": (
            _ratio(observed, ["manifest", "unresolved"]) if observed["manifest"] else (0.5 if observed["state"] else 0.0),
            [] if (observed["manifest"] and observed["unresolved"]) or observed["state"] else [{"role": "managed-state-or-task-state", "finding": "missing"}],
            "managed state or custom task-state evidence",
        ),
        "file-ownership": (
            1.0 if observed["manifest"] else (0.5 if authority_boundary else 0.0),
            [] if observed["manifest"] or authority_boundary else [{"role": "manifest-or-authority-map", "finding": "missing"}],
            "manifest ownership or custom authority-map evidence",
        ),
        "context-efficiency": (
            1.0 if observed["agents"] and agents_lines <= 120 else 0.0,
            [] if observed["agents"] and agents_lines <= 120 else [{"path": agents_path, "finding": "entry point is not concise", "lines": agents_lines}],
            "line-count evidence; document count adds no points",
        ),
        "cross-agent-compatibility": (
            0.5 if observed["agents"] else 0.0,
            [] if observed["agents"] else [{"role": "agents", "finding": "missing"}],
            "client-specific compatibility requires live verification",
        ),
        "updateability": (
            1.0 if observed["manifest"] and observed["source-index"] else (0.5 if observed["state"] and refs["checked_references"] else 0.0),
            [] if (observed["manifest"] and observed["source-index"]) or (observed["state"] and refs["checked_references"]) else [{"role": "managed-state-or-custom-continuity", "finding": "missing"}],
            "managed baseline or custom continuity/reference evidence",
        ),
        "safety": (
            0.5 if observed["agents"] and preservation_boundary else 0.0,
            [] if observed["agents"] and preservation_boundary else [{"role": "agents-safety-boundary", "finding": "missing"}],
            "secret handling and safe-change boundaries require host evidence; score capped at 50%",
        ),
        "nonfiction": (
            0.5 if observed["acceptance"] else 0.0,
            _missing_role_evidence(observed, roles, ["acceptance"]),
            "semantic non-fiction review requires host evidence; score capped at 50%",
        ),
    }

    total = 0.0
    for identifier, weight in HEALTH_WEIGHTS.items():
        ratio, evidence, assessment = checks[identifier]
        points = round(weight * ratio, 2)
        total += points
        dimensions.append({
            "id": identifier,
            "score": points,
            "max_score": weight,
            "assessment": assessment,
            "evidence": evidence,
        })
    score = round(total, 2)
    return {
        "schema_version": "1.0.0",
        "root": str(root),
        "score": score,
        "max_score": 100,
        "grade": "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F",
        "layout": layout,
        "role_evidence": _role_evidence(roles),
        "dimensions": dimensions,
        "rule": "File count never adds points; semantic dimensions remain capped without host-agent evidence.",
        "read_only": True,
    }


def _ratio(observed: dict[str, bool], keys: list[str]) -> float:
    return sum(1 for key in keys if observed[key]) / len(keys)


def _missing_role_evidence(observed: dict[str, bool], roles: dict[str, list[str]], keys: list[str]) -> list[dict[str, str]]:
    return [{"role": key, "finding": "missing"} for key in keys if not observed[key]]


def _has_preservation_boundary(text: str) -> bool:
    lower = text.lower()
    english = ("preserve", "do not overwrite", "don't overwrite", "user-owned", "uncommitted change")
    chinese = ("不得覆盖", "不要覆盖", "禁止覆盖", "保留用户", "未提交的修改", "未提交修改")
    return any(marker in lower for marker in english) or any(marker in text for marker in chinese)


def _has_authority_boundary(text: str) -> bool:
    lower = text.lower()
    english = ("source of truth", "authoritative", "authority", "owned by", "user-owned")
    chinese = ("事实来源", "权威", "所有权", "用户维护", "入口地图")
    return any(marker in lower for marker in english) or any(marker in text for marker in chinese)
