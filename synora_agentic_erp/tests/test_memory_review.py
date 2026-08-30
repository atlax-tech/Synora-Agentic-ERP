"""Real Frappe persistence, permission, and review tests for Phase 8 T03."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from uuid import uuid4

import frappe
from frappe.model.document import Document
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import (
    get_memory_review_candidate,
    list_memory_review_queue,
    review_memory_candidate,
)
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.memory.service import (
    SERVICE_FLAG,
    list_review_queue,
    review_candidate,
)

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
_UNSET = object()


def _future(days: int = 30) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _past() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


class TestMemoryReview(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.db.delete("Synora Memory Record")
        frappe.set_user("Administrator")
        super().tearDown()

    def _candidate(
        self,
        *,
        initiator: str = BUYER,
        kind: str = "SEMANTIC",
        content: str = "approved replenishment SOP",
        expires_at: str | object | None = _UNSET,
        supersedes_memory: str | None = None,
        digest: str | None = None,
        memory_version: int = 1,
    ) -> Document:
        values = {
            "doctype": "Synora Memory Record",
            "kind": kind,
            "state": "CANDIDATE",
            "initiator": initiator,
            "company_scope": COMPANY,
            "warehouse_scope": WAREHOUSE,
            "scope_run": None,
            "source_run": None,
            "source_claim_id": f"claim-{uuid4().hex}",
            "source_revision": "erpnext-sop-v1",
            "content": content,
            "content_classification": "UNTRUSTED",
            "digest": digest or hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "memory_version": memory_version,
            "state_version": 1,
            "supersedes_memory": supersedes_memory,
        }
        if expires_at is not _UNSET:
            values["expires_at"] = expires_at
        else:
            values["expires_at"] = _future()
        doc = frappe.get_doc(values)
        doc.flags[SERVICE_FLAG] = True
        return doc.insert(ignore_permissions=True)

    def test_valid_durable_kinds_persist_but_working_does_not(self) -> None:
        for kind in ("EPISODIC", "SEMANTIC", "PROCEDURAL"):
            doc = self._candidate(kind=kind)
            self.assertEqual(doc.kind, kind)
            self.assertEqual(doc.state, "CANDIDATE")
        with self.assertRaises(frappe.ValidationError):
            self._candidate(kind="WORKING")

    def test_invariants_digest_and_episodic_expiry_fail_closed(self) -> None:
        with self.assertRaises(frappe.ValidationError):
            self._candidate(digest="0" * 64)
        with self.assertRaises(frappe.ValidationError):
            self._candidate(kind="EPISODIC", expires_at=None)
        with self.assertRaises(frappe.ValidationError):
            self._candidate(content="x" * 32_001)

    def test_direct_mutation_and_uncontrolled_state_change_are_rejected(self) -> None:
        doc = self._candidate()
        doc.content = "tampered"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

        doc = frappe.get_doc("Synora Memory Record", doc.name)
        doc.state = "APPROVED"
        doc.state_version = 2
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

    def test_owner_scope_visibility_and_opaque_foreign_lookup(self) -> None:
        doc = self._candidate(kind="EPISODIC")
        frappe.set_user(BUYER)
        queue = list_review_queue(50, 0)
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["items"][0]["name"], doc.name)

        frappe.set_user(VIEWER)
        foreign = list_review_queue(50, 0)
        self.assertEqual(foreign["total"], 0)
        with self.assertRaises(GatewayFault) as raised:
            from synora_agentic_erp.memory.service import get_review_candidate

            get_review_candidate(doc.name)
        self.assertEqual(raised.exception.code, "MEMORY_NOT_AVAILABLE")

    def test_system_manager_reviews_company_scoped_semantic_memory(self) -> None:
        doc = self._candidate(kind="SEMANTIC")
        frappe.set_user("Administrator")
        queue = list_review_queue(50, 0)
        self.assertEqual(queue["total"], 1)
        reviewed = review_candidate(doc.name, "APPROVE", 1)
        self.assertEqual(reviewed["state"], "APPROVED")
        self.assertEqual(reviewed["reviewer"], "Administrator")

    def test_normal_user_cannot_review_semantic_or_procedural_memory(self) -> None:
        semantic = self._candidate(kind="SEMANTIC")
        procedural = self._candidate(kind="PROCEDURAL")
        frappe.set_user(BUYER)
        self.assertEqual(list_review_queue(50, 0)["total"], 0)
        for doc in (semantic, procedural):
            with self.assertRaises(GatewayFault) as raised:
                review_candidate(doc.name, "APPROVE", 1)
            self.assertEqual(raised.exception.code, "MEMORY_NOT_AVAILABLE")

    def test_permission_is_rechecked_for_current_company_and_warehouse(self) -> None:
        doc = self._candidate()
        frappe.set_user(BUYER)
        original = frappe.has_permission

        def deny_scope(doctype: str, ptype: str = "read", **kwargs: object) -> bool:
            if doctype in {"Company", "Warehouse"}:
                return False
            return bool(original(doctype, ptype, **kwargs))

        from unittest.mock import patch

        with patch("frappe.has_permission", side_effect=deny_scope):
            self.assertEqual(list_review_queue(50, 0)["total"], 0)
            with self.assertRaises(GatewayFault) as raised:
                from synora_agentic_erp.memory.service import get_review_candidate

                get_review_candidate(doc.name)
        self.assertEqual(raised.exception.code, "MEMORY_NOT_AVAILABLE")

    def test_authenticated_review_stamps_server_actor_time_and_increments_once(self) -> None:
        doc = self._candidate(kind="EPISODIC")
        frappe.set_user(BUYER)
        result = review_candidate(doc.name, "APPROVE", 1)
        self.assertEqual(result["state"], "APPROVED")
        self.assertEqual(result["state_version"], 2)
        self.assertEqual(result["reviewer"], BUYER)
        self.assertTrue(result["reviewed_at"])

        stored = frappe.get_doc("Synora Memory Record", doc.name)
        self.assertEqual(stored.state, "APPROVED")
        self.assertEqual(stored.state_version, 2)
        self.assertEqual(stored.reviewer, BUYER)

    def test_reject_reason_is_bounded_and_stale_or_repeat_review_conflicts(self) -> None:
        doc = self._candidate(kind="EPISODIC")
        frappe.set_user(BUYER)
        rejected = review_candidate(doc.name, "REJECT", 1, reason="needs a newer SOP")
        self.assertEqual(rejected["state"], "REJECTED")
        self.assertEqual(rejected["review_reason"], "needs a newer SOP")

        with self.assertRaises(GatewayFault) as stale:
            review_candidate(doc.name, "APPROVE", 1)
        self.assertEqual(stale.exception.code, "CONFLICT")

        with self.assertRaises(GatewayFault) as invalid:
            review_candidate(doc.name, "UNKNOWN", 2)
        self.assertEqual(invalid.exception.code, "INVALID_INPUT")

    def test_expired_and_correction_candidates_are_not_reviewed(self) -> None:
        expired = self._candidate(kind="EPISODIC")
        frappe.db.set_value("Synora Memory Record", expired.name, "expires_at", _past())
        frappe.set_user(BUYER)
        self.assertEqual(list_review_queue(50, 0)["total"], 0)
        with self.assertRaises(GatewayFault) as expiry:
            review_candidate(expired.name, "APPROVE", 1)
        self.assertEqual(expiry.exception.code, "CONFLICT")
        self.assertEqual(frappe.get_doc("Synora Memory Record", expired.name).state, "CANDIDATE")

        old = self._candidate(kind="EPISODIC")
        correction = self._candidate(kind="EPISODIC", supersedes_memory=old.name, memory_version=2)
        with self.assertRaises(GatewayFault) as correction_error:
            review_candidate(correction.name, "APPROVE", 1)
        self.assertEqual(correction_error.exception.code, "CONFLICT")

    def test_generic_docperm_and_guest_api_cannot_bypass_service(self) -> None:
        frappe.set_user(BUYER)
        values = {
            "doctype": "Synora Memory Record",
            "kind": "SEMANTIC",
            "state": "CANDIDATE",
            "initiator": BUYER,
            "company_scope": COMPANY,
            "source_claim_id": "direct-create",
            "source_revision": "v1",
            "content": "direct create",
            "content_classification": "UNTRUSTED",
            "digest": hashlib.sha256(b"direct create").hexdigest(),
            "memory_version": 1,
            "state_version": 1,
            "expires_at": _future(),
        }
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc(values).insert()

        frappe.set_user("Guest")
        response = list_memory_review_queue()
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "AUTHENTICATION_REQUIRED")
        response = get_memory_review_candidate("missing")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "AUTHENTICATION_REQUIRED")

    def test_api_review_response_uses_controlled_service(self) -> None:
        doc = self._candidate(kind="EPISODIC")
        frappe.set_user(BUYER)
        queue = list_memory_review_queue(limit=50, offset=0)
        self.assertTrue(queue["ok"])
        self.assertEqual(queue["total"], 1)
        detail = get_memory_review_candidate(doc.name)
        self.assertTrue(detail["ok"])
        reviewed = review_memory_candidate(doc.name, "APPROVE", 1)
        self.assertTrue(reviewed["ok"])
        self.assertEqual(reviewed["memory"]["state"], "APPROVED")
