"""Command implementations shared by source and installed skill wrappers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .analysis import (
    check_references,
    detect_drift,
    detect_state,
    score_health,
    validate_structure,
)
from .common import HarnessError, ScanLimits, emit, resolve_root, safe_relative_path, scan_repository
from .manifest import validate_manifest_file


EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_OPERATIONAL = 3
EXIT_LIMIT = 4


def add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", nargs="?", default=".", help="repository root (default: current directory)")
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")


def add_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-files", type=_positive_int, default=10_000)
    parser.add_argument("--max-file-bytes", type=_positive_int, default=2 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=_positive_int, default=50 * 1024 * 1024)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _run(parser: argparse.ArgumentParser, argv: Optional[Sequence[str]], operation: Callable[[argparse.Namespace], tuple[dict[str, Any], int]]) -> int:
    try:
        args = parser.parse_args(argv)
        payload, code = operation(args)
        emit(payload, pretty=not getattr(args, "compact", False))
        return code
    except HarnessError as exc:
        emit({"schema_version": "1.0.0", "error": str(exc), "exit_code": EXIT_OPERATIONAL})
        return EXIT_OPERATIONAL
    except OSError as exc:
        emit({"schema_version": "1.0.0", "error": f"filesystem error: {exc}", "exit_code": EXIT_OPERATIONAL})
        return EXIT_OPERATIONAL


def main_scan(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only repository inventory with exclusions and safety limits.")
    add_root(parser)
    add_limits(parser)
    parser.add_argument("--hash", action="store_true", help="include SHA-256 for scanned files")

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        root = resolve_root(args.root)
        limits = ScanLimits(args.max_files, args.max_file_bytes, args.max_total_bytes)
        result = scan_repository(root, limits=limits, include_hashes=args.hash)
        return result.as_dict(limits), EXIT_LIMIT if result.truncated else EXIT_OK

    return _run(parser, argv, operation)


def main_detect_state(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Classify repository state and recommend a Harness Armor skill.")
    add_root(parser)
    add_limits(parser)

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        root = resolve_root(args.root)
        limits = ScanLimits(args.max_files, args.max_file_bytes, args.max_total_bytes)
        payload = detect_state(root, limits=limits)
        return payload, EXIT_LIMIT if payload["scan_truncated"] else EXIT_OK

    return _run(parser, argv, operation)


def main_fingerprint(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Calculate stable SHA-256 source fingerprints without modifying files.")
    add_root(parser)
    parser.add_argument("paths", nargs="*", help="relative paths; omit to fingerprint scanned documents/config/code")

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        root = resolve_root(args.root)
        result = scan_repository(root, include_hashes=True)
        scanned = {item["path"]: item for item in result.files}
        requested = args.paths
        rels = sorted(set(requested)) if requested else [item["path"] for item in result.files if item["kind"] in {"document", "configuration", "code", "test"}]
        fingerprints = []
        errors = []
        for rel in rels:
            normalized = Path(rel).as_posix()
            if not safe_relative_path(normalized):
                errors.append({"path": rel, "reason": "escapes-root"})
                continue
            item = scanned.get(normalized)
            if item is None:
                skipped = next((entry["reason"] for entry in result.skipped if entry["path"] == normalized), None)
                errors.append({"path": rel, "reason": skipped or "excluded-missing-or-over-limit"})
                continue
            fingerprints.append({"path": normalized, "size": item["size"], "sha256": item["sha256"]})
        payload = {"schema_version": "1.0.0", "root": str(root), "fingerprints": fingerprints, "errors": errors, "warnings": result.warnings, "scan_truncated": result.truncated, "read_only": True}
        if result.truncated:
            return payload, EXIT_LIMIT
        return payload, EXIT_FINDINGS if errors else EXIT_OK

    return _run(parser, argv, operation)


def main_validate_manifest(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Harness Armor manifest and its linked state files.")
    add_root(parser)
    parser.add_argument("--manifest", default=".harness/manifest.json", help="manifest path relative to root")

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        root = resolve_root(args.root)
        path = (root / args.manifest).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise HarnessError("manifest path escapes repository root") from exc
        payload = validate_manifest_file(path, root=root)
        return payload, EXIT_OK if payload["valid"] else EXIT_FINDINGS

    return _run(parser, argv, operation)


def main_validate_structure(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate managed Harness structure, manifest, and local references.")
    add_root(parser)

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        payload = validate_structure(resolve_root(args.root))
        return payload, EXIT_OK if payload["valid"] else EXIT_FINDINGS

    return _run(parser, argv, operation)


def main_check_references(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Find broken local Markdown, asset, reference, and script links.")
    add_root(parser)

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        payload = check_references(resolve_root(args.root))
        if payload["scan_truncated"]:
            return payload, EXIT_LIMIT
        return payload, EXIT_OK if payload["valid"] else EXIT_FINDINGS

    return _run(parser, argv, operation)


def main_detect_drift(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare managed and source fingerprints with current files; read-only.")
    add_root(parser)
    parser.add_argument("--manifest", default=".harness/manifest.json")

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        root = resolve_root(args.root)
        manifest = (root / args.manifest).resolve()
        try:
            manifest.relative_to(root)
        except ValueError as exc:
            raise HarnessError("manifest path escapes repository root") from exc
        payload = detect_drift(root, manifest)
        return payload, EXIT_FINDINGS if payload["has_drift"] else EXIT_OK

    return _run(parser, argv, operation)


def main_score_health(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score machine-verifiable Harness health with evidence for deductions.")
    add_root(parser)
    parser.add_argument("--fail-below", type=float, default=None, help="return 1 when score is below this threshold")

    def operation(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
        payload = score_health(resolve_root(args.root))
        code = EXIT_FINDINGS if args.fail_below is not None and payload["score"] < args.fail_below else EXIT_OK
        return payload, code

    return _run(parser, argv, operation)
