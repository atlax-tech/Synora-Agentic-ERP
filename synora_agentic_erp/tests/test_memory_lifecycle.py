"""T03/P8.1 lifecycle tests for the Frappe-authoritative Memory service."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

import synora_agentic_erp.memory.service as memory_service
from synora_agentic_erp.api import (
    create_memory_candidate as create_memory_candidate_api,
)
from synora_agentic_erp.api import (
    issue_run,
    review_memory_candidate,
    tombstone_memory,
)
from synora_agentic_erp.api import (
    list_visible_memories as list_visible_memories_api,
)
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.memory.service import (
    create_memory_candidate,
    create_memory_correction,
    delete_memory,
    list_visible_memories,
    review_candidate,
)

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"


def _future(days: int = 30) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


class TestMemoryLifecycle(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        frappe.db.delete("Synora Memory Record")
        frappe.db.delete("Synora Agent Run")
        super().tearDown()

    def _run(self, actor: str = BUYER) -> str:
        frappe.set_user(actor)
        result = issue_run(
            COMPANY,
            "capture the approved replenishment SOP",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(result["ok"])
        return str(result["run"]["run_id"])

    def _candidate(
        self, source_run: str, *, content: str = "approved replenishment SOP"
    ) -> dict[str, object]:
        return create_memory_candidate(
            kind="EPISODIC",
            source_run=source_run,
            source_revision="erpnext-sop-v1",
            content=content,
            expires_at=_future(),
        )

    def test_candidate_resolves_source_scope_and_rejects_scope_spoofing(self) -> None:
        run_id = self._run()
        result = self._candidate(run_id)
        memory = result["memory"]
        self.assertTrue(result["created"])
        self.assertEqual(memory["source_run"], run_id)
        self.assertEqual(memory["scope_run"], run_id)
        self.assertEqual(memory["initiator"], BUYER)
        self.assertEqual(memory["company_scope"], COMPANY)
        self.assertEqual(memory["warehouse_scope"], WAREHOUSE)
        with self.assertRaises(GatewayFault) as missing:
            self._candidate(str(uuid4()))
        self.assertEqual(missing.exception.code, "MEMORY_NOT_AVAILABLE")

    def test_exact_duplicate_is_idempotent_and_dedupe_key_is_unique(self) -> None:
        run_id = self._run()
        first = self._candidate(run_id)
        second = self._candidate(run_id)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["memory"]["name"], second["memory"]["name"])
        self.assertEqual(frappe.db.count("Synora Memory Record"), 1)
        self.assertTrue(
            frappe.db.get_value("Synora Memory Record", first["memory"]["name"], "dedupe_key")
        )

    def test_correction_uses_dual_cas_and_supersedes_only_on_approval(self) -> None:
        run_id = self._run()
        original = self._candidate(run_id)
        original = review_candidate(original["memory"]["name"], "APPROVE", 1)
        correction = create_memory_correction(
            predecessor_memory_id=original["name"],
            expected_predecessor_state_version=2,
            source_run=run_id,
            source_revision="erpnext-sop-v2",
            content="approved replenishment SOP with correction",
            expires_at=_future(),
        )
        self.assertTrue(correction["created"])
        candidate = correction["memory"]
        self.assertEqual(candidate["state"], "PENDING")
        self.assertEqual(candidate["memory_version"], 2)
        self.assertEqual(candidate["supersedes_memory"], original["name"])
        with self.assertRaises(GatewayFault) as stale:
            review_candidate(candidate["name"], "APPROVE", 1, expected_predecessor_state_version=1)
        self.assertEqual(stale.exception.code, "CONFLICT")
        self.assertEqual(
            frappe.db.get_value("Synora Memory Record", original["name"], "state"),
            "APPROVED",
        )
        approved = review_candidate(
            candidate["name"],
            "APPROVE",
            1,
            expected_predecessor_state_version=2,
        )
        self.assertEqual(approved["memory"]["state"], "APPROVED")
        self.assertEqual(approved["superseded_memory"]["state"], "SUPERSEDED")

    def test_correction_approval_api_response_is_not_nested(self) -> None:
        run_id = self._run()
        original = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        correction = create_memory_correction(
            predecessor_memory_id=original["name"],
            expected_predecessor_state_version=2,
            source_run=run_id,
            source_revision="erpnext-sop-v2-api",
            content="API correction",
            expires_at=_future(),
        )
        reviewed = review_memory_candidate(
            correction["memory"]["name"],
            "APPROVE",
            1,
            expected_predecessor_state_version=2,
        )
        self.assertTrue(reviewed["ok"])
        self.assertEqual(reviewed["memory"]["state"], "APPROVED")
        self.assertEqual(reviewed["superseded_memory"]["state"], "SUPERSEDED")
        self.assertNotIn("memory", reviewed["memory"])

    def test_unique_conflict_reloads_authorized_winner(self) -> None:
        run_id = self._run()
        winner = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        winner_doc = memory_service._load_memory(str(winner["name"]))

        class InsertConflict:
            flags: ClassVar[dict[str, object]] = {}

            def insert(self, *, ignore_permissions: bool = False) -> object:
                del ignore_permissions
                raise frappe.DuplicateEntryError("dedupe race")

        values = {
            "doctype": "Synora Memory Record",
            "kind": "EPISODIC",
            "state": "PENDING",
            "initiator": BUYER,
            "company_scope": COMPANY,
            "warehouse_scope": WAREHOUSE,
            "scope_run": run_id,
            "source_run": run_id,
            "source_revision": "race",
            "content": "race",
            "content_classification": "UNTRUSTED",
            "digest": "a" * 64,
            "memory_version": 1,
            "state_version": 1,
            "expires_at": _future(),
        }
        with (
            patch.object(
                memory_service,
                "_find_by_dedupe",
                side_effect=[None, winner_doc],
            ),
            patch.object(memory_service.frappe, "get_doc", return_value=InsertConflict()),
        ):
            resolved, created = memory_service._insert_candidate(
                values, str(winner_doc.dedupe_key), BUYER
            )
        self.assertFalse(created)
        self.assertEqual(resolved.name, winner_doc.name)

    def test_unique_conflict_does_not_reveal_unauthorized_winner(self) -> None:
        run_id = self._run()
        winner = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        winner_doc = memory_service._load_memory(str(winner["name"]))

        class InsertConflict:
            flags: ClassVar[dict[str, object]] = {}

            def insert(self, *, ignore_permissions: bool = False) -> object:
                del ignore_permissions
                raise frappe.DuplicateEntryError("dedupe race")

        values = {"doctype": "Synora Memory Record", "content": "race"}
        with (
            patch.object(
                memory_service,
                "_find_by_dedupe",
                side_effect=[None, winner_doc],
            ),
            patch.object(memory_service.frappe, "get_doc", return_value=InsertConflict()),
        ):
            with self.assertRaises(GatewayFault) as unavailable:
                memory_service._insert_candidate(values, str(winner_doc.dedupe_key), VIEWER)
        self.assertEqual(unavailable.exception.code, "MEMORY_NOT_AVAILABLE")

    def test_correction_approval_rolls_back_if_predecessor_save_fails(self) -> None:
        run_id = self._run()
        original = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        correction = create_memory_correction(
            predecessor_memory_id=original["name"],
            expected_predecessor_state_version=2,
            source_run=run_id,
            source_revision="erpnext-sop-v2",
            content="atomic correction",
            expires_at=_future(),
        )
        real_save = memory_service._save_service
        calls = 0

        def fail_on_second_save(doc: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected predecessor failure")
            return real_save(doc)

        with patch.object(memory_service, "_save_service", side_effect=fail_on_second_save):
            with self.assertRaises(RuntimeError):
                review_candidate(
                    correction["memory"]["name"],
                    "APPROVE",
                    1,
                    expected_predecessor_state_version=2,
                )
        self.assertEqual(
            frappe.db.get_value("Synora Memory Record", original["name"], "state"),
            "APPROVED",
        )
        self.assertEqual(
            frappe.db.get_value("Synora Memory Record", correction["memory"]["name"], "state"),
            "PENDING",
        )

    def test_pending_correction_temporarily_hides_predecessor_from_recall(self) -> None:
        run_id = self._run()
        original = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        correction = create_memory_correction(
            predecessor_memory_id=original["name"],
            expected_predecessor_state_version=2,
            source_run=run_id,
            source_revision="erpnext-sop-v2",
            content="pending correction",
            expires_at=_future(),
        )
        self.assertEqual(
            list_visible_memories(company=COMPANY, warehouse=WAREHOUSE, run_id=run_id)["total"],
            0,
        )
        review_candidate(correction["memory"]["name"], "REJECT", 1)
        self.assertEqual(
            list_visible_memories(company=COMPANY, warehouse=WAREHOUSE, run_id=run_id)["total"],
            1,
        )

    def test_rejected_correction_leaves_predecessor_approved(self) -> None:
        run_id = self._run()
        original = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        correction = create_memory_correction(
            predecessor_memory_id=original["name"],
            expected_predecessor_state_version=2,
            source_run=run_id,
            source_revision="erpnext-sop-v2",
            content="rejected correction",
            expires_at=_future(),
        )
        rejected = review_candidate(correction["memory"]["name"], "REJECT", 1)
        self.assertEqual(rejected["state"], "REJECTED")
        self.assertEqual(
            frappe.db.get_value("Synora Memory Record", original["name"], "state"),
            "APPROVED",
        )

    def test_delete_is_a_tombstone_and_recall_is_current_scope_safe(self) -> None:
        run_id = self._run()
        candidate = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        visible = list_visible_memories(company=COMPANY, warehouse=WAREHOUSE, run_id=run_id)
        self.assertEqual(visible["total"], 1)
        deleted = delete_memory(
            candidate["name"], expected_state_version=2, reason="cleanup requested"
        )
        self.assertEqual(deleted["state"], "DELETED")
        self.assertEqual(deleted["deleted_by"], BUYER)
        self.assertEqual(deleted["deletion_reason"], "cleanup requested")
        self.assertTrue(deleted["deleted_at"])
        self.assertEqual(
            frappe.db.exists("Synora Memory Record", candidate["name"]), candidate["name"]
        )
        self.assertEqual(
            list_visible_memories(company=COMPANY, warehouse=WAREHOUSE, run_id=run_id)["total"],
            0,
        )
        with self.assertRaises(GatewayFault) as repeated:
            delete_memory(candidate["name"], expected_state_version=2)
        self.assertEqual(repeated.exception.code, "CONFLICT")

    def test_recall_excludes_pending_expired_and_foreign_records(self) -> None:
        run_id = self._run()
        pending = self._candidate(run_id, content="pending")
        expired = self._candidate(run_id, content="expired")
        frappe.db.set_value(
            "Synora Memory Record",
            expired["memory"]["name"],
            "expires_at",
            (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        approved = review_candidate(
            self._candidate(run_id, content="approved")["memory"]["name"], "APPROVE", 1
        )
        frappe.set_user(VIEWER)
        with self.assertRaises(GatewayFault) as foreign_run:
            list_visible_memories(company=COMPANY, warehouse=WAREHOUSE, run_id=run_id)
        self.assertEqual(foreign_run.exception.code, "MEMORY_NOT_AVAILABLE")
        frappe.set_user(BUYER)
        result = list_visible_memories(company=COMPANY, warehouse=WAREHOUSE, run_id=run_id)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["name"], approved["name"])
        self.assertNotIn(pending["memory"]["name"], {item["name"] for item in result["items"]})

    def test_lifecycle_does_not_write_business_documents(self) -> None:
        doctypes = (
            "Material Request",
            "Purchase Order",
            "Purchase Receipt",
            "Purchase Invoice",
            "Payment Entry",
        )
        before = {doctype: frappe.db.count(doctype) for doctype in doctypes}
        run_id = self._run()
        candidate = review_candidate(self._candidate(run_id)["memory"]["name"], "APPROVE", 1)
        delete_memory(candidate["name"], expected_state_version=2)
        after = {doctype: frappe.db.count(doctype) for doctype in doctypes}
        self.assertEqual(after, before)

    def test_lifecycle_api_routes_remain_scoped_and_typed(self) -> None:
        run_id = self._run()
        created = create_memory_candidate_api(
            kind="EPISODIC",
            source_run=run_id,
            source_revision="erpnext-sop-v1",
            content="API candidate",
            expires_at=_future(),
        )
        self.assertTrue(created["ok"])
        memory_id = created["memory"]["name"]
        reviewed = review_memory_candidate(memory_id, "APPROVE", 1)
        self.assertTrue(reviewed["ok"])
        visible = list_visible_memories_api(
            company=COMPANY,
            warehouse=WAREHOUSE,
            run_id=run_id,
        )
        self.assertTrue(visible["ok"])
        self.assertEqual(visible["total"], 1)
        deleted = tombstone_memory(memory_id, 2)
        self.assertTrue(deleted["ok"])
        self.assertIsNone(deleted["memory"]["content"])
