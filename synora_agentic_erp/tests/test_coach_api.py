"""T08.2 authenticated Frappe-to-Runtime Coach adapter tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from synora_agentic_erp.api import ask_coach, issue_run, revoke_run
from synora_agentic_erp.coach import service as coach_service

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
RUNTIME_TOKEN = "test-runtime-token"
CLAIM_DOMAIN = b"synora-coach-claim-v1"
CAPABILITY = "A" * 43


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _signature(payload: dict[str, object]) -> str:
    key = hmac.new(RUNTIME_TOKEN.encode(), CLAIM_DOMAIN, hashlib.sha256).digest()
    return hmac.new(key, _canonical(payload).encode(), hashlib.sha256).hexdigest()


def _live_citation(run_id: str, *, citation_id: str = "live-1") -> dict[str, object]:
    return {
        "citation_type": "LIVE_ERP",
        "citation_id": citation_id,
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


def _retrieval_citation(*, citation_id: str = "retrieval-1") -> dict[str, object]:
    return {
        "citation_type": "RETRIEVAL",
        "citation_id": citation_id,
        "chunk_id": "b" * 64,
        "content_digest": "c" * 64,
        "ordinal": 1,
        "source_type": "sop",
        "revision": "v1",
        "erp_version": "16.0",
        "permission_scope": "internal",
    }


def _source_snapshot(run_id: str) -> str:
    return _canonical(
        {
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
    )


def _signed_package(
    run_id: str,
    correlation_id: str,
    *,
    claim_id: str,
    ordinal: int,
    claim_type: str,
    claim_text: str,
    citations: list[dict[str, object]],
) -> dict[str, object]:
    provenance = {"citations": citations}
    package: dict[str, object] = {
        "schema_version": "1",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "claim_id": claim_id,
        "ordinal": ordinal,
        "claim_type": claim_type,
        "claim_text": claim_text,
        "claim_digest": hashlib.sha256(claim_text.encode()).hexdigest(),
        "citation_provenance": provenance,
        "citation_digest": hashlib.sha256(_canonical(provenance).encode()).hexdigest(),
        "source_revision": "f" * 40,
        "source_snapshot": _source_snapshot(run_id),
    }
    package["signature"] = _signature(package)
    return package


def _answer(
    run_id: str,
    correlation_id: str,
    *,
    packages: list[dict[str, object]],
    claims: list[dict[str, object]],
    citations: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "answer_status": "ANSWERED",
        "answer": "\n".join(str(claim["text"]) for claim in claims),
        "claims": claims,
        "citations": citations,
        "refusal_reason": None,
        "retrieval_trace": {
            "selected_chunk_ids": [],
            "selected_content_digests": [],
            "selected_revisions": [],
            "live_fact_digests": ["a" * 64],
            "provider_tools": [],
            "context_fragment_ids": [],
        },
        "token_usage": {"prompt_tokens": 2, "completion_tokens": 3, "reasoning_tokens": 0},
        "latency_ms": 4,
        "validated_claims": packages,
    }


def _unknown_answer(reason: str) -> dict[str, object]:
    return {
        "schema_version": "1",
        "answer_status": "UNKNOWN",
        "answer": "",
        "claims": [],
        "citations": [],
        "refusal_reason": reason,
        "retrieval_trace": {
            "selected_chunk_ids": [],
            "selected_content_digests": [],
            "selected_revisions": [],
            "live_fact_digests": [],
            "provider_tools": [],
            "context_fragment_ids": [],
        },
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0},
        "latency_ms": 0,
        "validated_claims": [],
    }


class TestCoachAPI(FrappeTestCase):  # type: ignore[misc]
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

    def _issue(self, actor: str = BUYER) -> dict[str, object]:
        frappe.set_user(actor)
        result = issue_run(
            COMPANY,
            "answer the current replenishment question",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(result["ok"])
        run = cast(dict[str, object], result["run"])
        return {**run, "correlation_id": result["correlation_id"]}

    def _ask(
        self,
        run: dict[str, object],
        *,
        actor: str = BUYER,
        extra_fields: dict[str, object] | None = None,
        **extra: object,
    ) -> dict[str, Any]:
        frappe.set_user(actor)
        fields = {} if extra_fields is None else dict(extra_fields)
        fields.update(extra)
        return cast(
            dict[str, Any],
            ask_coach(
                run_id=run["run_id"],
                capability=run.get("capability", CAPABILITY),
                question="How many units remain open?",
                current_doctype="Material Request",
                current_name="MAT-MR-0001",
                **fields,
            ),
        )

    def test_guest_is_rejected_before_runtime(self) -> None:
        frappe.set_user("Guest")
        with patch("synora_agentic_erp.coach.service._call_coach_runtime") as runtime:
            response = ask_coach(
                run_id=str(uuid4()),
                capability=CAPABILITY,
                question="What remains?",
                current_doctype="Material Request",
                current_name="MAT-MR-0001",
            )
        self.assertEqual(response["error"]["code"], "AUTHENTICATION_REQUIRED")
        runtime.assert_not_called()

    def test_valid_run_derives_server_correlation_and_persists_safe_provenance(self) -> None:
        run = self._issue()
        run_id = str(run["run_id"])
        correlation_id = str(run["correlation_id"])
        citation = _live_citation(run_id)
        package = _signed_package(
            run_id,
            correlation_id,
            claim_id="claim-1",
            ordinal=1,
            claim_type="ERP_FACT",
            claim_text="open_order_stock_qty=2",
            citations=[citation],
        )
        claim = {
            "claim_id": "claim-1",
            "ordinal": 1,
            "claim_type": "ERP_FACT",
            "text": "open_order_stock_qty=2",
            "citation_refs": ["live-1"],
        }
        with patch(
            "synora_agentic_erp.coach.service._call_coach_runtime",
            return_value=_answer(
                run_id,
                correlation_id,
                packages=[package],
                claims=[claim],
                citations=[citation],
            ),
        ) as runtime:
            response = self._ask(run)

        self.assertTrue(response["ok"])
        self.assertEqual(response["correlation_id"], correlation_id)
        self.assertEqual(response["coach"]["provenance"][0]["claim_id"], "claim-1")
        self.assertNotIn(str(run["capability"]), json.dumps(response))
        self.assertNotIn(RUNTIME_TOKEN, json.dumps(response))
        self.assertEqual(frappe.db.count("Synora Coach Claim"), 1)
        payload = runtime.call_args.args[0]
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "run_id",
                "correlation_id",
                "question",
                "current_document",
                "capability",
            },
        )
        self.assertEqual(payload["correlation_id"], correlation_id)
        self.assertNotIn("company", payload)
        self.assertNotIn("warehouse", payload)

    def test_system_manager_can_access_valid_run_without_overriding_scope(self) -> None:
        run = self._issue()
        correlation_id = str(run["correlation_id"])
        with patch(
            "synora_agentic_erp.coach.service._call_coach_runtime",
            return_value=_unknown_answer("provider detail contains a secret"),
        ) as runtime:
            response = self._ask(run, actor="Administrator")

        self.assertTrue(response["ok"])
        self.assertEqual(response["correlation_id"], correlation_id)
        self.assertEqual(
            response["coach"]["refusal_reason"], "Coach could not produce a grounded answer"
        )
        self.assertNotIn("provider detail", json.dumps(response))
        payload = runtime.call_args.args[0]
        self.assertEqual(payload["correlation_id"], correlation_id)

    def test_foreign_wrong_expired_and_revoked_runs_are_opaque(self) -> None:
        run = self._issue()
        with patch("synora_agentic_erp.coach.service._call_coach_runtime") as runtime:
            foreign = self._ask(run, actor=VIEWER)
            wrong = self._ask({**run, "capability": CAPABILITY}, actor=BUYER)
        self.assertEqual(foreign["error"]["code"], "COACH_RUN_NOT_AVAILABLE")
        self.assertEqual(wrong["error"]["code"], "COACH_RUN_NOT_AVAILABLE")
        runtime.assert_not_called()

        frappe.set_user(BUYER)
        frappe.db.set_value("Synora Agent Run", run["run_id"], "expires_at", now_datetime())
        with patch("synora_agentic_erp.coach.service._call_coach_runtime") as runtime:
            expired = self._ask(run)
        self.assertEqual(expired["error"]["code"], "COACH_RUN_NOT_AVAILABLE")
        runtime.assert_not_called()

        revoked_run = self._issue()
        revoke_run(str(revoked_run["run_id"]), str(revoked_run["correlation_id"]))
        with patch("synora_agentic_erp.coach.service._call_coach_runtime") as runtime:
            revoked = self._ask(revoked_run)
        self.assertEqual(revoked["error"]["code"], "COACH_RUN_NOT_AVAILABLE")
        runtime.assert_not_called()

    def test_caller_cannot_add_identity_or_authority_fields(self) -> None:
        run = self._issue()
        for field in (
            "correlation_id",
            "company",
            "warehouse",
            "facts",
            "retrieval_hits",
            "tools",
            "provider_config",
        ):
            with self.subTest(field=field):
                with patch("synora_agentic_erp.coach.service._call_coach_runtime") as runtime:
                    response = self._ask(run, extra_fields={field: {"untrusted": True}})
                self.assertEqual(response["error"]["code"], "INVALID_INPUT")
                runtime.assert_not_called()

    def test_malformed_runtime_answer_is_not_persisted(self) -> None:
        run = self._issue()
        malformed = _unknown_answer("not used")
        malformed["answer_status"] = "ANSWERED"
        malformed["answer"] = "forged"
        with patch("synora_agentic_erp.coach.service._call_coach_runtime", return_value=malformed):
            response = self._ask(run)
        self.assertEqual(response["error"]["code"], "COACH_RESPONSE_INVALID")
        self.assertEqual(frappe.db.count("Synora Coach Claim"), 0)

    def test_multi_claim_persistence_rolls_back_as_one_unit(self) -> None:
        run = self._issue()
        run_id = str(run["run_id"])
        correlation_id = str(run["correlation_id"])
        live = _live_citation(run_id)
        retrieval = _retrieval_citation()
        claims = [
            {
                "claim_id": "claim-1",
                "ordinal": 1,
                "claim_type": "ERP_FACT",
                "text": "open_order_stock_qty=2",
                "citation_refs": ["live-1"],
            },
            {
                "claim_id": "claim-2",
                "ordinal": 2,
                "claim_type": "RETRIEVED_KNOWLEDGE",
                "text": "The internal SOP requires review.",
                "citation_refs": ["retrieval-1"],
            },
        ]
        packages = [
            _signed_package(
                run_id,
                correlation_id,
                claim_id="claim-1",
                ordinal=1,
                claim_type="ERP_FACT",
                claim_text="open_order_stock_qty=2",
                citations=[live],
            ),
            _signed_package(
                run_id,
                correlation_id,
                claim_id="claim-2",
                ordinal=2,
                claim_type="RETRIEVED_KNOWLEDGE",
                claim_text="The internal SOP requires review.",
                citations=[retrieval],
            ),
        ]
        real_persist = coach_service.persist_coach_claim
        calls = 0

        def persist_once_then_fail(*, validated_claim: object) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_persist(validated_claim=validated_claim)
            raise RuntimeError("simulated second claim failure")

        with (
            patch(
                "synora_agentic_erp.coach.service._call_coach_runtime",
                return_value=_answer(
                    run_id,
                    correlation_id,
                    packages=packages,
                    claims=claims,
                    citations=[live, retrieval],
                ),
            ),
            patch(
                "synora_agentic_erp.coach.service.persist_coach_claim",
                side_effect=persist_once_then_fail,
            ),
        ):
            response = self._ask(run)

        self.assertEqual(response["error"]["code"], "COACH_CLAIMS_NOT_PERSISTED")
        self.assertEqual(frappe.db.count("Synora Coach Claim"), 0)
