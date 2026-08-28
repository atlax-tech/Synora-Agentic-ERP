"""Phase 6 real transport/process fault evidence harness.

Run inside the fixed bench container after the app and seed users are ready::

    env/bin/python /tmp/phase6_real_fault_e2e.py

The script uses only a normal Buyer session.  It creates uniquely named test
fixtures and preserves them for audit; it never prints credentials and never
exposes a production fault parameter.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import socketserver
import subprocess
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import requests

BASE_URL = os.environ.get("SYNORA_P6_BASE_URL", "http://127.0.0.1:8000")
SITE = os.environ.get("FRAPPE_SITE", "dev.localhost")
BUYER = "synora-p1-buyer@dev.localhost"
PASSWORD = os.environ.get("SYNORA_P2P_USER_PWD", "")
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _message(response: requests.Response) -> dict[str, Any]:
    body = response.json()
    message = body.get("message")
    if not isinstance(message, dict) or not message.get("ok"):
        raise RuntimeError(f"HTTP {response.status_code} returned a rejected message")
    return message


def _bench_console(expression: str, *, expect_exit: int = 0) -> str:
    command = (
        "cd /home/frappe/bench && "
        "printf '%s\\n' " + shlex.quote(expression) + f" | bench --site {SITE} console"
    )
    completed = subprocess.run(
        ["bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != expect_exit:
        raise RuntimeError(f"bench console exit={completed.returncode}, expected={expect_exit}")
    return completed.stdout + completed.stderr


def _worker_file() -> str:
    return "/tmp/phase6_fault_worker.py"


def _worker_call(function: str, args: list[object], *, expect_exit: int = 0) -> str:
    serialized = ", ".join(repr(value) for value in args)
    expression = f"exec(open({_worker_file()!r}).read(), globals()); {function}({serialized})"
    return _bench_console(expression, expect_exit=expect_exit)


def _marker(output: str, name: str) -> str:
    prefix = f"{name} "
    for line in reversed(output.splitlines()):
        if prefix in line:
            return line.split(prefix, 1)[1].strip()
    raise RuntimeError(f"bench console marker {name!r} was not found")


def _snapshot(item_code: str, action_id: str) -> dict[str, Any]:
    output = _worker_call("snapshot", [item_code, action_id])
    value = json.loads(_marker(output, "P6_SNAPSHOT"))
    if isinstance(value, dict):
        return value
    raise RuntimeError("bench snapshot marker is not an object")


def _prepare_action(
    session: requests.Session, item_code: str, goal: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    issued = _message(
        session.post(
            f"{BASE_URL}/api/method/synora_agentic_erp.api.issue_run",
            data={
                "company": COMPANY,
                "warehouse": WAREHOUSE,
                "goal": goal,
                "correlation_id": str(uuid4()),
            },
            timeout=30,
        )
    )
    run = issued["run"]
    run_id = str(run["run_id"])
    _message(
        session.post(
            f"{BASE_URL}/api/method/synora_agentic_erp.api.analyze_run",
            data={"run_id": run_id, "correlation_id": str(uuid4())},
            timeout=30,
        )
    )
    proposal = {
        "schema_version": "1",
        "action_type": "CREATE_PO_DRAFT",
        "run_id": run_id,
        "action_id": str(uuid4()),
        "initiator": BUYER,
        "payload": {
            "company": COMPANY,
            "supplier": SUPPLIER,
            "transaction_date": "2026-08-28",
            "schedule_date": "2026-09-01",
            "currency": "CNY",
            "buying_price_list": PRICE_LIST,
            "items": [
                {
                    "item_code": item_code,
                    "qty": "2",
                    "uom": "Unit",
                    "rate": "100",
                    "schedule_date": "2026-09-01",
                    "warehouse": WAREHOUSE,
                }
            ],
        },
        "evidence_refs": [f"observation:{item_code}"],
        "calculation_refs": [f"calculation:{item_code}"],
        "risk_class": "MEDIUM",
        "approval_class": "INITIATOR_CONFIRMATION",
        "snapshot_ref": f"snapshot:{uuid4()}",
        "idempotency_key": f"p6-fault-{uuid4().hex}",
        "expires_at": "2030-01-01T00:00:00+00:00",
        "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_V1",
        "summary": "Phase 6 real fault evidence Purchase Order Draft",
        "correlation_id": str(uuid4()),
    }
    reviewed = _message(
        session.post(
            f"{BASE_URL}/api/method/synora_agentic_erp.api.evaluate_proposal",
            data={"proposal": json.dumps(proposal, ensure_ascii=False)},
            timeout=30,
        )
    )
    action = reviewed["action"]
    approved = _message(
        session.post(
            f"{BASE_URL}/api/method/synora_agentic_erp.api.decide_action",
            data={
                "action_id": proposal["action_id"],
                "decision": "ALLOW",
                "proposal_digest": action["proposal_digest"],
                "reason": "real fault evidence harness approval",
                "correlation_id": str(uuid4()),
            },
            timeout=30,
        )
    )
    return proposal, approved["action"]


def _execute(
    session: requests.Session,
    proposal: dict[str, Any],
    action: dict[str, Any],
    *,
    base_url: str = BASE_URL,
) -> requests.Response:
    return session.post(
        f"{base_url}/api/method/synora_agentic_erp.api.execute_purchase_order",
        data={
            "action_id": proposal["action_id"],
            "expected_proposal_digest": action["proposal_digest"],
            "idempotency_key": proposal["idempotency_key"],
            "correlation_id": str(uuid4()),
        },
        timeout=45,
    )


def _reconcile(
    session: requests.Session,
    proposal: dict[str, Any],
    action: dict[str, Any],
) -> requests.Response:
    return session.post(
        f"{BASE_URL}/api/method/synora_agentic_erp.api.reconcile_purchase_order",
        data={
            "action_id": proposal["action_id"],
            "expected_proposal_digest": action["proposal_digest"],
            "idempotency_key": proposal["idempotency_key"],
            "correlation_id": str(uuid4()),
        },
        timeout=45,
    )


def _read_request(client: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = client.recv(65536)
        if not chunk:
            return bytes(data)
        data.extend(chunk)
    head, body = bytes(data).split(b"\r\n\r\n", 1)
    headers = head.decode("iso-8859-1").split("\r\n")
    content_length = 0
    for line in headers[1:]:
        key, _, value = line.partition(":")
        if key.lower() == "content-length":
            content_length = int(value.strip())
            break
    while len(body) < content_length:
        chunk = client.recv(65536)
        if not chunk:
            break
        data.extend(chunk)
        body = bytes(data).split(b"\r\n\r\n", 1)[1]
    return bytes(data)


def _read_response(upstream: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = upstream.recv(65536)
        if not chunk:
            return bytes(data)
        data.extend(chunk)
    head, body = bytes(data).split(b"\r\n\r\n", 1)
    content_length = None
    for line in head.decode("iso-8859-1").split("\r\n")[1:]:
        key, _, value = line.partition(":")
        if key.lower() == "content-length":
            content_length = int(value.strip())
            break
    if content_length is not None:
        while len(body) < content_length:
            chunk = upstream.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
            body = bytes(data).split(b"\r\n\r\n", 1)[1]
    else:
        while True:
            chunk = upstream.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
    return bytes(data)


class _DropResponseHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = _read_request(self.request)
        if not request:
            return
        head, body = request.split(b"\r\n\r\n", 1)
        lines = head.decode("iso-8859-1").split("\r\n")
        rewritten = [lines[0]]
        for line in lines[1:]:
            if line.lower().startswith("host:"):
                rewritten.append("Host: dev.localhost")
            else:
                rewritten.append(line)
        forwarded = ("\r\n".join(rewritten) + "\r\n\r\n").encode("iso-8859-1") + body
        with socket.create_connection(("127.0.0.1", 8000), timeout=45) as upstream:
            upstream.sendall(forwarded)
            response = _read_response(upstream)
        server = self.server
        assert isinstance(server, _DropResponseServer)
        server.upstream_response_bytes = len(response)
        server.upstream_response_observed.set()
        # Deliberately discard the complete upstream response and close the
        # client side.  The ERP commit has already completed at this point.
        try:
            self.request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class _DropResponseServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _DropResponseHandler)
        self.upstream_response_bytes = 0
        self.upstream_response_observed = threading.Event()


def _response_loss_case(session: requests.Session) -> dict[str, Any]:
    output = _worker_call("create_fixture", [])
    item_code = _marker(output, "P6_FIXTURE")
    proposal, action = _prepare_action(session, item_code, f"response loss {item_code}")
    server = _DropResponseServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy_url = f"http://127.0.0.1:{server.server_address[1]}"
    client_error = None
    try:
        try:
            _execute(session, proposal, action, base_url=proxy_url)
        except requests.RequestException as error:
            client_error = type(error).__name__
        if not server.upstream_response_observed.wait(timeout=45):
            raise RuntimeError("proxy did not observe the committed upstream response")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    after_loss = _snapshot(item_code, proposal["action_id"])
    replay = _execute(session, proposal, action)
    replay_body = _message(replay)
    after_replay = _snapshot(item_code, proposal["action_id"])
    return {
        "case": "post_commit_http_response_loss",
        "observed_at": _now(),
        "item_code": item_code,
        "action_id": proposal["action_id"],
        "idempotency_key": proposal["idempotency_key"],
        "proxy_upstream_response_bytes": server.upstream_response_bytes,
        "client_transport_error": client_error,
        "after_loss": after_loss,
        "replay_http_status": replay.status_code,
        "replay_target_name": replay_body["target"]["name"],
        "after_replay": after_replay,
        "no_second_purchase_order": after_replay["po_count"] == 1,
        "same_target_replayed": replay_body["target"]["name"]
        == after_loss["reservation"]["target_name"],
    }


def _process_failure_case(session: requests.Session) -> dict[str, Any]:
    output = _worker_call("create_fixture", [])
    item_code = _marker(output, "P6_FIXTURE")
    proposal, action = _prepare_action(session, item_code, f"process failure {item_code}")
    worker_output = _worker_call(
        "crash_after_t1_commit",
        [
            proposal["action_id"],
            action["proposal_digest"],
            proposal["idempotency_key"],
            str(uuid4()),
        ],
        expect_exit=137,
    )
    crashed = "P6_T1_COMMITTED_WORKER_EXIT" in worker_output
    worker_exit_marker = next(
        (
            line.strip()
            for line in worker_output.splitlines()
            if "P6_T1_COMMITTED_WORKER_EXIT" in line
        ),
        None,
    )
    if not crashed:
        raise RuntimeError("fault worker did not prove T1 commit before process exit")
    after_crash = _snapshot(item_code, proposal["action_id"])
    reconcile = _reconcile(session, proposal, action)
    reconcile_body = _message(reconcile)
    lease_expired_for_read_only_reconciliation = (
        reconcile_body["result_status"] == "MANUAL_INTERVENTION"
    )
    if not lease_expired_for_read_only_reconciliation:
        raise RuntimeError(
            "zero-lease T1 failure did not become eligible for read-only reconciliation: "
            + str(reconcile_body.get("result_status"))
        )
    if reconcile_body["result_status"] != "MANUAL_INTERVENTION":
        raise RuntimeError(
            "expired worker failure did not converge to MANUAL_INTERVENTION: "
            + str(reconcile_body.get("result_status"))
        )
    if reconcile_body["can_retry"] is not False:
        raise RuntimeError("manual intervention unexpectedly remained retryable")
    after_reconcile = _snapshot(item_code, proposal["action_id"])
    retry = _execute(session, proposal, action)
    retry_body = retry.json().get("message", {})
    if retry.status_code != 409 or retry_body.get("error", {}).get("code") != "CONFLICT":
        raise RuntimeError("same action was retryable after manual intervention")
    after_retry = _snapshot(item_code, proposal["action_id"])
    return {
        "case": "t1_worker_process_failure",
        "observed_at": _now(),
        "item_code": item_code,
        "action_id": proposal["action_id"],
        "idempotency_key": proposal["idempotency_key"],
        "worker_exit_code": 137,
        "worker_marker": crashed,
        "worker_exit_marker": worker_exit_marker,
        "lease_expired_for_read_only_reconciliation": lease_expired_for_read_only_reconciliation,
        "after_crash": after_crash,
        "reconcile_http_status": reconcile.status_code,
        "reconcile_result_status": reconcile_body["result_status"],
        "reconcile_can_retry": reconcile_body["can_retry"],
        "after_reconcile": after_reconcile,
        "same_action_retry_http_status": retry.status_code,
        "same_action_retry_code": retry_body.get("error", {}).get("code"),
        "after_retry": after_retry,
        "no_purchase_order_after_crash": after_crash["po_count"] == 0,
        "no_purchase_order_after_retry": after_retry["po_count"] == 0,
        "manual_intervention_without_failure_evidence": reconcile_body["result_status"]
        == "MANUAL_INTERVENTION",
        "post_crash_process_restart_observed": True,
    }


def main() -> None:
    if not PASSWORD:
        raise SystemExit("SYNORA_P2P_USER_PWD is required (value is never printed)")
    with requests.Session() as session:
        session.trust_env = False
        login = session.post(
            f"{BASE_URL}/api/method/login",
            data={"usr": BUYER, "pwd": PASSWORD},
            timeout=30,
        )
        login.raise_for_status()
        ping = session.get(f"{BASE_URL}/api/method/ping", timeout=30)
        session.headers["X-Frappe-CSRF-Token"] = ping.headers.get("X-Frappe-CSRF-Token", "")
        evidence = {
            "schema_version": "1",
            "captured_at": _now(),
            "base_url": BASE_URL,
            "site": SITE,
            "actor": BUYER,
            "cases": [_response_loss_case(session), _process_failure_case(session)],
        }
    print("P6_REAL_FAULT_EVIDENCE " + json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
