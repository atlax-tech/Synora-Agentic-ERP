"""Pure contract checks for the Frappe-authoritative Phase 8 Memory slice."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "synora_agentic_erp"
DOCTYPE_ROOT = APP_ROOT / "synora_agentic_erp" / "doctype" / "synora_memory_record"
PAGE_ROOT = APP_ROOT / "synora_agentic_erp" / "page" / "memory_review"


def _imports(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _doctype() -> dict[str, object]:
    path = DOCTYPE_ROOT / "synora_memory_record.json"
    assert path.is_file()
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _field(meta: dict[str, object], fieldname: str) -> dict[str, object]:
    fields = meta.get("fields")
    assert isinstance(fields, list)
    for field in fields:
        assert isinstance(field, dict)
        if field.get("fieldname") == fieldname:
            return field
    raise AssertionError(f"missing field: {fieldname}")


def test_memory_doctype_matches_runtime_durable_contract() -> None:
    meta = _doctype()
    assert meta["name"] == "Synora Memory Record"
    assert meta["autoname"] == "UUID"
    assert meta["track_changes"] == 1

    kind_options = str(_field(meta, "kind")["options"]).splitlines()
    assert set(kind_options) == {"EPISODIC", "SEMANTIC", "PROCEDURAL"}
    assert "WORKING" not in kind_options

    state_options = str(_field(meta, "state")["options"]).splitlines()
    assert set(state_options) == {
        "PENDING",
        "APPROVED",
        "REJECTED",
        "SUPERSEDED",
        "EXPIRED",
        "DELETED",
    }
    assert str(_field(meta, "content_classification")["options"]) == "UNTRUSTED"
    dedupe = _field(meta, "dedupe_key")
    assert dedupe["hidden"] == 1
    assert dedupe["read_only"] == 1
    assert dedupe["set_only_once"] == 1
    assert dedupe["unique"] == 1


def test_generic_doctype_permissions_cannot_bypass_review_service() -> None:
    meta = _doctype()
    permissions = meta.get("permissions")
    assert permissions == [{"read": 1, "role": "All"}]
    assert not any(
        permission.get("create") or permission.get("write") or permission.get("delete")
        for permission in permissions
        if isinstance(permission, dict)
    )


def test_native_reads_use_server_scope_hooks() -> None:
    hooks = (APP_ROOT / "hooks.py").read_text(encoding="utf-8")
    assert '"Synora Memory Record"' in hooks
    assert "get_permission_query_conditions" in hooks
    assert "has_permission" in hooks


def test_frappe_memory_boundary_has_no_runtime_or_model_dependencies() -> None:
    controller = (DOCTYPE_ROOT / "synora_memory_record.py").read_text(encoding="utf-8")
    service = (APP_ROOT / "memory" / "service.py").read_text(encoding="utf-8")
    forbidden_modules = {
        "agent_runtime",
        "sqlite3",
        "pysqlite3",
        "sqlalchemy",
    }
    assert not (_imports(controller) | _imports(service)) & forbidden_modules
    assert "Provider" not in service
    assert "from synora_agentic_erp.agent" not in service
    assert "agent_runtime" not in service
    assert "gateway.registry" not in service


def test_memory_review_is_not_registered_as_an_agent_or_gateway_tool() -> None:
    registry = (APP_ROOT / "gateway" / "registry.py").read_text(encoding="utf-8")
    tools = (APP_ROOT / "gateway" / "tools.py").read_text(encoding="utf-8")
    assert "memory.visible" not in registry
    assert "memory.visible" not in tools
    assert "review_memory_candidate" not in registry
    assert "review_memory_candidate" not in tools


def test_desk_review_renders_memory_as_text_and_exposes_safe_states() -> None:
    doctype_path = APP_ROOT / "synora_agentic_erp" / "doctype" / "synora_memory_record"
    form = doctype_path / "synora_memory_record.js"
    list_view = doctype_path / "synora_memory_record_list.js"
    compatibility_page = PAGE_ROOT / "memory_review.js"
    assert form.is_file()
    assert list_view.is_file()
    assert compatibility_page.is_file()
    form_source = form.read_text(encoding="utf-8")
    list_source = list_view.read_text(encoding="utf-8")
    page_source = compatibility_page.read_text(encoding="utf-8")
    assert "UNTRUSTED" in form_source
    assert "review_memory_candidate" in form_source
    assert 'frappe.listview_settings["Synora Memory Record"]' in list_source
    assert 'frappe.set_route("List", "Synora Memory Record")' in page_source
    assert "innerHTML" not in form_source
    assert "innerHTML" not in list_source
