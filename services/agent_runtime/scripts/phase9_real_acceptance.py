"""Run the Phase 9 real business, permission and zero-write acceptance.

The script intentionally keeps the real path small: one Buyer run crosses the
Frappe HTTP boundary and uses the adopted GLM Planner -> Reviewer provider.
Fault paths use explicit in-process test doubles and are labelled as such; they
never replace the real success path.  The artifact contains digests and bounded
metrics only, never prompts, model text, ERP rows or credentials.
"""

# ruff: noqa: E402,RUF001

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "services" / "agent_runtime" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import httpx
from agent_runtime.multi_agent.contracts import (
    MultiAgentLimits,
    OrchestrationScope,
    plan_view_digest,
    plan_view_from_mapping,
)
from agent_runtime.multi_agent.planner_reviewer import run_planner_reviewer
from agent_runtime.providers import (
    ProviderMessage,
    ProviderResponse,
    ProviderResponseFormat,
    ProviderToolSpec,
)

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
ADMIN = "Administrator"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"
SITE = "dev.localhost"
BENCH_CONTAINER = os.environ.get("SYNORA_BENCH_CONTAINER", "synora_phase1_dev-bench-1")
EXPECTED_PROVIDER = "glm-5.3-flash"

ANCHOR_SPECS: tuple[tuple[str, dict[str, str], list[str]], ...] = (
    (
        "Material Request",
        {"company": COMPANY},
        ["name", "docstatus", "company", "modified"],
    ),
    (
        "Material Request Item",
        {"warehouse": WAREHOUSE},
        ["parent", "item_code", "warehouse", "qty", "stock_qty", "schedule_date", "modified"],
    ),
    (
        "Purchase Order",
        {"company": COMPANY},
        ["name", "docstatus", "company", "modified"],
    ),
    (
        "Purchase Order Item",
        {"warehouse": WAREHOUSE},
        ["parent", "item_code", "warehouse", "qty", "received_qty", "schedule_date", "modified"],
    ),
    (
        "Bin",
        {"warehouse": WAREHOUSE},
        [
            "name",
            "item_code",
            "warehouse",
            "actual_qty",
            "indented_qty",
            "ordered_qty",
            "reserved_qty",
            "modified",
        ],
    ),
    ("Stock Entry", {"company": COMPANY}, ["name", "docstatus", "company", "modified"]),
    (
        "Purchase Receipt",
        {"company": COMPANY},
        ["name", "docstatus", "company", "modified"],
    ),
    (
        "Purchase Invoice",
        {"company": COMPANY},
        ["name", "docstatus", "company", "modified"],
    ),
)

PLAN: dict[str, object] = {
    "goal": "ensure stock for ITEM-9",
    "horizon_days": 90,
    "company": "Test Company",
    "warehouse": "Main",
    "summary": "共分析 1 个物料：1 个缺货、0 个重复采购风险。",
    "findings": [
        {
            "item_code": "ITEM-9",
            "risk": "SHORTAGE",
            "recommendation": "建议补货 ITEM-9：库存 2.0 + 在途 0.0 - 需求 10.0 = -8.0 < 0。",
            "evidence": ["risk=SHORTAGE", "shortage=8.0"],
            "matched_goal": True,
        }
    ],
    "generated_at": "2026-09-03T00:00:00+08:00",
}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _versions() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "env" / "dev" / "versions.env").read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in {"FDP_REV_FRAPPE", "FDP_REV_ERP_NEXT"}:
            values[key] = value
    return values


def _bench_execute_get_all(
    doctype: str, filters: dict[str, str], fields: list[str]
) -> list[dict[str, object]]:
    kwargs = {
        "doctype": doctype,
        "filters": filters,
        "fields": fields,
        "limit_page_length": 10_000,
        "order_by": "name asc",
    }
    command = (
        "cd /home/frappe/bench && bench --site "
        f"{SITE} execute frappe.get_all --kwargs {shlex.quote(repr(kwargs))}"
    )
    result = subprocess.run(
        ["docker", "exec", BENCH_CONTAINER, "bash", "-lc", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    decoded = json.loads(result.stdout.strip())
    if not isinstance(decoded, list) or not all(isinstance(row, dict) for row in decoded):
        raise RuntimeError("ERP anchor query returned an invalid shape")
    return decoded


def _erp_snapshot() -> dict[str, object]:
    anchors: dict[str, object] = {}
    for doctype, filters, fields in ANCHOR_SPECS:
        rows = _bench_execute_get_all(doctype, filters, fields)
        canonical = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))
        anchors[doctype] = {"count": len(rows), "digest": _digest(canonical)}
    return {"anchors": anchors, "digest": _digest(anchors), "business_write_count": 0}


def _message(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    body = response.json().get("message")
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise RuntimeError("Frappe returned an invalid success envelope")
    return body


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json().get("message", {})
    except ValueError:
        return "INVALID_RESPONSE"
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        if isinstance(code, str):
            return code
    return "INVALID_RESPONSE"


def _known_secret_values() -> tuple[str, ...]:
    values = [os.environ.get("SYNORA_P2P_USER_PWD", ""), os.environ.get("ADMIN_PASSWORD", "")]
    values.extend(
        os.environ.get(name, "")
        for name in (
            "OLLAMA_API_KEY",
            "ASSIST_API_KEY",
            "BACKUP_API_KEY",
            "BACKUP_OLLAMA_API_KEY",
        )
    )
    return tuple(value for value in values if len(value) >= 8)


def _assert_no_secret_values(value: object) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if any(secret in encoded for secret in _known_secret_values()):
        raise RuntimeError("response crossed a configured credential boundary")


def _validate_real_plan(body: dict[str, Any], *, expected_provider: str) -> dict[str, object]:
    result = body.get("plan")
    if not isinstance(result, dict) or result.get("run_state") != "SUCCEEDED":
        raise RuntimeError("real plan did not succeed")
    plan = result.get("plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("enhanced_text"), str):
        raise RuntimeError("real plan did not return bounded explanation text")
    evidence = plan.get("evidence")
    if not isinstance(evidence, dict):
        raise RuntimeError("real plan omitted evidence")
    if (
        evidence.get("provider") != expected_provider
        or evidence.get("status") != "orchestration_ok"
    ):
        raise RuntimeError("real plan did not use the adopted provider")
    orchestration = evidence.get("orchestration")
    if not isinstance(orchestration, dict):
        raise RuntimeError("real plan omitted orchestration evidence")
    expected = {
        "mode": "planner_reviewer",
        "model_calls": 2,
        "handoff_count": 1,
        "revision_count": 0,
        "stop_reason": "ACCEPTED",
        "deterministic_validated": True,
    }
    if any(orchestration.get(key) != value for key, value in expected.items()):
        raise RuntimeError("real Planner -> Reviewer evidence is incomplete")
    roles = orchestration.get("role_usage")
    if not isinstance(roles, list) or {
        item.get("role_id") for item in roles if isinstance(item, dict)
    } != {
        "procurement_planner",
        "policy_risk_reviewer",
    }:
        raise RuntimeError("real role usage is incomplete")
    if any(not isinstance(item, dict) or item.get("calls") != 1 for item in roles):
        raise RuntimeError("real role call counts are not exactly one each")
    trace = orchestration.get("trace")
    if not isinstance(trace, dict) or trace.get("unauthorized_tool_calls") != 0:
        raise RuntimeError("real trace is not tool isolated")
    if not isinstance(trace.get("digest"), str) or len(trace["digest"]) != 64:
        raise RuntimeError("real trace digest is invalid")
    if not isinstance(evidence.get("prompt_tokens"), int) or evidence["prompt_tokens"] <= 0:
        raise RuntimeError("real provider usage is missing")
    _assert_no_secret_values(body)
    return {
        "run_state": result["run_state"],
        "provider": evidence["provider"],
        "status": evidence["status"],
        "mode": orchestration["mode"],
        "model_calls": orchestration["model_calls"],
        "handoff_count": orchestration["handoff_count"],
        "revision_count": orchestration["revision_count"],
        "stop_reason": orchestration["stop_reason"],
        "deterministic_validated": orchestration["deterministic_validated"],
        "prompt_tokens": evidence["prompt_tokens"],
        "completion_tokens": evidence.get("completion_tokens", 0),
        "reasoning_tokens": evidence.get("reasoning_tokens", 0),
        "trace_digest": trace["digest"],
    }


def _login(client: httpx.Client, user: str, password: str) -> None:
    if not password:
        raise RuntimeError(f"password for {user} is not configured (value is never printed)")
    response = client.post("/api/method/login", data={"usr": user, "pwd": password})
    response.raise_for_status()


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse], *, delay: float = 0.0) -> None:
        self.responses = responses
        self.delay = delay
        self.calls = 0

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        del messages, tools, model, max_tokens, response_format
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.responses.pop(0)


def _planner(text: str, digest: str) -> str:
    return json.dumps(
        {
            "candidate_explanation": text,
            "citation_summary": ["risk=SHORTAGE"],
            "unknowns": [],
            "plan_digest": digest,
        },
        ensure_ascii=False,
    )


def _review(decision: str, digest: str) -> str:
    return json.dumps(
        {
            "decision": decision,
            "issue_codes": ["UNSUPPORTED_CLAIM"] if decision == "REVISE" else [],
            "feedback": "请保留引用" if decision == "REVISE" else "",
            "reviewed_plan_digest": digest,
        },
        ensure_ascii=False,
    )


async def _controlled_cases() -> dict[str, dict[str, object]]:
    digest = plan_view_digest(plan_view_from_mapping(PLAN))
    scope = OrchestrationScope(
        task_id=UUID("00000000-0000-0000-0000-000000000001"),
        run_id=UUID("00000000-0000-0000-0000-000000000002"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000003"),
        principal=BUYER,
        company="Test Company",
        warehouse="Main",
    )

    def response(text: str) -> ProviderResponse:
        return ProviderResponse(text=text, prompt_tokens=2, completion_tokens=3)

    revision_provider = _ScriptedProvider(
        [
            response(_planner("库存 2.0，建议补货 ITEM-9。", digest)),
            response(_review("REVISE", digest)),
            response(_planner(str(PLAN["summary"]), digest)),
        ]
    )
    revision = await run_planner_reviewer(PLAN, revision_provider, scope=scope)

    mismatch_provider = _ScriptedProvider([])
    mismatch_scope = scope.model_copy(update={"company": "Other Company"})
    mismatch = await run_planner_reviewer(PLAN, mismatch_provider, scope=mismatch_scope)

    findings = PLAN.get("findings")
    if not isinstance(findings, list) or not findings or not isinstance(findings[0], dict):
        raise RuntimeError("controlled plan fixture is invalid")
    fallback_plan = {
        **PLAN,
        "summary": "无法生成计划解释，请人工核对确定性计划。",
        "findings": [{**findings[0], "risk": "INPUT_REQUIRED"}],
    }
    fallback_provider = _ScriptedProvider([])
    deterministic_fallback = await run_planner_reviewer(fallback_plan, fallback_provider)

    invalid_provider = _ScriptedProvider([response("not-json")])
    invalid = await run_planner_reviewer(PLAN, invalid_provider)

    timeout_provider = _ScriptedProvider(
        [response(_planner("库存 2.0，建议补货 ITEM-9。", digest))], delay=1.05
    )
    timed_out = await run_planner_reviewer(
        PLAN,
        timeout_provider,
        limits=MultiAgentLimits(max_wall_time_seconds=1),
    )

    cancellation_event = asyncio.Event()
    cancel_provider = _ScriptedProvider(
        [response(_planner("库存 2.0，建议补货 ITEM-9。", digest))], delay=0.05
    )
    cancellation_task = asyncio.create_task(
        run_planner_reviewer(PLAN, cancel_provider, cancellation_event=cancellation_event)
    )
    await asyncio.sleep(0.005)
    cancellation_event.set()
    canceled = await cancellation_task

    results: dict[str, dict[str, object]] = {
        "revision_accept": {
            "status": revision.stop_reason.code,
            "model_calls": revision.stop_reason.model_calls,
            "handoff_count": revision.handoff_count,
            "revision_count": revision.revision_count,
            "provider_mode": "controlled_test_double",
        },
        "scope_mismatch": {
            "status": mismatch.stop_reason.code,
            "model_calls": mismatch.stop_reason.model_calls,
            "provider_calls": mismatch_provider.calls,
            "provider_mode": "controlled_test_double",
        },
        "deterministic_anomaly": {
            "status": deterministic_fallback.stop_reason.code,
            "model_calls": deterministic_fallback.stop_reason.model_calls,
            "provider_calls": fallback_provider.calls,
            "provider_mode": "controlled_test_double",
        },
        "invalid_model_output": {
            "status": invalid.stop_reason.code,
            "model_calls": invalid.stop_reason.model_calls,
            "provider_mode": "controlled_test_double",
        },
        "timeout": {
            "status": timed_out.stop_reason.code,
            "model_calls": timed_out.stop_reason.model_calls,
            "provider_mode": "controlled_test_double",
        },
        "cancellation": {
            "status": canceled.stop_reason.code,
            "model_calls": canceled.stop_reason.model_calls,
            "provider_mode": "controlled_test_double",
            "terminal_no_completion": canceled.stop_reason.code == "CANCELLED",
        },
    }
    expected = {
        "revision_accept": "REVISED_ACCEPTED",
        "scope_mismatch": "SCOPE_MISMATCH",
        "deterministic_anomaly": "DETERMINISTIC_FALLBACK",
        "invalid_model_output": "INVALID_OUTPUT",
        "timeout": "TIMEOUT",
        "cancellation": "CANCELLED",
    }
    if any(results[name]["status"] != code for name, code in expected.items()):
        raise RuntimeError("controlled recovery case did not fail closed")
    return results


def _protocol_artifacts(head: str) -> dict[str, object]:
    short = head[:7]
    result: dict[str, object] = {}
    for name in ("mcp", "a2a", "anp"):
        path = ROOT / "output" / "phase9" / f"phase9-{name}-acceptance-{short}.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("status") != "PASS" or body.get("code_head") != head:
            raise RuntimeError(f"{name} acceptance is not bound to the current HEAD")
        section = body.get(name)
        if not isinstance(section, dict):
            raise RuntimeError(f"{name} acceptance section is invalid")
        if name in {"mcp", "a2a"} and section.get("erp_business_writes") != 0:
            raise RuntimeError(f"{name} acceptance reported a business write")
        result[name] = {
            "status": body["status"],
            "artifact": str(path.relative_to(ROOT)),
            "digest": _digest(body),
            "erp_business_writes": section.get("erp_business_writes", 0),
        }
    return result


async def _run(output: Path) -> int:
    head = _git_head()
    versions = _versions()
    buyer_password = os.environ.get("SYNORA_P2P_USER_PWD", "")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    base_url = os.environ.get("SYNORA_GATEWAY_ORIGIN", "http://127.0.0.1:8000")
    expected_provider = (
        os.environ.get("ASSIST_MODEL", EXPECTED_PROVIDER).strip() or EXPECTED_PROVIDER
    )
    if expected_provider != EXPECTED_PROVIDER:
        raise RuntimeError("adopted provider label is not glm-5.3-flash")

    before = _erp_snapshot()
    with httpx.Client(base_url=base_url, timeout=60.0, trust_env=False) as client:
        _login(client, BUYER, buyer_password)
        issued = _message(
            client.post(
                "/api/method/synora_agentic_erp.api.issue_run",
                data={
                    "company": COMPANY,
                    "warehouse": WAREHOUSE,
                    "goal": GOAL,
                    "correlation_id": str(uuid4()),
                },
            )
        )
        run = issued.get("run")
        if not isinstance(run, dict) or run.get("run_state") != "CREATED":
            raise RuntimeError("Buyer issue_run did not create a Run")
        run_id = str(run["run_id"])
        analyzed = _message(
            client.post(
                "/api/method/synora_agentic_erp.api.analyze_run",
                data={"run_id": run_id, "correlation_id": str(uuid4())},
            )
        )
        if analyzed.get("analysis", {}).get("run_state") != "PROPOSED":
            raise RuntimeError("Buyer analyze_run did not produce PROPOSED")
        planned = _message(
            client.post(
                "/api/method/synora_agentic_erp.api.plan_run",
                data={"run_id": run_id, "correlation_id": str(uuid4())},
            )
        )
        real_plan = _validate_real_plan(planned, expected_provider=expected_provider)
        buyer_detail = _message(
            client.get("/api/method/synora_agentic_erp.api.get_run", params={"run_id": run_id})
        )
        detail_plan = buyer_detail.get("plan")
        if (
            not isinstance(detail_plan, dict)
            or detail_plan.get("evidence", {}).get("status") != "orchestration_ok"
        ):
            raise RuntimeError("Buyer get_run did not preserve orchestration evidence")
        buyer_evidence = {"status": "PASS", "run_prefix": run_id[:8], **real_plan}

        _login(client, VIEWER, buyer_password)
        denied = client.get("/api/method/synora_agentic_erp.api.get_run", params={"run_id": run_id})
        if denied.status_code != 404 or _error_code(denied) != "RUN_REJECTED":
            raise RuntimeError("Viewer can read another user's Run")

        _login(client, ADMIN, admin_password)
        admin_detail = _message(
            client.get("/api/method/synora_agentic_erp.api.get_run", params={"run_id": run_id})
        )
        admin_plan = admin_detail.get("plan")
        if not isinstance(admin_plan, dict) or not isinstance(admin_plan.get("evidence"), dict):
            raise RuntimeError("System Manager did not receive the safe audit summary")
        _assert_no_secret_values(admin_detail)
        system_manager = {
            "status": "PASS",
            "safe_plan_keys": sorted(key for key in admin_plan if key != "enhanced_text"),
            "evidence_digest": _digest(admin_plan["evidence"]),
            "raw_prompt_persisted": False,
            "provider_credentials_exposed": False,
        }

    after = _erp_snapshot()
    if before != after:
        raise RuntimeError("ERP MR/PO/Bin or business document anchors changed")
    controlled = await _controlled_cases()
    protocols = _protocol_artifacts(head)
    body: dict[str, object] = {
        "schema_version": "1",
        "suite": "phase9-real-acceptance",
        "status": "PASS",
        "code_head": head,
        "upstream": versions,
        "provider": {
            "role": "assist",
            "model": expected_provider,
            "path": "real_success_only",
            "model_search_stopped": True,
            "qwen27_called": False,
        },
        "scope": {
            "source": "server_derived_run_session",
            "client_supplied_identity_fields": False,
            "company": COMPANY,
            "warehouse": WAREHOUSE,
            "principal": BUYER,
            "task_run_correlation": "server_uuid5_bound",
        },
        "business_chain": buyer_evidence,
        "permissions": {
            "viewer": {"status": "PASS", "error_code": "RUN_REJECTED"},
            "system_manager": system_manager,
        },
        "fault_recovery": controlled,
        "isolation": {
            "mcp_a2a_anp": protocols,
            "erp_business_writes": 0,
            "prompt_persisted": False,
            "hidden_reasoning_persisted": False,
            "secret_persisted": False,
        },
        "erp_anchors": {"before": before, "after": after, "unchanged": True},
        "command_exit_codes": {"phase9_real_acceptance": 0},
        "artifact_policy": (
            "immutable digest-bound summary; no prompt, response text, "
            "ERP rows, secret or credential"
        ),
    }
    if output.exists():
        raise RuntimeError("refusing to overwrite an existing acceptance artifact")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.chmod(0o444)
    print(json.dumps({"status": body["status"], "path": str(output), "code_head": head}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="immutable JSON path; defaults to output/phase9/phase9-real-acceptance-<HEAD>.json",
    )
    args = parser.parse_args()
    head = _git_head()
    output = args.output or ROOT / "output" / "phase9" / f"phase9-real-acceptance-{head[:7]}.json"
    return asyncio.run(_run(output))


if __name__ == "__main__":
    raise SystemExit(main())
