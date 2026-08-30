import hashlib

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

SERVICE_FLAG = "synora_memory_service"
MAX_CONTENT_LENGTH = 32_000
MAX_SOURCE_LENGTH = 140

DURABLE_KINDS = frozenset({"EPISODIC", "SEMANTIC", "PROCEDURAL"})
STATES = frozenset({"CANDIDATE", "APPROVED", "REJECTED", "SUPERSEDED", "EXPIRED", "DELETED"})
REVIEW_STATES = frozenset({"APPROVED", "REJECTED"})
REVIEW_FIELDS = frozenset({"state", "state_version", "reviewed_at", "reviewer", "review_reason"})
IMMUTABLE_FIELDS = frozenset(
    {
        "kind",
        "initiator",
        "company_scope",
        "warehouse_scope",
        "scope_run",
        "source_run",
        "source_claim_id",
        "source_revision",
        "content",
        "content_classification",
        "digest",
        "memory_version",
        "supersedes_memory",
        "expires_at",
    }
)


def _fail(message: str) -> None:
    frappe.throw(message)


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class SynoraMemoryRecord(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            _fail("Memory records require the controlled memory service")

        if self.kind not in DURABLE_KINDS:
            _fail("Working memory cannot be persisted")
        if self.state not in STATES:
            _fail("Memory state is invalid")
        if not self.initiator or not self.company_scope or not self.source_revision:
            _fail("Memory identity and scope are required")
        if not self.source_run and not self.source_claim_id:
            _fail("Memory requires a source run or claim")
        if (
            not isinstance(self.content, str)
            or not self.content
            or len(self.content) > MAX_CONTENT_LENGTH
        ):
            _fail("Memory content is required")
        for field in ("source_revision", "source_run", "source_claim_id", "supersedes_memory"):
            value = getattr(self, field, None)
            if value is not None and len(str(value)) > MAX_SOURCE_LENGTH:
                _fail("Memory source identity is too long")
        if self.content_classification != "UNTRUSTED":
            _fail("Memory content must remain untrusted")
        if not _valid_digest(self.digest):
            _fail("Memory digest is invalid")
        expected_digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.digest != expected_digest:
            _fail("Memory digest does not match content")

        memory_version = int(self.memory_version or 0)
        state_version = int(self.state_version or 0)
        if memory_version < 1 or state_version < 1:
            _fail("Memory versions are invalid")
        if self.kind == "EPISODIC" and not self.expires_at:
            _fail("Episodic memory requires an expiry")
        if self.expires_at and self.creation:
            if get_datetime(self.expires_at) <= get_datetime(self.creation):
                _fail("Memory expiry must be later than creation")
        if self.supersedes_memory and memory_version < 2:
            _fail("Correction memory must increment its version")

        if self.is_new():
            if self.state != "CANDIDATE" or state_version != 1:
                _fail("Memory records must start as candidates")
            if not self.supersedes_memory and memory_version != 1:
                _fail("Initial memory records must use version one")
            if self.reviewer or self.reviewed_at:
                _fail("Candidates cannot have review metadata")
            return

        changed_immutable = [field for field in IMMUTABLE_FIELDS if self.has_value_changed(field)]
        if changed_immutable:
            _fail("Memory content and scope are immutable")

        changed = {field for field in REVIEW_FIELDS if self.has_value_changed(field)}
        if changed and not self.flags.get(SERVICE_FLAG):
            _fail("Memory review changes require the controlled memory service")
        if changed and not changed <= REVIEW_FIELDS:
            _fail("Memory contains an unsupported review change")
        if changed:
            previous_state = str(self.get_db_value("state") or "")
            previous_version = int(self.get_db_value("state_version") or 0)
            if previous_state != "CANDIDATE" or self.state not in REVIEW_STATES:
                _fail("Memory review transition is invalid")
            if state_version != previous_version + 1:
                _fail("Memory state version must increase exactly once")
            if not self.reviewer or not self.reviewed_at:
                _fail("Reviewed memory requires server review metadata")
        elif self.flags.get(SERVICE_FLAG):
            _fail("Memory transition did not change state")

        if self.state == "CANDIDATE" and (self.reviewer or self.reviewed_at):
            _fail("Candidates cannot have review metadata")
        if self.state in REVIEW_STATES and (not self.reviewer or not self.reviewed_at):
            _fail("Reviewed memory requires server review metadata")

    def on_trash(self) -> None:
        _fail("Memory records cannot be deleted directly")
