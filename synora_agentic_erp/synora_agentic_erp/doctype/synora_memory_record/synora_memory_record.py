import hashlib
import json
from typing import Self, cast

import frappe
from frappe.model.document import Document, LazyDocument
from frappe.utils import get_datetime, now_datetime

SERVICE_FLAG = "synora_memory_service"
MAX_CONTENT_LENGTH = 32_000
MAX_SOURCE_LENGTH = 140
MAX_REASON_LENGTH = 2_000

DURABLE_KINDS = frozenset({"EPISODIC", "SEMANTIC", "PROCEDURAL"})
STATES = frozenset({"PENDING", "APPROVED", "REJECTED", "SUPERSEDED", "EXPIRED", "DELETED"})
REVIEW_STATES = frozenset({"APPROVED", "REJECTED"})
REVIEW_FIELDS = frozenset({"state", "state_version", "reviewed_at", "reviewer", "review_reason"})
DELETION_FIELDS = frozenset({"deleted_at", "deleted_by", "deletion_reason"})
LIFECYCLE_FIELDS = REVIEW_FIELDS | DELETION_FIELDS
LIFECYCLE_TRANSITIONS = {
    "PENDING": frozenset({"APPROVED", "REJECTED", "DELETED"}),
    "APPROVED": frozenset({"SUPERSEDED", "DELETED"}),
    "REJECTED": frozenset({"DELETED"}),
    "SUPERSEDED": frozenset({"DELETED"}),
    "EXPIRED": frozenset({"DELETED"}),
    "DELETED": frozenset(),
}
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
        "dedupe_key",
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


def _is_system_manager(actor: str) -> bool:
    try:
        return "System Manager" in frappe.get_roles(actor)
    except Exception:
        return False


def _scope_permission(doctype: str, name: str, actor: str) -> bool:
    if not name or not frappe.db.exists(doctype, name):
        return False
    try:
        return bool(frappe.has_permission(doctype, "read", doc=name, user=actor))
    except Exception:
        return False


def can_review_memory(memory: Document, actor: str) -> bool:
    """Apply the same scope rule to the native Desk and controlled service."""
    if actor == "Guest" or not frappe.db.get_value("User", actor, "enabled"):
        return False
    company = str(memory.company_scope or "")
    if not _scope_permission("Company", company, actor):
        return False
    warehouse = str(memory.warehouse_scope or "")
    if warehouse:
        row = frappe.db.get_value("Warehouse", warehouse, ["company", "disabled"], as_dict=True)
        if (
            not row
            or row.company != company
            or row.disabled
            or not _scope_permission("Warehouse", warehouse, actor)
        ):
            return False
    if memory.kind == "EPISODIC":
        return str(memory.initiator) == actor
    return memory.kind in {"SEMANTIC", "PROCEDURAL"} and _is_system_manager(actor)


def can_read_memory(memory: Document, actor: str) -> bool:
    """Check current read scope; review authority is deliberately narrower."""
    if actor == "Guest" or not frappe.db.get_value("User", actor, "enabled"):
        return False
    company = str(memory.company_scope or "")
    if not _scope_permission("Company", company, actor):
        return False
    warehouse = str(memory.warehouse_scope or "")
    if warehouse:
        row = frappe.db.get_value("Warehouse", warehouse, ["company", "disabled"], as_dict=True)
        if (
            not row
            or str(row.company) != company
            or row.disabled
            or not _scope_permission("Warehouse", warehouse, actor)
        ):
            return False
    if memory.kind == "EPISODIC":
        return str(memory.initiator) == actor
    return memory.kind in {"SEMANTIC", "PROCEDURAL"}


def compute_dedupe_key(memory: Document) -> str:
    """Stable identity hash shared by the service and legacy test fixtures."""
    identity = {
        "kind": str(memory.kind or ""),
        "initiator": str(memory.initiator or ""),
        "company_scope": str(memory.company_scope or ""),
        "warehouse_scope": str(memory.warehouse_scope or ""),
        "scope_run": str(memory.scope_run or ""),
        "source_run": str(memory.source_run or ""),
        "source_claim_id": str(memory.source_claim_id or ""),
        "source_revision": str(memory.source_revision or ""),
        "digest": str(memory.digest or ""),
        "supersedes_memory": str(memory.supersedes_memory or ""),
    }
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _allowed_scope_names(doctype: str, actor: str) -> list[str]:
    try:
        fields = ["name"]
        if doctype == "Warehouse":
            fields += ["disabled"]
        rows = frappe.get_all(
            doctype,
            fields=fields,
            limit_page_length=0,
            ignore_permissions=True,
        )
        names: list[str] = []
        for row in rows:
            if doctype == "Warehouse" and row.disabled:
                continue
            if frappe.has_permission(doctype, "read", doc=row.name, user=actor):
                names.append(str(row.name))
        return names
    except Exception:
        return []


def get_permission_query_conditions(user: str | None = None, **_: object) -> str:
    """Keep native List queries within the same review scope as the service."""
    actor = str(user or getattr(frappe.session, "user", "Guest") or "Guest")
    table = "`tabSynora Memory Record`"
    if actor == "Guest" or not frappe.db.get_value("User", actor, "enabled"):
        return "1=0"
    companies = _allowed_scope_names("Company", actor)
    warehouses = _allowed_scope_names("Warehouse", actor)
    if not companies:
        return "1=0"
    company_sql = ", ".join(frappe.db.escape(name) for name in companies)
    warehouse_sql = ", ".join(frappe.db.escape(name) for name in warehouses)
    scope = (
        f"{table}.company_scope in ({company_sql}) and "
        f"({table}.warehouse_scope is null or {table}.warehouse_scope = '' "
        f"or {table}.warehouse_scope in ({warehouse_sql or frappe.db.escape('__none__')}))"
    )
    expiry = (
        f"(({table}.kind = 'EPISODIC' and {table}.expires_at is not null and "
        f"{table}.expires_at > NOW()) or ({table}.kind in ('SEMANTIC', 'PROCEDURAL') and "
        f"({table}.expires_at is null or {table}.expires_at > NOW())))"
    )
    common = (
        f"{table}.state = 'PENDING' and "
        f"({table}.supersedes_memory is null or {table}.supersedes_memory = '') and "
        f"{expiry} and ({scope})"
    )
    episodic = f"({table}.kind = 'EPISODIC' and {table}.initiator = {frappe.db.escape(actor)})"
    if not _is_system_manager(actor):
        return f"({common}) and {episodic}"
    durable = f"{table}.kind in ('SEMANTIC', 'PROCEDURAL')"
    return f"({common}) and ({episodic} or {durable})"


def is_expired(doc: Document) -> bool:
    if doc.kind == "EPISODIC" and not doc.expires_at:
        return True
    if not doc.expires_at:
        return False
    try:
        return bool(get_datetime(doc.expires_at) <= now_datetime())
    except TypeError:
        return True
    except ValueError:
        return True


def _native_read_allowed(name: str) -> bool:
    """Make native Form loading indistinguishable from an unknown record."""
    row = frappe.db.get_value(
        "Synora Memory Record",
        name,
        [
            "kind",
            "state",
            "initiator",
            "company_scope",
            "warehouse_scope",
            "supersedes_memory",
            "expires_at",
        ],
        as_dict=True,
    )
    if not row:
        return True
    return _pending_and_unexpired(row) and can_review_memory(
        row, str(frappe.session.user or "Guest")
    )


def _pending_and_unexpired(doc: Document) -> bool:
    if str(doc.state or "") != "PENDING" or doc.supersedes_memory:
        return False
    return not is_expired(doc)


def has_permission(
    doc: Document | None, ptype: str = "read", user: str | None = None, **_: object
) -> bool:
    """Prevent native Form/API reads from bypassing the scoped review service."""
    if ptype not in {"read", "select"} or doc is None:
        return True
    actor = str(user or getattr(frappe.session, "user", "Guest") or "Guest")
    return _pending_and_unexpired(doc) and can_review_memory(doc, actor)


class SynoraMemoryRecord(Document):  # type: ignore[misc]
    def load_from_db(self) -> Self:
        if (
            self.name
            and not self.flags.ignore_permissions
            and not isinstance(self, LazyDocument)
            and not getattr(frappe.flags, "synora_memory_service_read", False)
            and not _native_read_allowed(str(self.name))
        ):
            frappe.throw(
                "Memory record is not available",
                frappe.DoesNotExistError(doctype=self.doctype),
            )
        return cast(Self, super().load_from_db())

    def has_permission(
        self, permtype: str = "read", *, debug: bool = False, user: str | None = None
    ) -> bool:
        """Keep native Form permission checks scoped even for Frappe Administrator."""
        if permtype in {"read", "select"} and not self.flags.ignore_permissions:
            return has_permission(self, permtype, user=user)
        return bool(super().has_permission(permtype, debug=debug, user=user))

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
        is_tombstone = str(self.state or "") == "DELETED" and not self.content
        if (
            not isinstance(self.content, str)
            or (not self.content and not is_tombstone)
            or len(self.content) > MAX_CONTENT_LENGTH
        ):
            _fail("Memory content is required")
        for field in ("source_revision", "source_run", "source_claim_id", "supersedes_memory"):
            value = getattr(self, field, None)
            if value is not None and len(str(value)) > MAX_SOURCE_LENGTH:
                _fail("Memory source identity is too long")
        if self.content_classification != "UNTRUSTED":
            _fail("Memory content must remain untrusted")
        if self.deletion_reason and len(str(self.deletion_reason)) > MAX_REASON_LENGTH:
            _fail("Memory deletion reason is too long")
        if not _valid_digest(self.digest):
            _fail("Memory digest is invalid")
        expected_digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content and self.digest != expected_digest:
            _fail("Memory digest does not match content")
        dedupe_key = getattr(self, "dedupe_key", None)
        if not dedupe_key:
            # Existing rows created before the unique field was introduced
            # remain migratable; new service rows always supply it.
            dedupe_key = compute_dedupe_key(self)
            self.set("dedupe_key", dedupe_key)
        if not _valid_digest(dedupe_key):
            _fail("Memory dedupe key is invalid")

        memory_version = int(self.memory_version or 0)
        state_version = int(self.state_version or 0)
        if memory_version < 1 or state_version < 1:
            _fail("Memory versions are invalid")
        if self.kind == "EPISODIC" and not self.expires_at:
            _fail("Episodic memory requires an expiry")
        if self.expires_at and self.creation:
            try:
                if get_datetime(self.expires_at) <= get_datetime(self.creation):
                    _fail("Memory expiry must be later than creation")
            except TypeError:
                _fail("Memory expiry is invalid")
            except ValueError:
                _fail("Memory expiry is invalid")
            except OverflowError:
                _fail("Memory expiry is invalid")
        if self.supersedes_memory and memory_version < 2:
            _fail("Correction memory must increment its version")

        if self.is_new():
            if self.state != "PENDING" or state_version != 1:
                _fail("Memory records must start as candidates")
            if not self.supersedes_memory and memory_version != 1:
                _fail("Initial memory records must use version one")
            if (
                self.reviewer
                or self.reviewed_at
                or any(getattr(self, field, None) for field in DELETION_FIELDS)
            ):
                _fail("Candidates cannot have review metadata")
            return

        previous_state = str(self.get_db_value("state") or "")
        previous_version = int(self.get_db_value("state_version") or 0)
        changed_immutable = [field for field in IMMUTABLE_FIELDS if self.has_value_changed(field)]
        if changed_immutable:
            # Deletion is a tombstone operation: only the searchable body may
            # be redacted while identity, digest, source, and scope remain.
            if not (
                self.state == "DELETED"
                and previous_state != "DELETED"
                and set(changed_immutable) <= {"content"}
                and not self.content
            ):
                _fail("Memory content and scope are immutable")

        changed = {field for field in LIFECYCLE_FIELDS if self.has_value_changed(field)}
        if changed and not self.flags.get(SERVICE_FLAG):
            _fail("Memory review changes require the controlled memory service")
        if changed:
            allowed_targets = LIFECYCLE_TRANSITIONS.get(previous_state, frozenset())
            if self.state not in allowed_targets:
                _fail("Memory lifecycle transition is invalid")
            if state_version != previous_version + 1:
                _fail("Memory state version must increase exactly once")
            if self.state in REVIEW_STATES and (not self.reviewer or not self.reviewed_at):
                _fail("Reviewed memory requires server review metadata")
            if self.state not in REVIEW_STATES and any(
                self.has_value_changed(field)
                for field in ("reviewed_at", "reviewer", "review_reason")
            ):
                _fail("Lifecycle transition cannot overwrite review metadata")
            if self.state == "DELETED":
                if not self.deleted_by or not self.deleted_at:
                    _fail("Deleted memory requires deletion audit metadata")
                if str(self.deleted_by) != str(frappe.session.user or "Guest"):
                    _fail("Deletion actor must be the current user")
            elif any(self.has_value_changed(field) for field in DELETION_FIELDS):
                _fail("Deletion audit metadata is immutable")
        elif self.flags.get(SERVICE_FLAG):
            _fail("Memory transition did not change state")

        if self.state == "PENDING" and (self.reviewer or self.reviewed_at):
            _fail("Candidates cannot have review metadata")
        if self.state in REVIEW_STATES and (not self.reviewer or not self.reviewed_at):
            _fail("Reviewed memory requires server review metadata")
        if self.state != "DELETED" and any(getattr(self, field, None) for field in DELETION_FIELDS):
            _fail("Deletion audit metadata is only valid on tombstones")

    def on_trash(self) -> None:
        _fail("Memory records cannot be deleted directly")
