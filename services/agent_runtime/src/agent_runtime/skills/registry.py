"""Strict local Skill manifests and progressive disclosure.

Skills are versioned procedural guidance, not capability grants.  The
registry only reads the package-local resources below, verifies their hashes,
and returns ContextBuilder fragments after a server-owned task-profile match.
No URL, user upload, RAG/Memory source, shell command, or ERP writer is
reachable from this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from agent_runtime.agent.context import ContextFragment
from agent_runtime.agent.contracts import StrictModel, canonical_json

SKILL_REGISTRY_VERSION: Literal["1"] = "1"
SKILL_SCHEMA_VERSION: Literal["1"] = "1"
SUPPORTED_SKILL_VERSION = "1.0.0"
SKILL_RESOURCE_ROOT = Path(__file__).with_name("resources")

READ_ONLY_TOOL_NAMES = frozenset(
    {
        "item.lookup",
        "supplier.lookup",
        "stock.projected",
        "demand.open",
        "material_request.open",
        "purchase_order.open",
    }
)

TASK_SKILL_IDS: Mapping[str, tuple[str, ...]] = {
    "REPLENISHMENT_ANALYSIS": (
        "replenishment-analysis",
        "duplicate-purchase-check",
    ),
    "MATERIAL_REQUEST_DRAFT": ("material-request-draft",),
    "RECONCILIATION": ("reconciliation",),
    "PLAN_ENHANCEMENT": (),
}

_SKILL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,119}$")
_RELATIVE_PATH = re.compile(r"^[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*$")
_TASK_PROFILE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "version",
        "summary",
        "source",
        "freedom",
        "allowed_tools",
        "body_path",
        "body_hash",
        "references",
        "reference_hashes",
    }
)


def _tuple_from_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


_TupleStrings = Annotated[tuple[str, ...], BeforeValidator(_tuple_from_json)]


class SkillRegistryError(ValueError):
    """A malformed, unavailable, or capability-expanding Skill resource."""

    code: Literal["CONTEXT_INVALID"] = "CONTEXT_INVALID"


class SkillReference(StrictModel):
    path: str = Field(min_length=1, max_length=240)
    version: str = Field(min_length=1, max_length=40)
    task_profiles: _TupleStrings = ()
    required_tools: _TupleStrings = ()

    @model_validator(mode="after")
    def validate_reference(self) -> SkillReference:
        if not _RELATIVE_PATH.fullmatch(self.path) or self.path.startswith("/"):
            raise ValueError("skill path is invalid")
        if not _VERSION.fullmatch(self.version) or self.version != SUPPORTED_SKILL_VERSION:
            raise ValueError("skill reference version is unsupported")
        if len(set(self.task_profiles)) != len(self.task_profiles) or any(
            not _TASK_PROFILE.fullmatch(profile) for profile in self.task_profiles
        ):
            raise ValueError("skill reference task profile is invalid")
        if len(set(self.required_tools)) != len(self.required_tools) or any(
            tool not in READ_ONLY_TOOL_NAMES for tool in self.required_tools
        ):
            raise ValueError("skill reference tool allowlist is invalid")
        return self


class SkillManifest(StrictModel):
    schema_version: Literal["1"]
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=120)
    freedom: Literal["BOUNDED", "STRICT"]
    allowed_tools: _TupleStrings = ()
    body_path: str = Field(min_length=1, max_length=240)
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    references: Annotated[tuple[SkillReference, ...], BeforeValidator(_tuple_from_json)] = ()
    reference_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_manifest(self) -> SkillManifest:
        if not _SKILL_ID.fullmatch(self.id):
            raise ValueError("skill id is invalid")
        if self.version != SUPPORTED_SKILL_VERSION:
            raise ValueError("skill version is unsupported")
        if not _SOURCE.fullmatch(self.source) or "://" in self.source or ".." in self.source:
            raise ValueError("skill source is invalid")
        if not _RELATIVE_PATH.fullmatch(self.body_path) or self.body_path.startswith("/"):
            raise ValueError("skill path is invalid")
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or any(
            tool not in READ_ONLY_TOOL_NAMES for tool in self.allowed_tools
        ):
            raise ValueError("skill allowed_tools must be a read-only allowlist")
        reference_paths = tuple(reference.path for reference in self.references)
        if len(set(reference_paths)) != len(reference_paths):
            raise ValueError("skill reference paths must be unique")
        if set(self.reference_hashes) != set(reference_paths):
            raise ValueError("skill reference hashes are incomplete")
        if any(
            not _RELATIVE_PATH.fullmatch(path) or not _HASH.fullmatch(value)
            for path, value in self.reference_hashes.items()
        ):
            raise ValueError("skill reference hash is invalid")
        return self


class SkillLoadRecord(StrictModel):
    skill_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    skill_version: str = Field(min_length=1, max_length=40)
    skill_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclosure_level: Literal[2, 3]
    effective_tool_names: _TupleStrings = ()
    load_reason: str = Field(min_length=1, max_length=240)


@dataclass(frozen=True)
class SkillSelection:
    """Provider-visible Skill fragments and trace-safe load metadata."""

    skill_fragments: tuple[ContextFragment, ...]
    reference_fragments: tuple[ContextFragment, ...]
    records: tuple[SkillLoadRecord, ...]

    @property
    def selected_fragments(self) -> tuple[ContextFragment, ...]:
        return (*self.skill_fragments, *self.reference_fragments)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_manifest_json(raw: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_pairs,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SkillRegistryError("skill manifest JSON is invalid") from error
    if not isinstance(value, dict):
        raise SkillRegistryError("skill manifest must be an object")
    return value


class SkillRegistry:
    """Load only server-selected Skills from a fixed local resource root."""

    version: Literal["1"] = SKILL_REGISTRY_VERSION

    def __init__(self, *, root: Path | None = None) -> None:
        configured_root = root or SKILL_RESOURCE_ROOT
        if configured_root.is_symlink():
            raise SkillRegistryError("skill resource root is invalid")
        self.root = configured_root.resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise SkillRegistryError("skill resource root is invalid")

    def list_manifests(self) -> tuple[SkillManifest, ...]:
        manifests: list[SkillManifest] = []
        seen_ids: set[str] = set()
        entries = self._root_entries()
        for entry in entries:
            manifest_path = entry / "skill.json"
            manifest = self._read_manifest(manifest_path, expected_id=entry.name)
            if manifest.id in seen_ids:
                raise SkillRegistryError("duplicate skill id")
            seen_ids.add(manifest.id)
            manifests.append(manifest)
        if not manifests:
            raise SkillRegistryError("no governed skills are registered")
        return tuple(manifests)

    def load_for_task(
        self,
        task_profile: str,
        *,
        allowed_tools: frozenset[str],
        skill_ids: Sequence[str] | None = None,
    ) -> SkillSelection:
        self._root_entries()
        configured_ids = TASK_SKILL_IDS.get(task_profile)
        if configured_ids is None and skill_ids is None:
            raise SkillRegistryError("task profile has no governed Skill mapping")
        if skill_ids is None:
            assert configured_ids is not None
            selected_ids = configured_ids
        else:
            selected_ids = tuple(skill_ids)
        if len(set(selected_ids)) != len(selected_ids):
            raise SkillRegistryError("duplicate skill id")
        manifests = [self._read_manifest_for_id(skill_id) for skill_id in selected_ids]
        if len({manifest.id for manifest in manifests}) != len(manifests):
            raise SkillRegistryError("duplicate skill id")
        if not allowed_tools.issubset(READ_ONLY_TOOL_NAMES):
            raise SkillRegistryError("caller allowlist contains a non-read-only tool")

        skill_fragments: list[ContextFragment] = []
        reference_fragments: list[ContextFragment] = []
        records: list[SkillLoadRecord] = []
        for manifest in sorted(manifests, key=lambda item: item.id):
            manifest_tools = frozenset(manifest.allowed_tools)
            if not manifest_tools.issubset(allowed_tools):
                raise SkillRegistryError("skill allowed_tools exceed caller allowlist")
            manifest_hash = self._manifest_hash(manifest)
            body_path = self._resolve_path(manifest.body_path, manifest.id)
            body = self._read_text(body_path)
            self._verify_hash(body_path, manifest.body_hash, "skill body hash does not match")
            skill_fragments.append(
                ContextFragment.from_content(
                    fragment_id=f"skill:{manifest.id}:body",
                    fragment_type="skill",
                    source=f"skill:{manifest.id}:body",
                    version=manifest.version,
                    trust_level="CONTROLLED",
                    priority=600,
                    content=body,
                    required=True,
                )
            )
            loaded_references = 0
            for reference in manifest.references:
                if not self._reference_triggered(reference, task_profile, allowed_tools):
                    continue
                reference_path = self._resolve_path(reference.path, manifest.id)
                reference_content = self._read_text(reference_path)
                self._verify_hash(
                    reference_path,
                    manifest.reference_hashes[reference.path],
                    "skill reference hash does not match",
                )
                reference_fragments.append(
                    ContextFragment.from_content(
                        fragment_id=(
                            f"skill:{manifest.id}:reference:{reference.path.replace('/', ':')}"
                        ),
                        fragment_type="reference",
                        source=f"skill:{manifest.id}:{reference.path.lower()}",
                        version=reference.version,
                        trust_level="CONTROLLED",
                        priority=500,
                        content=reference_content,
                    )
                )
                loaded_references += 1
            records.append(
                SkillLoadRecord(
                    skill_id=manifest.id,
                    skill_version=manifest.version,
                    skill_manifest_hash=manifest_hash,
                    disclosure_level=3 if loaded_references else 2,
                    effective_tool_names=tuple(sorted(manifest_tools)),
                    load_reason=(
                        f"server task profile {task_profile}; "
                        f"{loaded_references} triggered reference(s)"
                    ),
                )
            )
        return SkillSelection(
            skill_fragments=tuple(skill_fragments),
            reference_fragments=tuple(reference_fragments),
            records=tuple(records),
        )

    def _root_entries(self) -> tuple[Path, ...]:
        try:
            entries = tuple(sorted(self.root.iterdir(), key=lambda path: path.name))
        except OSError as error:
            raise SkillRegistryError("skill resource root is unavailable") from error
        for entry in entries:
            if entry.is_symlink():
                raise SkillRegistryError("skill resource symlink is invalid")
            if not entry.is_dir() or not (entry / "skill.json").is_file():
                raise SkillRegistryError("unknown skill resource in root")
            if (entry / "skill.json").is_symlink():
                raise SkillRegistryError("skill manifest symlink is invalid")
        return entries

    def _read_manifest_for_id(self, skill_id: str) -> SkillManifest:
        if not isinstance(skill_id, str) or not _SKILL_ID.fullmatch(skill_id):
            raise SkillRegistryError("skill id is invalid")
        skill_dir = self._resolve_path(skill_id, skill_id, directory=True)
        return self._read_manifest(skill_dir / "skill.json", expected_id=skill_id)

    def _read_manifest(self, path: Path, *, expected_id: str) -> SkillManifest:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SkillRegistryError("skill manifest is unavailable") from error
        manifest_data = _parse_manifest_json(raw)
        try:
            manifest = SkillManifest.model_validate(manifest_data)
        except ValueError as error:
            message = str(error)
            if set(manifest_data) - _MANIFEST_FIELDS:
                reason = "manifest contains unsupported fields"
            elif manifest_data.get("version") != SUPPORTED_SKILL_VERSION:
                reason = "skill version is unsupported"
            elif "path" in message:
                reason = "skill path is invalid"
            elif "hash" in message:
                reason = "skill hash is invalid"
            elif "allowlist" in message:
                reason = "skill allowed_tools must be a read-only allowlist"
            else:
                reason = "skill manifest is invalid"
            raise SkillRegistryError(reason) from error
        if manifest.id != expected_id:
            raise SkillRegistryError("skill directory and manifest id differ")
        if path.is_symlink():
            raise SkillRegistryError("skill manifest symlink is invalid")
        self._resolve_path(manifest.body_path, manifest.id)
        for reference in manifest.references:
            self._resolve_path(reference.path, manifest.id)
        return manifest

    def _resolve_path(
        self,
        relative: str,
        skill_id: str,
        *,
        directory: bool = False,
    ) -> Path:
        if (
            not isinstance(relative, str)
            or not relative
            or not _RELATIVE_PATH.fullmatch(relative)
            or relative.startswith("/")
            or "\\" in relative
            or "://" in relative
            or any(part in {".", ".."} for part in Path(relative).parts)
        ):
            raise SkillRegistryError("skill path is invalid")
        skill_root = self.root / skill_id
        candidate = skill_root / relative if relative != skill_id else self.root / relative
        try:
            cursor = self.root
            for part in candidate.relative_to(self.root).parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise SkillRegistryError("skill path symlink is invalid")
            resolved = candidate.resolve(strict=True)
        except SkillRegistryError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise SkillRegistryError("skill path is unavailable") from error
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise SkillRegistryError("skill path escapes resource root") from error
        if directory and not resolved.is_dir():
            raise SkillRegistryError("skill directory is invalid")
        if not directory and not resolved.is_file():
            raise SkillRegistryError("skill resource is not a file")
        return resolved

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SkillRegistryError("skill resource text is invalid") from error
        if not text or len(text) > 16_000:
            raise SkillRegistryError("skill resource is too large")
        return text

    @staticmethod
    def _verify_hash(path: Path, expected: str, reason: str) -> None:
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError) as error:
            raise SkillRegistryError("skill resource cannot be hashed") from error
        if actual != expected:
            raise SkillRegistryError(reason)

    @staticmethod
    def _manifest_hash(manifest: SkillManifest) -> str:
        return hashlib.sha256(
            canonical_json(manifest.model_dump(mode="json")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _reference_triggered(
        reference: SkillReference,
        task_profile: str,
        allowed_tools: frozenset[str],
    ) -> bool:
        return (
            (not reference.task_profiles or task_profile in reference.task_profiles)
            and frozenset(reference.required_tools).issubset(allowed_tools)
        )
