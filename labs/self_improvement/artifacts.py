"""Safe, immutable artifact I/O and dataset/experiment verification."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .contracts import DatasetManifest, ExperimentRecord, safe_output_path
from .data import validate_grouped_splits

PHASE12_RELATIVE_ROOT = "output/phase12"
MAX_CALLS = 1_200


def code_version() -> str:
    """Return a short repository revision without reading credentials."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError, subprocess.SubprocessError:
        return "working-tree"
    value = result.stdout.strip()
    return value if value else "working-tree"


def _target(root: Path, relative: str) -> Path:
    path = safe_output_path(root, relative)
    try:
        path.relative_to((root / PHASE12_RELATIVE_ROOT).resolve())
    except ValueError as error:
        raise ValueError("artifact must remain under output/phase12") from error
    return path


def write_json_once(root: Path, relative: str, payload: object) -> Path:
    path = _target(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _render_manifest(payload: dict[str, object]) -> str:
    """Keep the large case list reviewable without a giant single-line document."""
    lines = ["{"]
    keys = tuple(key for key in sorted(payload) if key not in {"cases", "case_digests"})
    for index, key in enumerate(keys):
        encoded = json.dumps(payload[key], ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        has_following = index < len(keys) - 1 or "case_digests" in payload or "cases" in payload
        suffix = "," if has_following else ""
        lines.append(f"  {json.dumps(key)}: {encoded}{suffix}")
    if "case_digests" in payload:
        lines.append('  "case_digests": {')
        digests = payload["case_digests"]
        if not isinstance(digests, dict):
            raise ValueError("manifest case_digests must be an object")
        digest_items = tuple(sorted(digests.items()))
        for index, (case_id, digest) in enumerate(digest_items):
            suffix = "," if index < len(digest_items) - 1 else ""
            lines.append(f"    {json.dumps(case_id)}: {json.dumps(digest)}{suffix}")
        lines.append("  },")
    if "cases" in payload:
        lines.append('  "cases": [')
        cases = payload["cases"]
        if not isinstance(cases, list):
            raise ValueError("manifest cases must be an array")
        for index, case in enumerate(cases):
            suffix = "," if index < len(cases) - 1 else ""
            lines.append(
                "    "
                + json.dumps(case, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                + suffix
            )
        lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_text_once(root: Path, relative: str, content: str) -> Path:
    path = _target(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.write_text(content, encoding="utf-8")
    return path


def read_json(root: Path, relative: str) -> object:
    path = _target(root, relative)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(root: Path, manifest: DatasetManifest) -> Path:
    path = _target(root, f"{PHASE12_RELATIVE_ROOT}/dataset-{manifest.dataset_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.write_text(_render_manifest(manifest.model_dump(mode="json")), encoding="utf-8")
    return path


def read_manifest(root: Path, relative: str) -> DatasetManifest:
    payload = read_json(root, relative)
    if not isinstance(payload, dict):
        raise ValueError("dataset manifest must be an object")
    return DatasetManifest.model_validate(payload)


def verify_manifest(manifest: DatasetManifest) -> None:
    validate_grouped_splits(manifest.cases)
    expected_splits = {"train": 72, "dev": 24, "test": 24}
    if manifest.split_counts != expected_splits:
        raise ValueError("dataset split counts do not match the frozen contract")
    if manifest.group_counts != {"train": 36, "dev": 12, "test": 12}:
        raise ValueError("dataset group counts do not match the frozen contract")
    for case in manifest.cases:
        expected_digest = manifest.case_digests.get(case.case_id)
        from .contracts import digest_json

        if expected_digest != digest_json(case.model_dump(mode="json")):
            raise ValueError(f"case digest mismatch: {case.case_id}")
    payload = {
        "code_version": manifest.code_version,
        "seed": manifest.seed,
        "cases": [case.model_dump(mode="json") for case in manifest.cases],
    }
    from .contracts import digest_json

    if manifest.dataset_digest != digest_json(payload):
        raise ValueError("dataset digest mismatch")


def write_records(root: Path, name: str, records: Iterable[ExperimentRecord]) -> Path:
    if "/" in name or ".." in name or not name.endswith(".jsonl"):
        raise ValueError("record file name is invalid")
    path = _target(root, f"{PHASE12_RELATIVE_ROOT}/{name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    values = tuple(records)
    path.write_text(
        "".join(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=True, sort_keys=True) + "\n"
            for record in values
        ),
        encoding="utf-8",
    )
    return path


def read_records(root: Path, relative: str) -> tuple[ExperimentRecord, ...]:
    path = _target(root, relative)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    records: list[ExperimentRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("experiment record line must be an object")
        records.append(ExperimentRecord.model_validate(payload))
    return tuple(records)


def verify_records(records: Iterable[ExperimentRecord], manifest: DatasetManifest) -> None:
    values = tuple(records)
    if len({record.experiment_id for record in values}) != len(values):
        raise ValueError("experiment ids must be unique")
    if sum(record.calls for record in values) > MAX_CALLS:
        raise ValueError("cumulative model call budget exceeded")
    for record in values:
        if (
            record.dataset_id != manifest.dataset_id
            or record.dataset_digest != manifest.dataset_digest
        ):
            raise ValueError("experiment is bound to a different dataset")
        if record.split == "test" and record.method.startswith("candidate-"):
            raise ValueError("unregistered candidate cannot access held-out test")
