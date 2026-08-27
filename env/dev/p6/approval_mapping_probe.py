"""Read-only Phase 6 approval/workflow evidence probe.

Run inside a site-aware ``bench console`` session.  The probe deliberately
returns metadata only; it never evaluates Server Script bodies or accepts a
business payload.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "1"
TARGET_DOCTYPES = ("Material Request", "Purchase Order")
USER_ALIASES = {
    "synora-p1-buyer@dev.localhost": "buyer",
    "synora-p1-approver@dev.localhost": "approver",
    "synora-p1-receiver@dev.localhost": "receiver",
    "synora-p1-accountant@dev.localhost": "accountant",
    "synora-p1-viewer@dev.localhost": "viewer",
    "synora-p26-aonly@dev.localhost": "company_a_only",
}
PERMISSION_FIELDS = ("read", "create", "write", "submit", "cancel", "amend")
_FORBIDDEN_KEYS = {"password", "secret", "token", "capability", "api_key", "script"}
_TOP_LEVEL_KEYS = {
    "schema_version",
    "probe",
    "captured_at",
    "site",
    "source_revisions",
    "target_doctypes",
    "workflows",
    "doctype_permissions",
    "users",
    "user_permissions",
    "effective_permissions",
    "server_scripts",
    "permission_hooks",
    "configuration",
    "limitations",
}
_REQUIRED_TOP_LEVEL_KEYS = _TOP_LEVEL_KEYS - {"captured_at"}


def _safe_scalar(value: object, limit: int = 240) -> str | int | float | bool | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:limit]


def _stable_rows(
    rows: Iterable[Mapping[str, object]], keys: tuple[str, ...]
) -> list[dict[str, object]]:
    materialized = [dict(row) for row in rows]
    return sorted(
        materialized,
        key=lambda row: tuple(str(row.get(key, "")) for key in keys),
    )


def _read(call: Callable[[], Any]) -> tuple[str, Any, str | None]:
    try:
        return "OK", call(), None
    except Exception as error:
        return "UNRESOLVED", None, type(error).__name__


def _available_fields(frappe: Any, doctype: str, candidates: Iterable[str]) -> list[str]:
    status, meta, error_type = _read(lambda: frappe.get_meta(doctype))
    if status != "OK":
        raise RuntimeError(error_type or "metadata unavailable")
    available = {field.fieldname for field in meta.fields}
    return [field for field in candidates if field in available]


def _row_dict(row: object) -> dict[str, object]:
    as_dict = getattr(row, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    if isinstance(row, Mapping):
        return dict(row)
    return {}


def _workflow_record(frappe: Any, row: Mapping[str, object]) -> dict[str, object]:
    name = str(row.get("name", ""))
    status, doc, error_type = _read(lambda: frappe.get_doc("Workflow", name))
    record: dict[str, object] = {
        "name_digest": hashlib.sha256(name.encode("utf-8")).hexdigest()[:16],
        "document_type": _safe_scalar(row.get("document_type")),
        "is_active": bool(row.get("is_active")),
        "workflow_state_field": _safe_scalar(row.get("workflow_state_field")),
        "modified": _safe_scalar(row.get("modified")),
    }
    if status != "OK":
        record["status"] = "UNRESOLVED"
        record["error_type"] = error_type
        return record

    def child_rows(
        field: str, allowed: tuple[str, ...], sort_keys: tuple[str, ...]
    ) -> list[dict[str, object]]:
        values = getattr(doc, field, []) or []
        result: list[dict[str, object]] = []
        for value in values:
            raw = _row_dict(value)
            result.append({key: _safe_scalar(raw.get(key)) for key in allowed})
        return _stable_rows(result, sort_keys)

    record["status"] = "OK"
    record["states"] = child_rows(
        "states",
        ("state", "doc_status", "allow_edit", "allow_submit", "allow_cancel", "allow_delete"),
        ("state",),
    )
    record["transitions"] = child_rows(
        "transitions",
        (
            "state",
            "action",
            "next_state",
            "allowed",
            "allow_self_approval",
            "send_email_to_creator",
            "conditions",
            "condition",
        ),
        ("state", "action", "next_state", "allowed"),
    )
    return record


def _get_user_permissions(frappe: Any, user: str) -> list[dict[str, object]]:
    fields = _available_fields(
        frappe,
        "User Permission",
        (
            "allow",
            "for_value",
            "is_default",
            "apply_to_all_doctypes",
            "applicable_for",
            "hide_descendants",
        ),
    )
    rows = frappe.get_all(
        "User Permission",
        filters={"user": user},
        fields=fields,
        order_by="allow asc, for_value asc",
    )
    result = []
    for row in rows:
        result.append(
            {
                "allow": _safe_scalar(row.get("allow")),
                "scope_value": _safe_scalar(row.get("for_value")),
                "is_default": bool(row.get("is_default")),
                "apply_to_all_doctypes": bool(row.get("apply_to_all_doctypes")),
                "applicable_for": _safe_scalar(row.get("applicable_for")),
                "hide_descendants": bool(row.get("hide_descendants")),
            }
        )
    return _stable_rows(result, ("allow", "scope_value", "applicable_for"))


def _permission_snapshot(frappe: Any, user: str, alias: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for doctype in TARGET_DOCTYPES:
        snapshot: dict[str, object] = {"user": alias, "doctype": doctype}
        for ptype in ("read", "create"):
            status, value, error_type = _read(
                lambda doctype=doctype, ptype=ptype: frappe.has_permission(
                    doctype, ptype, user=user
                )
            )
            snapshot[ptype] = bool(value) if status == "OK" else None
            if status != "OK":
                snapshot[f"{ptype}_status"] = "UNRESOLVED"
                snapshot[f"{ptype}_error_type"] = error_type
        result.append(snapshot)
    return result


def _hook_snapshot(frappe: Any) -> dict[str, object]:
    result: dict[str, object] = {}
    for hook_name in ("permission_query_conditions", "has_permission"):
        status, hooks, error_type = _read(lambda hook_name=hook_name: frappe.get_hooks(hook_name))
        if status != "OK":
            result[hook_name] = {"status": "UNRESOLVED", "error_type": error_type}
            continue
        by_doctype: dict[str, list[str]] = {}
        for doctype in TARGET_DOCTYPES:
            values = hooks.get(doctype, []) if isinstance(hooks, Mapping) else []
            if not isinstance(values, (list, tuple, set)):
                values = [values]
            by_doctype[doctype] = sorted(str(value)[:200] for value in values)
        result[hook_name] = {"status": "OK", "by_doctype": by_doctype}
    return result


def _configuration_snapshot(frappe: Any) -> dict[str, object]:
    keys = ("developer_mode", "allow_tests", "server_script_enabled", "disable_document_sharing")
    result: dict[str, object] = {}
    for key in keys:
        value = frappe.conf.get(key)
        result[key] = _safe_scalar(value) if isinstance(value, (bool, int, float, str)) else None
    return result


def validate_probe_output(payload: Mapping[str, object]) -> None:
    unknown = set(payload) - _TOP_LEVEL_KEYS
    missing = _REQUIRED_TOP_LEVEL_KEYS - set(payload)
    if unknown or missing or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("probe schema is invalid")
    if payload.get("probe") != "approval-workflow-mapping":
        raise ValueError("probe name is invalid")
    if payload.get("target_doctypes") != list(TARGET_DOCTYPES):
        raise ValueError("probe target doctypes are invalid")

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in _FORBIDDEN_KEYS or lowered.endswith("_password"):
                    raise ValueError("sensitive field in probe output")
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(payload)


def collect() -> dict[str, object]:
    import frappe

    users = list(USER_ALIASES)
    user_rows = frappe.get_all(
        "User",
        filters={"name": ["in", users]},
        fields=["name", "enabled", "user_type"],
        order_by="name asc",
    )
    present = {str(row.get("name")): row for row in user_rows}

    workflow_fields = _available_fields(
        frappe,
        "Workflow",
        ("name", "document_type", "is_active", "workflow_state_field", "modified"),
    )
    workflow_rows = frappe.get_all(
        "Workflow",
        filters={"document_type": ["in", list(TARGET_DOCTYPES)]},
        fields=workflow_fields,
        order_by="document_type asc, name asc",
    )

    doctype_permissions: dict[str, object] = {}
    for doctype in TARGET_DOCTYPES:
        doc_fields = _available_fields(
            frappe,
            "DocType",
            ("name", "custom", "is_submittable", "module", "modified"),
        )
        doc_rows = frappe.get_all("DocType", filters={"name": doctype}, fields=doc_fields)
        perm_fields = _available_fields(
            frappe,
            "DocPerm",
            (
                "name",
                "role",
                "permlevel",
                "read",
                "create",
                "write",
                "submit",
                "cancel",
                "amend",
                "if_owner",
                "idx",
            ),
        )
        perm_rows = frappe.get_all(
            "DocPerm",
            filters={"parent": doctype, "parenttype": "DocType"},
            fields=perm_fields,
            order_by="permlevel asc, role asc, idx asc",
        )
        doctype_permissions[doctype] = {
            "doctype": [
                {
                    "custom": bool(row.get("custom")),
                    "is_submittable": bool(row.get("is_submittable")),
                    "module": _safe_scalar(row.get("module")),
                    "modified": _safe_scalar(row.get("modified")),
                }
                for row in doc_rows
            ],
            "docperm": [
                {
                    "role": _safe_scalar(row.get("role")),
                    "permlevel": int(row.get("permlevel") or 0),
                    **{field: bool(row.get(field)) for field in PERMISSION_FIELDS},
                    "if_owner": bool(row.get("if_owner")),
                    "idx": int(row.get("idx") or 0),
                }
                for row in perm_rows
            ],
        }
        doctype_permissions[doctype]["docperm"] = _stable_rows(
            doctype_permissions[doctype]["docperm"],
            ("permlevel", "role", "idx"),
        )

    users_output: list[dict[str, object]] = []
    user_permissions: list[dict[str, object]] = []
    effective_permissions: list[dict[str, object]] = []
    limitations: list[str] = []
    for user, alias in USER_ALIASES.items():
        row = present.get(user)
        if row is None:
            users_output.append({"user": alias, "present": False})
            limitations.append(f"test user unavailable: {alias}")
            continue
        status, roles, error_type = _read(lambda user=user: frappe.get_roles(user))
        explicit_status, explicit_rows, explicit_error = _read(
            lambda user=user: frappe.get_all(
                "Has Role",
                filters={"parent": user, "parenttype": "User"},
                fields=["role"],
                order_by="role asc",
            )
        )
        explicit_roles = (
            sorted(str(item.get("role")) for item in explicit_rows)
            if explicit_status == "OK"
            else None
        )
        users_output.append(
            {
                "user": alias,
                "present": True,
                "enabled": bool(row.get("enabled")),
                "user_type": _safe_scalar(row.get("user_type")),
                "roles": sorted(str(role) for role in roles) if status == "OK" else None,
                "roles_status": status,
                "roles_error_type": error_type,
                "explicit_roles": explicit_roles,
                "explicit_roles_status": explicit_status,
                "explicit_roles_error_type": explicit_error,
            }
        )
        permission_rows = _get_user_permissions(frappe, user)
        user_permissions.extend({"user": alias, **permission} for permission in permission_rows)
        effective_permissions.extend(_permission_snapshot(frappe, user, alias))

    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "probe": "approval-workflow-mapping",
        "captured_at": datetime.now(UTC).isoformat(),
        "site": str(getattr(frappe.local, "site", "")),
        "source_revisions": {
            "frappe": os.environ.get("FDP_REV_FRAPPE"),
            "erpnext": os.environ.get("FDP_REV_ERP_NEXT"),
        },
        "target_doctypes": list(TARGET_DOCTYPES),
        "workflows": [_workflow_record(frappe, row) for row in workflow_rows],
        "doctype_permissions": doctype_permissions,
        "users": users_output,
        "user_permissions": _stable_rows(user_permissions, ("user", "allow", "scope_value")),
        "effective_permissions": _stable_rows(effective_permissions, ("user", "doctype")),
        "server_scripts": {
            "status": "UNRESOLVED",
            "reason": "Server Script metadata is intentionally probed by the next read-only query",
        },
        "permission_hooks": _hook_snapshot(frappe),
        "configuration": _configuration_snapshot(frappe),
        "limitations": sorted(set(limitations)),
    }

    script_fields = _available_fields(
        frappe,
        "Server Script",
        (
            "name",
            "script_type",
            "reference_doctype",
            "event_frequency",
            "doctype_event",
            "api_method",
            "allow_guest",
            "disabled",
            "modified",
        ),
    )
    script_status, script_rows, script_error = _read(
        lambda: frappe.get_all("Server Script", fields=script_fields, order_by="name asc")
    )
    if script_status == "OK":
        payload["server_scripts"] = {
            "status": "OK",
            "records": [
                {
                    "name_digest": hashlib.sha256(
                        str(row.get("name", "")).encode("utf-8")
                    ).hexdigest()[:16],
                    "script_type": _safe_scalar(row.get("script_type")),
                    "reference_doctype": _safe_scalar(row.get("reference_doctype")),
                    "event_frequency": _safe_scalar(row.get("event_frequency")),
                    "doctype_event": _safe_scalar(row.get("doctype_event")),
                    "api_method": _safe_scalar(row.get("api_method")),
                    "allow_guest": bool(row.get("allow_guest")),
                    "disabled": bool(row.get("disabled")),
                    "modified": _safe_scalar(row.get("modified")),
                }
                for row in script_rows
            ],
        }
    else:
        payload["server_scripts"] = {"status": "UNRESOLVED", "error_type": script_error}
        payload["limitations"] = sorted(set([*limitations, "Server Script metadata unavailable"]))

    validate_probe_output(payload)
    return payload


def render(payload: Mapping[str, object]) -> str:
    validate_probe_output(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    print(render(collect()))
