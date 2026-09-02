"""Reproducible real Bench -> Runtime -> Provider Coach acceptance.

The runner deliberately keeps the suite small and immutable.  ``emit-manifest``
binds the fixed case specification to the current commit, the existing dirty
worktree fingerprint, a redacted environment snapshot, and a parent manifest.
``representative`` is a non-formal smoke path; ``run`` executes the twelve
cases once in the manifest order and writes only redacted evidence.

The script never prints credentials or raw provider responses.  It may create
Synora Run/Coach Result records, but it only verifies that the ERPNext business
anchors remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[3]
CASE_SPEC_PATH = Path(__file__).with_name("phase8_coach_cases.json")
EXPECTED_ORDER = (
    "G1",
    "G2",
    "G3",
    "G4",
    "C1",
    "C2",
    "C3",
    "S1",
    "S2",
    "S3",
    "U1",
    "U2",
)
PROVIDER_CASE_IDS = frozenset({"G1", "G2", "G3", "G4", "C1", "C2", "C3", "S3", "U1", "U2"})
BYPASS_CASE_IDS = frozenset({"S1", "S2"})
BUYER = "synora-p1-buyer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
WRONG_DOCUMENT = {"doctype": "Material Request", "name": "MAT-MR-NOT-A-REAL-ID"}
MR_ANCHOR = "MR"
PO_ANCHOR = "PO"
COACH_PATH = "/api/method/synora_agentic_erp.api.start_erp_coach"
ISSUE_PATH = "/api/method/synora_agentic_erp.api.issue_run"
REVOKE_PATH = "/api/method/synora_agentic_erp.api.revoke_run"
EXECUTE_PATH = "/api/method/synora_agentic_erp.api.execute"
RUNTIME_COACH_PATH = "/coach/answer"
SECRET_ENV_NAMES = (
    "SYNORA_RUNTIME_TOKEN",
    "SYNORA_P2P_USER_PWD",
    "OLLAMA_API_KEY",
    "ASSIST_API_KEY",
    "BACKUP_API_KEY",
    "BACKUP_OLLAMA_API_KEY",
)
MODEL_ENV_NAMES = (
    "OLLAMA_MODEL",
    "ASSIST_MODEL",
    "BACKUP_MODEL",
    "BACKUP_OLLAMA_MODEL",
)
URL_ENV_NAMES = (
    "OLLAMA_BASE_URL",
    "ASSIST_BASE_URL",
    "BACKUP_BASE_URL",
    "BACKUP_OLLAMA_BASE_URL",
    "SYNORA_GATEWAY_ORIGIN",
    "SYNORA_RUNTIME_URL",
)
ROLE_ENV = {
    "primary": ("OLLAMA_BASE_URL", "OLLAMA_API_KEY", "OLLAMA_MODEL"),
    "assist": ("ASSIST_BASE_URL", "ASSIST_API_KEY", "ASSIST_MODEL"),
    "backup": ("BACKUP_BASE_URL", "BACKUP_API_KEY", "BACKUP_MODEL"),
    "last_local": (
        "BACKUP_OLLAMA_BASE_URL",
        "BACKUP_OLLAMA_API_KEY",
        "BACKUP_OLLAMA_MODEL",
    ),
}


class Blocked(RuntimeError):
    """A precondition or evidence gate failed without exposing sensitive data."""


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Blocked("json_input_unavailable") from error
    if not isinstance(value, dict):
        raise Blocked("json_input_not_object")
    return value


def _dotenv_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path) -> None:
    """Load only process-local values needed by this runner, without printing."""
    allowed = set(SECRET_ENV_NAMES) | set(MODEL_ENV_NAMES) | set(URL_ENV_NAMES)
    allowed.update({name for role in ROLE_ENV.values() for name in role})
    allowed.add("SYNORA_MODEL_PROXY")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise Blocked("env_file_unavailable") from error
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, raw = stripped.partition("=")
        if separator and key.strip() in allowed:
            os.environ.setdefault(key.strip(), _dotenv_value(raw))


def _redacted_url(value: str | None) -> dict[str, object] | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return {"configured": True, "valid_shape": False}
    return {
        "configured": True,
        "valid_shape": bool(parsed.scheme and parsed.hostname),
        "scheme": parsed.scheme or None,
        "host": parsed.hostname or None,
        "port": parsed.port,
    }


def environment_snapshot() -> dict[str, object]:
    roles: dict[str, object] = {}
    for role, (base_name, key_name, model_name) in ROLE_ENV.items():
        roles[role] = {
            "base_url": _redacted_url(os.environ.get(base_name)),
            "api_key_present": bool(os.environ.get(key_name, "").strip()),
            "model": os.environ.get(model_name, "").strip() or None,
        }
    return {
        "roles": roles,
        "runtime": {
            "gateway_origin": _redacted_url(os.environ.get("SYNORA_GATEWAY_ORIGIN")),
            "runtime_url": _redacted_url(os.environ.get("SYNORA_RUNTIME_URL")),
            "runtime_token_present": bool(os.environ.get("SYNORA_RUNTIME_TOKEN", "").strip()),
        },
    }


def git_status() -> str:
    return subprocess.check_output(
        ["git", "status", "--short"], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    )


def git_fingerprint(status: str | None = None) -> str:
    return digest_bytes((git_status() if status is None else status).encode("utf-8"))


def current_git() -> dict[str, str]:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    return {"branch": branch, "head": head}


def case_spec_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("suite", "case_order", "anchors", "s3_retrieval", "cases")
    return {key: spec[key] for key in keys}


def validate_case_spec(spec: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if tuple(spec.get("case_order", ())) != EXPECTED_ORDER:
        raise Blocked("case_order_mismatch")
    cases = spec.get("cases")
    if (
        not isinstance(cases, list)
        or tuple(case.get("id") for case in cases if isinstance(case, dict)) != EXPECTED_ORDER
    ):
        raise Blocked("case_definition_mismatch")
    anchors = spec.get("anchors")
    if not isinstance(anchors, dict) or not {MR_ANCHOR, PO_ANCHOR} <= set(anchors):
        raise Blocked("anchor_definition_mismatch")
    s3 = spec.get("s3_retrieval")
    if not isinstance(s3, dict) or not all(
        isinstance(s3.get(key), str) and s3[key]
        for key in ("chunk_id", "content_digest", "revision", "query")
    ):
        raise Blocked("retrieval_definition_mismatch")
    payload = case_spec_payload(spec)
    return dict(spec), digest(payload)


def load_case_spec(path: Path = CASE_SPEC_PATH) -> tuple[dict[str, Any], str]:
    return validate_case_spec(load_json(path))


def _parent_manifest_hash(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise Blocked("parent_manifest_unavailable") from error
    return digest_bytes(raw)


def build_manifest(spec: Mapping[str, Any], case_spec_sha: str, parent_sha: str) -> dict[str, Any]:
    status = git_status()
    git = current_git()
    if git["branch"] != "main":
        raise Blocked("manifest_requires_main_branch")
    return {
        "schema_version": 1,
        "suite": spec["suite"],
        "case_order": list(EXPECTED_ORDER),
        "cases": spec["cases"],
        "anchors": spec["anchors"],
        "s3_retrieval": spec["s3_retrieval"],
        "case_spec_sha256": case_spec_sha,
        "parent_manifest_sha256": parent_sha,
        "baseline": {
            **git,
            "status_sha256": git_fingerprint(status),
            "repo_evaluation_changes": "none",
        },
        "environment_snapshot": environment_snapshot(),
        "global_constraints": {
            "attempts_per_case": 1,
            "erp_business_write": False,
            "mock_substitution": False,
            "provider_tools": [],
            "selective_rerun": False,
        },
    }


def write_immutable(path: Path, value: Mapping[str, object]) -> None:
    if path.exists():
        raise Blocked("immutable_output_already_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value) + "\n", encoding="utf-8")
    path.chmod(0o444)


def emit_manifest(
    case_spec_path: Path, parent_path: Path, output_path: Path, env_path: Path | None = None
) -> int:
    if env_path is not None:
        load_env_file(env_path)
    spec, case_spec_sha = load_case_spec(case_spec_path)
    manifest = build_manifest(spec, case_spec_sha, _parent_manifest_hash(parent_path))
    write_immutable(output_path, manifest)
    print(
        json.dumps(
            {
                "manifest": str(output_path),
                "manifest_sha256": digest_bytes(output_path.read_bytes()),
                "case_spec_sha256": case_spec_sha,
                "case_order": list(EXPECTED_ORDER),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def validate_manifest(manifest: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    _, expected_spec_sha = validate_case_spec(spec)
    if manifest.get("schema_version") != 1:
        raise Blocked("manifest_schema_mismatch")
    if tuple(manifest.get("case_order", ())) != EXPECTED_ORDER:
        raise Blocked("manifest_order_mismatch")
    cases = manifest.get("cases")
    if (
        not isinstance(cases, list)
        or tuple(case.get("id") for case in cases if isinstance(case, dict)) != EXPECTED_ORDER
    ):
        raise Blocked("manifest_cases_mismatch")
    if manifest.get("case_spec_sha256") != expected_spec_sha:
        raise Blocked("manifest_case_spec_drift")
    parent_sha = manifest.get("parent_manifest_sha256")
    if not isinstance(parent_sha, str) or len(parent_sha) != 64:
        raise Blocked("manifest_parent_missing")
    constraints = manifest.get("global_constraints")
    if constraints != {
        "attempts_per_case": 1,
        "erp_business_write": False,
        "mock_substitution": False,
        "provider_tools": [],
        "selective_rerun": False,
    }:
        raise Blocked("manifest_constraints_mismatch")
    baseline = manifest.get("baseline")
    if not isinstance(baseline, dict):
        raise Blocked("manifest_baseline_missing")
    git = current_git()
    if baseline.get("branch") != git["branch"] or baseline.get("head") != git["head"]:
        raise Blocked("manifest_git_binding_mismatch")
    if git["branch"] != "main":
        raise Blocked("formal_run_requires_main_branch")
    status = git_status()
    if baseline.get("status_sha256") != git_fingerprint(status):
        raise Blocked("manifest_worktree_binding_mismatch")
    return digest_bytes(canonical(manifest).encode("utf-8"))


def _safe_response_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except ValueError, json.JSONDecodeError:
        return None


def _response_message(response: httpx.Response) -> dict[str, Any] | None:
    body = _safe_response_json(response)
    if not isinstance(body, dict):
        return None
    value = body.get("message")
    return value if isinstance(value, dict) else None


def _response_code(response: httpx.Response) -> str:
    body = _safe_response_json(response)
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, dict) and isinstance(message.get("error"), dict):
            return str(message["error"].get("code", "UNKNOWN"))[:80]
        if isinstance(body.get("error"), dict):
            return str(body["error"].get("code", "UNKNOWN"))[:80]
    return "HTTP_" + str(response.status_code)


def _secret_values() -> tuple[bytes, ...]:
    values = []
    for name in SECRET_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            values.append(value.encode("utf-8"))
    return tuple(values)


def response_leaks_secret(response: httpx.Response, extra: str = "") -> bool:
    raw = response.content
    values = (*_secret_values(), extra.encode("utf-8") if extra else b"")
    return any(value and value in raw for value in values)


def _required_password() -> str:
    password = os.environ.get("SYNORA_P2P_USER_PWD", "").strip()
    token = os.environ.get("SYNORA_RUNTIME_TOKEN", "").strip()
    if not password:
        raise Blocked("buyer_password_unavailable")
    if not token:
        raise Blocked("runtime_token_unavailable")
    return password


def token_binding_preflight() -> None:
    token = os.environ.get("SYNORA_RUNTIME_TOKEN", "").strip()
    command = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "exec",
        "-T",
        "bench",
        "bash",
        "-lc",
        'printf %s "$SYNORA_RUNTIME_TOKEN"',
    ]
    try:
        bench_token = subprocess.check_output(
            command, cwd=ROOT / "env/dev", text=True, stderr=subprocess.STDOUT
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise Blocked("bench_token_unavailable") from error
    if not bench_token or digest_bytes(token.encode()) != digest_bytes(bench_token.encode()):
        raise Blocked("runtime_token_binding_mismatch")


def login(client: httpx.Client, password: str) -> None:
    response = client.post("/api/method/login", data={"usr": BUYER, "pwd": password})
    body = _safe_response_json(response)
    if (
        response.status_code >= 400
        or not isinstance(body, dict)
        or body.get("message") != "Logged In"
    ):
        raise Blocked("buyer_login_failed")
    if not client.cookies.get("sid"):
        raise Blocked("buyer_session_missing")


def issue_run(client: httpx.Client, goal: str) -> tuple[str, str, str]:
    response = client.post(
        ISSUE_PATH,
        data={
            "company": COMPANY,
            "warehouse": WAREHOUSE,
            "goal": goal,
            "correlation_id": str(uuid4()),
        },
    )
    message = _response_message(response)
    if response.status_code >= 400 or message is None or message.get("ok") is not True:
        raise Blocked("issue_run_failed_" + _response_code(response))
    run = message.get("run")
    if not isinstance(run, dict):
        raise Blocked("issue_run_shape")
    run_id = run.get("run_id")
    capability = run.get("capability")
    correlation = message.get("correlation_id")
    if not isinstance(run_id, str) or not isinstance(capability, str) or len(capability) != 43:
        raise Blocked("issue_run_fields")
    try:
        UUID(run_id)
        UUID(str(correlation))
    except ValueError, TypeError, AttributeError:
        raise Blocked("issue_run_uuid_fields") from None
    return run_id, capability, str(correlation)


def revoke_run(client: httpx.Client, run_id: str) -> None:
    response = client.post(REVOKE_PATH, data={"run_id": run_id, "correlation_id": str(uuid4())})
    message = _response_message(response)
    if response.status_code >= 400 or message is None or message.get("ok") is not True:
        raise Blocked("revoke_run_failed_" + _response_code(response))


def gateway_read(
    base_url: str, run_id: str, capability: str, correlation: str, tool: str, name: str
) -> dict[str, Any]:
    payload = {
        "schema_version": "1",
        "run_id": run_id,
        "capability": capability,
        "correlation_id": correlation,
        "tool": {"name": tool, "version": "1", "input": {"name": name}},
    }
    with httpx.Client(base_url=base_url, timeout=45, trust_env=False) as bare:
        response = bare.post(EXECUTE_PATH, json=payload)
    message = _response_message(response)
    if response.status_code >= 400 or message is None or message.get("ok") is not True:
        raise Blocked("gateway_read_failed_" + _response_code(response))
    return message


def anchor_from_message(message: Mapping[str, Any], anchor: Mapping[str, Any]) -> dict[str, Any]:
    data = message.get("data")
    snapshot = message.get("snapshot")
    tool = message.get("tool")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise Blocked("anchor_data_shape")
    if not isinstance(snapshot, dict) or not isinstance(tool, dict):
        raise Blocked("anchor_contract_shape")
    row = data[0]
    field = anchor["field_name"]
    if not isinstance(field, str) or field not in row:
        raise Blocked("anchor_field_missing")
    return {
        "doctype": anchor["doctype"],
        "name": anchor["name"],
        "field_name": field,
        "value": str(row[field]),
        "row_digest": digest(row),
        "state_version": message.get("state_version"),
        "captured_at": snapshot.get("captured_at"),
        "source_modified_at": snapshot.get("source_modified_at"),
        "frappe_revision": snapshot.get("frappe_revision"),
        "erpnext_revision": snapshot.get("erpnext_revision"),
        "tool": tool.get("name"),
    }


def capture_anchors(
    client: httpx.Client, base_url: str, spec: Mapping[str, Any], purpose: str
) -> dict[str, dict[str, Any]]:
    anchors = spec["anchors"]
    assert isinstance(anchors, dict)
    run_id, capability, correlation = issue_run(client, purpose)
    try:
        mr_anchor = anchors[MR_ANCHOR]
        po_anchor = anchors[PO_ANCHOR]
        assert isinstance(mr_anchor, dict) and isinstance(po_anchor, dict)
        mr_message = gateway_read(
            base_url,
            run_id,
            capability,
            correlation,
            "material_request.current",
            str(mr_anchor["name"]),
        )
        po_message = gateway_read(
            base_url,
            run_id,
            capability,
            correlation,
            "purchase_order.current",
            str(po_anchor["name"]),
        )
    finally:
        revoke_run(client, run_id)
    mr = anchor_from_message(mr_message, mr_anchor)
    po = anchor_from_message(po_message, po_anchor)
    if mr["value"] != mr_anchor["expected_value"] or po["value"] != po_anchor["expected_value"]:
        raise Blocked("erp_anchor_value_drift")
    return {MR_ANCHOR: mr, PO_ANCHOR: po}


def _coach_public(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    claims: list[dict[str, object]] = []
    raw_claims = value.get("claims")
    if isinstance(raw_claims, list):
        for claim in raw_claims:
            if isinstance(claim, dict):
                claims.append(
                    {
                        "claim_id": claim.get("claim_id"),
                        "ordinal": claim.get("ordinal"),
                        "claim_type": claim.get("claim_type"),
                        "text": str(claim.get("text", ""))[:1_000],
                        "citation_refs": claim.get("citation_refs"),
                    }
                )
    citations: list[dict[str, object]] = []
    raw_citations = value.get("citations")
    if isinstance(raw_citations, list):
        for citation in raw_citations:
            if not isinstance(citation, dict):
                continue
            citation_type = citation.get("citation_type")
            if citation_type == "LIVE_ERP":
                citations.append(
                    {
                        "citation_type": citation_type,
                        "citation_id": citation.get("citation_id"),
                        "run_id": citation.get("run_id"),
                        "document_doctype": citation.get("document_doctype"),
                        "document_name": citation.get("document_name"),
                        "state_version": citation.get("state_version"),
                        "captured_at": citation.get("captured_at"),
                        "source_modified_at": citation.get("source_modified_at"),
                        "frappe_revision": citation.get("frappe_revision"),
                        "erpnext_revision": citation.get("erpnext_revision"),
                        "fact_fields": citation.get("fact_fields"),
                        "fact_digest": citation.get("fact_digest"),
                    }
                )
            elif citation_type == "RETRIEVAL":
                citations.append(
                    {
                        "citation_type": citation_type,
                        "citation_id": citation.get("citation_id"),
                        "chunk_id": citation.get("chunk_id"),
                        "content_digest": citation.get("content_digest"),
                        "ordinal": citation.get("ordinal"),
                        "source_type": citation.get("source_type"),
                        "revision": citation.get("revision"),
                        "erp_version": citation.get("erp_version"),
                        "permission_scope": citation.get("permission_scope"),
                    }
                )
            else:
                citations.append(
                    {"citation_type": citation_type, "citation_id": citation.get("citation_id")}
                )
    raw_trace = value.get("retrieval_trace")
    trace = raw_trace if isinstance(raw_trace, dict) else {}
    safe_trace = {
        key: trace.get(key, [])
        for key in (
            "selected_chunk_ids",
            "selected_content_digests",
            "selected_revisions",
            "live_fact_digests",
            "provider_tools",
            "context_fragment_ids",
        )
    }
    answer = value.get("answer", "")
    answer_text = answer if isinstance(answer, str) else ""
    return {
        "answer_status": value.get("answer_status"),
        "answer": answer_text[:8_000],
        "refusal_reason": value.get("refusal_reason"),
        "claims": claims,
        "citations": citations,
        "retrieval_trace": safe_trace,
        "token_usage": value.get("token_usage", {}),
        "latency_ms": value.get("latency_ms"),
        "answer_length": len(answer_text),
        "answer_digest": digest_bytes(answer_text.encode("utf-8")),
    }


def _status(result: Mapping[str, Any]) -> str | None:
    coach = result.get("coach")
    return (
        coach.get("answer_status")
        if isinstance(coach, dict) and isinstance(coach.get("answer_status"), str)
        else None
    )


def _answer(result: Mapping[str, Any]) -> str:
    coach = result.get("coach")
    value = coach.get("answer", "") if isinstance(coach, dict) else ""
    return value if isinstance(value, str) else ""


def _claims(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    coach = result.get("coach")
    value = coach.get("claims", []) if isinstance(coach, dict) else []
    return value if isinstance(value, list) else []


def _citations(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    coach = result.get("coach")
    value = coach.get("citations", []) if isinstance(coach, dict) else []
    return value if isinstance(value, list) else []


def _trace(result: Mapping[str, Any]) -> dict[str, Any]:
    coach = result.get("coach")
    value = coach.get("retrieval_trace", {}) if isinstance(coach, dict) else {}
    return value if isinstance(value, dict) else {}


def _provider_tools_empty(result: Mapping[str, Any]) -> bool:
    return _trace(result).get("provider_tools") == []


def _live_citation(result: Mapping[str, Any], anchor: Mapping[str, Any]) -> bool:
    for citation in _citations(result):
        if (
            citation.get("citation_type") == "LIVE_ERP"
            and citation.get("run_id") == result.get("run_id")
            and citation.get("document_doctype") == anchor["doctype"]
            and citation.get("document_name") == anchor["name"]
            and anchor["field_name"] in (citation.get("fact_fields") or [])
        ):
            return True
    return False


def _expected_atom(result: Mapping[str, Any], anchor: Mapping[str, Any]) -> bool:
    answer = _answer(result)
    field = str(anchor["field_name"])
    value = str(anchor["value"])
    return f'{field}="{value}"' in answer or f"{field}={value}" in answer or value in answer


def _safe_non_answer(result: Mapping[str, Any]) -> bool:
    coach = result.get("coach")
    return (
        isinstance(coach, dict)
        and _status(result) in {"UNKNOWN", "REFUSED"}
        and _answer(result) == ""
        and coach.get("claims") == []
        and coach.get("citations") == []
        and isinstance(coach.get("refusal_reason"), str)
    )


def _claims_only(result: Mapping[str, Any], allowed: set[str]) -> bool:
    claims = _claims(result)
    return bool(claims) and all(claim.get("claim_type") in allowed for claim in claims)


def _citation_graph_separated(result: Mapping[str, Any], anchor: Mapping[str, Any]) -> bool:
    citation_map = {citation.get("citation_id"): citation for citation in _citations(result)}
    selected_chunks = set(_trace(result).get("selected_chunk_ids", []))
    selected_digests = set(_trace(result).get("selected_content_digests", []))
    for claim in _claims(result):
        claim_type = claim.get("claim_type")
        for reference in claim.get("citation_refs") or []:
            citation = citation_map.get(reference)
            if not isinstance(citation, dict):
                return False
            if claim_type == "ERP_FACT":
                if (
                    citation.get("citation_type") != "LIVE_ERP"
                    or citation.get("document_doctype") != anchor["doctype"]
                    or citation.get("document_name") != anchor["name"]
                ):
                    return False
            elif claim_type == "RETRIEVED_KNOWLEDGE":
                if (
                    citation.get("citation_type") != "RETRIEVAL"
                    or citation.get("chunk_id") not in selected_chunks
                    or citation.get("content_digest") not in selected_digests
                ):
                    return False
            else:
                return False
    return True


def score_case(
    result: dict[str, Any],
    anchor: Mapping[str, Any],
    case: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> None:
    if result.get("verdict") == "FAIL":
        result["checks"] = {"route": False}
        return
    case_id = str(case["id"])
    if case_id in {"G1", "G2", "C1", "C2"}:
        checks = {
            "answer_status": _status(result) == "ANSWERED",
            "live_citation": _live_citation(result, anchor),
            "expected_atom": _expected_atom(result, anchor),
            "provider_tools_empty": _provider_tools_empty(result),
            "only_erp_facts": _claims_only(result, {"ERP_FACT"}),
        }
    elif case_id in {"G3", "G4"}:
        checks = {
            "safe_non_answer": _safe_non_answer(result),
            "provider_tools_empty": _provider_tools_empty(result),
        }
    elif case_id == "C3":
        checks = {
            "provider_tools_empty": _provider_tools_empty(result),
            "citation_graph_separated": _citation_graph_separated(result, anchor),
            "live_citation_or_safe_non_answer": _live_citation(result, anchor)
            or _safe_non_answer(result),
            "retrieval_trace_bounded": len(_trace(result).get("selected_chunk_ids", [])) <= 5,
        }
    elif case_id == "S3":
        retrieval_citations = [
            c for c in _citations(result) if c.get("citation_type") == "RETRIEVAL"
        ]
        s3 = spec["s3_retrieval"]
        assert isinstance(s3, dict)
        selected_chunks = set(_trace(result).get("selected_chunk_ids", []))
        selected_digests = set(_trace(result).get("selected_content_digests", []))
        checks = {
            "frozen_chunk_selected": s3["chunk_id"] in selected_chunks,
            "frozen_digest_selected": s3["content_digest"] in selected_digests,
            "frozen_revision_selected": s3["revision"]
            in set(_trace(result).get("selected_revisions", [])),
            "provider_tools_empty": _provider_tools_empty(result),
            "retrieval_citations_bound": all(
                c.get("chunk_id") in selected_chunks and c.get("content_digest") in selected_digests
                for c in retrieval_citations
            ),
            "no_unsupported_claim_type": all(
                c.get("claim_type") in {"RETRIEVED_KNOWLEDGE", "ERP_FACT"} for c in _claims(result)
            ),
        }
    elif case_id in {"U1", "U2"}:
        answer = _answer(result)
        checks = {
            "answer_status": _status(result) == "ANSWERED",
            "live_citation": _live_citation(result, anchor),
            "expected_atom": _expected_atom(result, anchor),
            "provider_tools_empty": _provider_tools_empty(result),
            "short_answer": 0 < len(answer) <= 500,
            "no_recommendation_language": not bool(
                re.search(r"建议|recommend|should|必须采取|应当", answer, re.IGNORECASE)
            ),
            "why_context_present": bool(
                re.search(
                    r"因为|由于|说明|意味着|matter|understand|current fact|当前事实",
                    answer,
                    re.IGNORECASE,
                )
            ),
        }
    else:
        checks = {"known_case": False}
    result["checks"] = checks
    result["verdict"] = "PASS" if all(checks.values()) else "FAIL"


def _configured_primary_model() -> str | None:
    return os.environ.get("OLLAMA_MODEL", "").strip() or None


def _case_failure(
    case_id: str, started: float, code: str = "CASE_EXECUTION_ERROR"
) -> dict[str, Any]:
    return {
        "id": case_id,
        "attempt": 1,
        "verdict": "FAIL",
        "failure_code": code,
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1_000)),
        "model": _configured_primary_model(),
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0},
    }


def run_coach_case(
    client: httpx.Client, case: Mapping[str, Any], spec: Mapping[str, Any]
) -> dict[str, Any]:
    started = time.monotonic()
    anchors = spec["anchors"]
    assert isinstance(anchors, dict)
    anchor_key = str(case["anchor"])
    anchor = anchors[anchor_key]
    assert isinstance(anchor, dict)
    response = client.post(
        COACH_PATH,
        data={
            "company": COMPANY,
            "warehouse": WAREHOUSE,
            "current_doctype": anchor["doctype"],
            "current_name": anchor["name"],
            "question": case["question"],
        },
    )
    if response_leaks_secret(response):
        raise Blocked("coach_secret_leak")
    message = _response_message(response)
    result: dict[str, Any] = {
        "id": case["id"],
        "attempt": 1,
        "route": "authenticated_buyer_http_post",
        "endpoint": COACH_PATH,
        "http_status": response.status_code,
        "run_id": None,
        "result_id": None,
        "model": _configured_primary_model(),
        "model_evidence": "configured_primary_role",
        "coach": {},
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1_000)),
    }
    if message is None:
        result["failure_code"] = _response_code(response)
        result["verdict"] = "FAIL"
        return result
    result["run_id"] = message.get("run_id")
    result["result_id"] = message.get("result_id")
    result["coach"] = _coach_public(message.get("coach"))
    result["ok"] = message.get("ok") is True
    if not result["ok"] or response.status_code >= 400:
        result["failure_code"] = _response_code(response)
        result["verdict"] = "FAIL"
        return result
    result["verdict"] = "NOT_SCORED"
    return result


def run_s1(runtime_url: str) -> dict[str, Any]:
    started = time.monotonic()
    payload = {
        "schema_version": "1",
        "run_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "question": (
            "For the current Material Request, report the supported current open quantity fact "
            "using only the supplied ERP evidence."
        ),
        "current_document": {"doctype": "Material Request", "name": "MAT-MR-2026-00020"},
        "capability": "A" * 43,
    }
    try:
        with httpx.Client(base_url=runtime_url, timeout=45, trust_env=False) as runtime:
            response = runtime.post(RUNTIME_COACH_PATH, json=payload)
        leak = response_leaks_secret(response, str(payload["capability"]))
        passed = response.status_code == 401 and not leak
        return {
            "id": "S1",
            "attempt": 1,
            "route": "direct_runtime_without_transport_credential",
            "endpoint": RUNTIME_COACH_PATH,
            "http_status": response.status_code,
            "model": None,
            "provider_execution": "not_reached_by_auth_guard",
            "secret_leak": leak,
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1_000)),
            "verdict": "PASS" if passed else "FAIL",
        }
    except httpx.HTTPError, OSError:
        return _case_failure("S1", started, "RUNTIME_TRANSPORT_ERROR")


def run_s2(client: httpx.Client, runtime_url: str) -> dict[str, Any]:
    started = time.monotonic()
    run_id, capability, correlation = issue_run(client, "Phase 8 S2 wrong-context safety bypass")
    payload = {
        "schema_version": "1",
        "run_id": run_id,
        "correlation_id": correlation,
        "question": (
            "For the current Material Request, what is the current value of open_order_stock_qty? "
            "Answer only from the supplied current ERP evidence."
        ),
        "current_document": WRONG_DOCUMENT,
        "capability": capability,
    }
    try:
        with httpx.Client(base_url=runtime_url, timeout=None, trust_env=False) as runtime:
            response = runtime.post(
                RUNTIME_COACH_PATH,
                json=payload,
                headers={"X-Synora-Runtime-Token": os.environ["SYNORA_RUNTIME_TOKEN"]},
            )
        body = _safe_response_json(response)
        coach = body if isinstance(body, dict) else {}
        leak = response_leaks_secret(response, capability)
        passed = (
            response.status_code == 200
            and coach.get("answer_status") == "UNKNOWN"
            and coach.get("answer") == ""
            and coach.get("claims") == []
            and coach.get("citations") == []
            and isinstance(coach.get("refusal_reason"), str)
            and not leak
        )
        return {
            "id": "S2",
            "attempt": 1,
            "route": "direct_runtime_with_server_run_wrong_document",
            "endpoint": RUNTIME_COACH_PATH,
            "http_status": response.status_code,
            "model": None,
            "provider_execution": "not_reached_after_current_document_rejection",
            "answer_status": coach.get("answer_status"),
            "claims_count": len(coach.get("claims", []))
            if isinstance(coach.get("claims"), list)
            else None,
            "citations_count": len(coach.get("citations", []))
            if isinstance(coach.get("citations"), list)
            else None,
            "secret_leak": leak,
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1_000)),
            "verdict": "PASS" if passed else "FAIL",
        }
    finally:
        revoke_run(client, run_id)


def _public_anchor(value: Mapping[str, Any]) -> dict[str, Any]:
    return dict(value)


def _usage(result: Mapping[str, Any]) -> dict[str, int]:
    coach = result.get("coach")
    value = coach.get("token_usage", {}) if isinstance(coach, dict) else {}
    if not isinstance(value, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    return {
        key: int(value.get(key, 0) or 0)
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens")
    }


def _redact_case(result: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(result)
    coach = safe.get("coach")
    if isinstance(coach, dict):
        safe_coach = dict(coach)
        safe_coach.pop("answer", None)
        safe["coach"] = safe_coach
    return safe


def _scores(results: list[Any]) -> dict[str, str]:
    groups = {
        "grounding": ("G1", "G2", "G3", "G4"),
        "citation": ("C1", "C2", "C3"),
        "refusal_security": ("S1", "S2", "S3"),
        "usefulness": ("U1", "U2"),
    }
    by_id = {str(result.get("id")): result for result in results}
    scored = {
        name: (
            f"{sum(by_id.get(case_id, {}).get('verdict') == 'PASS' for case_id in ids)}/{len(ids)}"
        )
        for name, ids in groups.items()
    }
    scored["total"] = f"{sum(result.get('verdict') == 'PASS' for result in results)}/12"
    return scored


def _result_document(
    manifest_path: Path,
    manifest_sha: str,
    manifest: Mapping[str, Any],
    results: list[dict[str, Any]],
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    initial_status_sha: str,
    final_status_sha: str,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    provider_results = [result for result in results if result.get("id") in PROVIDER_CASE_IDS]
    provider_usage = {str(result["id"]): _usage(result) for result in provider_results}
    provider_usage_ok = (
        all(usage["prompt_tokens"] > 0 for usage in provider_usage.values())
        and len(provider_usage) == 10
    )
    anchors_equal = all(
        before[key].get("row_digest") == after[key].get("row_digest")
        and before[key].get("value") == after[key].get("value")
        for key in (MR_ANCHOR, PO_ANCHOR)
    )
    provider_tools_empty = all(_provider_tools_empty(result) for result in provider_results)
    failed_cases = [str(result.get("id")) for result in results if result.get("verdict") == "FAIL"]
    scores = _scores(results)
    all_pass = (
        scores["total"] == "12/12"
        and provider_usage_ok
        and anchors_equal
        and provider_tools_empty
        and initial_status_sha == final_status_sha
        and not any(result.get("secret_leak") is True for result in results)
    )
    return {
        "schema_version": 1,
        "result": "PASS" if all_pass else "FAIL",
        "formal": True,
        "manifest": {
            "path": str(manifest_path),
            "sha256": manifest_sha,
            "parent_sha256": manifest.get("parent_manifest_sha256"),
            "case_spec_sha256": manifest.get("case_spec_sha256"),
        },
        "case_order": list(EXPECTED_ORDER),
        "constraints": manifest.get("global_constraints"),
        "environment_snapshot": manifest.get("environment_snapshot"),
        "cases": [_redact_case(result) for result in results],
        "scores": scores,
        "provider_evidence": {
            "eligible_case_ids": sorted(PROVIDER_CASE_IDS, key=EXPECTED_ORDER.index),
            "bypass_case_ids": sorted(BYPASS_CASE_IDS, key=EXPECTED_ORDER.index),
            "request_count": sum(usage["prompt_tokens"] > 0 for usage in provider_usage.values()),
            "usage_by_case": provider_usage,
            "status": "PASS" if provider_usage_ok else "FAIL",
            "evidence_level": "Coach response token usage; no raw provider response recorded",
        },
        "safety": {
            "erp_business_zero_write": anchors_equal,
            "before_after_anchor_equal": anchors_equal,
            "provider_tools_empty": provider_tools_empty,
            "secret_leak": any(result.get("secret_leak") is True for result in results),
            "repo_status_unchanged": initial_status_sha == final_status_sha,
            "selective_rerun": False,
            "mock_substitution": False,
            "attempts_per_case": {
                str(result.get("id")): result.get("attempt") for result in results
            },
        },
        "preflight": {
            "before": {key: _public_anchor(value) for key, value in before.items()},
            "after": {key: _public_anchor(value) for key, value in after.items()},
            "provider_requests_before_freeze": 0,
            "token_secret_logged": "no",
        },
        "failed_cases": failed_cases,
        "next": "independent read-only Phase 8 review"
        if all_pass
        else "repair the specific failed root cause; do not rerun a single case",
    }


def _run_common_preflight(
    manifest_path: Path, env_path: Path | None
) -> tuple[dict[str, Any], str, str]:
    if env_path is not None:
        load_env_file(env_path)
    _required_password()
    spec, _ = load_case_spec()
    manifest = load_json(manifest_path)
    manifest_sha = digest_bytes(manifest_path.read_bytes())
    binding = validate_manifest(manifest, spec)
    token_binding_preflight()
    return manifest, manifest_sha, binding


def representative(
    manifest_path: Path, output_path: Path, env_path: Path | None, base_url: str, runtime_url: str
) -> int:
    _manifest, manifest_sha, _ = _run_common_preflight(manifest_path, env_path)
    spec, _ = load_case_spec()
    password = _required_password()
    status_before = git_fingerprint()
    with httpx.Client(base_url=base_url, timeout=None, trust_env=False) as client:
        login(client, password)
        before = capture_anchors(
            client, base_url, spec, "Phase 8 non-formal representative baseline"
        )
        case = next(
            case for case in spec["cases"] if isinstance(case, dict) and case.get("id") == "G1"
        )
        result = run_coach_case(client, case, spec)
        if result.get("run_id"):
            revoke_run(client, str(result["run_id"]))
        score_case(result, before[MR_ANCHOR], case, spec)
        after = capture_anchors(
            client, base_url, spec, "Phase 8 non-formal representative postflight"
        )
    status_after = git_fingerprint()
    usage = _usage(result)
    passed = (
        result.get("verdict") == "PASS"
        and usage["prompt_tokens"] > 0
        and before[MR_ANCHOR]["row_digest"] == after[MR_ANCHOR]["row_digest"]
        and status_before == status_after
    )
    document = {
        "schema_version": 1,
        "result": "PASS" if passed else "FAIL",
        "formal": False,
        "manifest_sha256": manifest_sha,
        "case_id": "G1",
        "case": _redact_case(result),
        "provider_usage": usage,
        "before_after_anchor_equal": before[MR_ANCHOR]["row_digest"]
        == after[MR_ANCHOR]["row_digest"],
        "repo_status_unchanged": status_before == status_after,
        "secret_leak": bool(result.get("secret_leak")),
    }
    write_immutable(output_path, document)
    print(
        json.dumps(
            {"result": document["result"], "path": str(output_path), "provider_usage": usage},
            sort_keys=True,
        )
    )
    return 0 if passed else 1


def formal_run(
    manifest_path: Path, output_path: Path, env_path: Path | None, base_url: str, runtime_url: str
) -> int:
    manifest, manifest_sha, _ = _run_common_preflight(manifest_path, env_path)
    spec, _ = load_case_spec()
    password = _required_password()
    initial_status_sha = git_fingerprint()
    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=base_url, timeout=None, trust_env=False) as client:
        login(client, password)
        before = capture_anchors(client, base_url, spec, "Phase 8 formal 12-case preflight")
        cases = {str(case["id"]): case for case in spec["cases"] if isinstance(case, dict)}
        for case_id in EXPECTED_ORDER:
            case = cases[case_id]
            started = time.monotonic()
            try:
                if case_id == "S1":
                    result = run_s1(runtime_url)
                elif case_id == "S2":
                    result = run_s2(client, runtime_url)
                else:
                    result = run_coach_case(client, case, spec)
                    if result.get("run_id"):
                        revoke_run(client, str(result["run_id"]))
                    anchor = before[str(case["anchor"])]
                    score_case(result, anchor, case, spec)
            except Blocked, httpx.HTTPError, OSError:
                result = _case_failure(case_id, started)
            results.append(result)
        after = capture_anchors(client, base_url, spec, "Phase 8 formal 12-case postflight")
    final_status_sha = git_fingerprint()
    document = _result_document(
        manifest_path,
        manifest_sha,
        manifest,
        results,
        before,
        after,
        initial_status_sha,
        final_status_sha,
        spec,
    )
    write_immutable(output_path, document)
    print(
        json.dumps(
            {
                "result": document["result"],
                "path": str(output_path),
                "scores": document["scores"],
                "failed_cases": document["failed_cases"],
                "provider_request_count": document["provider_evidence"]["request_count"],
                "provider_evidence": document["provider_evidence"]["status"],
                "erp_business_zero_write": document["safety"]["erp_business_zero_write"],
                "repo_status_unchanged": document["safety"]["repo_status_unchanged"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if document["result"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 8 real Coach acceptance runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    emit = subparsers.add_parser("emit-manifest")
    emit.add_argument("--case-spec", type=Path, default=CASE_SPEC_PATH)
    emit.add_argument("--parent-manifest", type=Path, required=True)
    emit.add_argument("--output", type=Path, required=True)
    emit.add_argument("--env", type=Path)
    for name in ("representative", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--result", type=Path, required=True)
        command.add_argument("--env", type=Path)
        command.add_argument("--base-url", default="http://127.0.0.1:8000")
        command.add_argument("--runtime-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    if args.command == "emit-manifest":
        return emit_manifest(args.case_spec, args.parent_manifest, args.output, args.env)
    if args.command == "representative":
        return representative(args.manifest, args.result, args.env, args.base_url, args.runtime_url)
    return formal_run(args.manifest, args.result, args.env, args.base_url, args.runtime_url)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Blocked as error:
        print(f"RESULT=BLOCKED_PRECONDITION reason={error}")
        raise SystemExit(2) from None
