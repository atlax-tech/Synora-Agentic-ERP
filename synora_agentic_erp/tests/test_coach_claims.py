"""T07 Frappe-authoritative Coach Claim provenance tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import issue_run
from synora_agentic_erp.coach.service import persist_coach_claim, resolve_coach_claim
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.memory.service import create_memory_candidate

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SERVICE_FLAG = "synora_coach_claim_service"
RUNTIME_TOKEN = "test-runtime-token"
CLAIM_DOMAIN = b"synora-coach-claim-v1"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _signature(payload: dict[str, object]) -> str:
    key = hmac.new(RUNTIME_TOKEN.encode(), CLAIM_DOMAIN, hashlib.sha256).digest()
    return hmac.new(key, _canonical(payload).encode(), hashlib.sha256).hexdigest()


class TestCoachClaims(FrappeTestCase):  # type: ignore[misc]
    def setUp(self) -> None:
        super().setUp()
        self._token_patch = patch.dict(os.environ, {"SYNORA_RUNTIME_TOKEN": RUNTIME_TOKEN})
        self._token_patch.start()

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        frappe.db.delete("Synora Coach Claim")
        frappe.db.delete("Synora Agent Run")
        self._token_patch.stop()
        super().tearDown()

    def _run(self, actor: str = BUYER) -> str:
        frappe.set_user(actor)
        result = issue_run(
            COMPANY,
            "answer the current replenishment question",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(result["ok"])
        return str(result["run"]["run_id"])

    def _signed_package(self, run_id: str, **overrides: object) -> dict[str, object]:
        correlation_id = str(frappe.db.get_value("Synora Agent Run", run_id, "correlation_id"))
        source_snapshot = {
            "run_id": run_id,
            "document": {"doctype": "Material Request", "name": "MAT-MR-0001"},
            "scope": {
                "company": COMPANY,
                "warehouse": WAREHOUSE,
                "coverage": "WAREHOUSE_SCOPED",
            },
            "state_version": 3,
            "captured_at": "2026-08-30 12:00:00",
            "source_modified_at": "2026-08-30 11:59:00",
            "frappe_revision": "f" * 40,
            "erpnext_revision": "e" * 40,
        }
        values: dict[str, object] = {
            "schema_version": "1",
            "run_id": run_id,
            "correlation_id": correlation_id,
            "claim_id": "claim-1",
            "ordinal": 1,
            "claim_type": "ERP_FACT",
            "claim_text": "2 units remain open.",
            "citation_provenance": {
                "citations": [
                    {
                        "citation_type": "LIVE_ERP",
                        "citation_id": "live-1",
                        "run_id": run_id,
                        "document_doctype": "Material Request",
                        "document_name": "MAT-MR-0001",
                        "state_version": 3,
                        "captured_at": "2026-08-30 12:00:00",
                        "source_modified_at": "2026-08-30 11:59:00",
                        "frappe_revision": "f" * 40,
                        "erpnext_revision": "e" * 40,
                        "fact_fields": ["open_order_stock_qty"],
                        "fact_digest": "a" * 64,
                    }
                ]
            },
            "source_revision": "f" * 40,
            "source_snapshot": _canonical(source_snapshot),
        }
        values.update(overrides)
        values["claim_digest"] = hashlib.sha256(str(values["claim_text"]).encode()).hexdigest()
        values["citation_digest"] = hashlib.sha256(
            _canonical(values["citation_provenance"]).encode()
        ).hexdigest()
        unsigned = dict(values)
        values["signature"] = _signature(unsigned)
        return values

    def _persist(self, run_id: str, **overrides: object) -> dict[str, object]:
        return persist_coach_claim(validated_claim=self._signed_package(run_id, **overrides))

    def test_service_only_claim_is_idempotent_and_resolves_scope(self) -> None:
        run_id = self._run()
        first = self._persist(run_id)
        second = self._persist(run_id)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["name"], second["name"])
        resolved = resolve_coach_claim(
            first["name"],
            run_id=run_id,
            source_revision="f" * 40,
            expected_claim_digest=first["claim_digest"],
            expected_citation_digest=first["citation_digest"],
        )
        self.assertEqual(resolved["name"], first["name"])
        self.assertEqual(resolved["initiator"], BUYER)
        self.assertEqual(resolved["company_scope"], COMPANY)
        self.assertEqual(resolved["warehouse_scope"], WAREHOUSE)

    def test_direct_insert_and_unknown_or_cross_scope_lookup_are_opaque(self) -> None:
        run_id = self._run()
        claim = self._persist(run_id)
        values = {
            "doctype": "Synora Coach Claim",
            "run": run_id,
            "initiator": BUYER,
            "company_scope": COMPANY,
            "warehouse_scope": WAREHOUSE,
            "claim_digest": "b" * 64,
            "citation_digest": "c" * 64,
            "source_revision": "f" * 40,
            "source_snapshot": "{}",
            "dedupe_key": "d" * 64,
        }
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc(values).insert()

        with self.assertRaises(GatewayFault) as unknown:
            resolve_coach_claim(str(uuid4()), run_id=run_id)
        self.assertEqual(unknown.exception.code, "COACH_CLAIM_NOT_AVAILABLE")

        frappe.set_user(VIEWER)
        with self.assertRaises(GatewayFault) as foreign:
            resolve_coach_claim(claim["name"], run_id=run_id)
        self.assertEqual(foreign.exception.code, "COACH_CLAIM_NOT_AVAILABLE")

    def test_claim_persistence_requires_runtime_signature_and_rejects_tampering(self) -> None:
        run_id = self._run()
        package = self._signed_package(run_id)
        forged = dict(package)
        forged["claim_text"] = "20 units remain open."
        with self.assertRaises(GatewayFault) as tampered:
            persist_coach_claim(validated_claim=forged)
        self.assertEqual(tampered.exception.code, "INVALID_INPUT")

        unsigned = dict(package)
        unsigned.pop("signature")
        with self.assertRaises(GatewayFault) as missing:
            persist_coach_claim(validated_claim=unsigned)
        self.assertEqual(missing.exception.code, "INVALID_INPUT")

        marked = dict(package)
        marked["validated_by_runtime"] = True
        with self.assertRaises(GatewayFault) as marker:
            persist_coach_claim(validated_claim=marked)
        self.assertEqual(marker.exception.code, "INVALID_INPUT")

        with patch.dict(os.environ, {"SYNORA_RUNTIME_TOKEN": ""}):
            with self.assertRaises(GatewayFault) as unavailable:
                persist_coach_claim(validated_claim=package)
        self.assertEqual(unavailable.exception.code, "UNAVAILABLE")

    def test_digest_and_run_revision_mismatch_fail_closed(self) -> None:
        run_id = self._run()
        claim = self._persist(run_id)
        with self.assertRaises(GatewayFault) as digest:
            resolve_coach_claim(claim["name"], run_id=run_id, expected_claim_digest="0" * 64)
        self.assertEqual(digest.exception.code, "COACH_CLAIM_NOT_AVAILABLE")
        with self.assertRaises(GatewayFault) as revision:
            resolve_coach_claim(claim["name"], run_id=run_id, source_revision="old")
        self.assertEqual(revision.exception.code, "COACH_CLAIM_NOT_AVAILABLE")

    def test_live_citation_fields_are_revalidated_at_frappe_boundary(self) -> None:
        run_id = self._run()
        package = self._signed_package(run_id)
        provenance = json.loads(json.dumps(package["citation_provenance"]))
        provenance["citations"][0]["fact_fields"] = ["not_a_current_field"]
        with self.assertRaises(GatewayFault) as invalid:
            self._persist(run_id, citation_provenance=provenance)
        self.assertEqual(invalid.exception.code, "INVALID_INPUT")

        malformed = json.loads(json.dumps(package["citation_provenance"]))
        malformed["citations"][0]["fact_fields"] = [{}]
        with self.assertRaises(GatewayFault) as malformed_error:
            self._persist(run_id, citation_provenance=malformed)
        self.assertEqual(malformed_error.exception.code, "INVALID_INPUT")

    def test_memory_source_claim_must_be_frappe_claim_bound_to_same_run_and_revision(self) -> None:
        run_id = self._run()
        with self.assertRaises(GatewayFault) as arbitrary:
            create_memory_candidate(
                kind="EPISODIC",
                source_run=run_id,
                source_revision="f" * 40,
                content="arbitrary claim id must not become authority",
                expires_at="2099-01-01 00:00:00",
                source_claim_id="claim-not-authoritative",
            )
        self.assertEqual(arbitrary.exception.code, "MEMORY_NOT_AVAILABLE")

        claim = self._persist(run_id)
        candidate = create_memory_candidate(
            kind="EPISODIC",
            source_run=run_id,
            source_revision="f" * 40,
            content="2 units remain open.",
            expires_at="2099-01-01 00:00:00",
            source_claim_id=claim["name"],
        )
        self.assertEqual(candidate["memory"]["source_claim_id"], claim["name"])
        with self.assertRaises(GatewayFault) as mismatch:
            create_memory_candidate(
                kind="EPISODIC",
                source_run=run_id,
                source_revision="different-revision",
                content="same claim cannot be relabeled",
                expires_at="2099-01-01 00:00:00",
                source_claim_id=claim["name"],
            )
        self.assertEqual(mismatch.exception.code, "MEMORY_NOT_AVAILABLE")

    def test_claim_lifecycle_does_not_mutate_erp_business_documents(self) -> None:
        doctypes = (
            "Material Request",
            "Purchase Order",
            "Purchase Receipt",
            "Purchase Invoice",
            "Payment Entry",
        )
        before = {doctype: frappe.db.count(doctype) for doctype in doctypes}
        self._persist(self._run())
        after = {doctype: frappe.db.count(doctype) for doctype in doctypes}
        self.assertEqual(after, before)
