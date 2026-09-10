"""Run the real Phase 10 P2P process-fault matrix against the dev site.

The host process owns orchestration and artifact writing.  ERP fixture creation
and proposal approval run inside a short-lived Frappe console process; each
fault and each read-back then runs in another independent console process.
The fault worker is copied into the container only for the duration of this
test and is never exposed through an HTTP endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO / "env" / "dev" / "docker-compose.yml"
SITE = "dev.localhost"
CONTAINER_DIR = "/tmp/synora_p10"
MATRIX_REMOTE = f"{CONTAINER_DIR}/phase10_fault_matrix.py"
WORKER_REMOTE = f"{CONTAINER_DIR}/phase10_fault_worker.py"

BUYER = "synora-p1-buyer@dev.localhost"
RECEIVER = "synora-p1-receiver@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
PAYMENT_OPERATOR = "synora-p1-payment-operator@dev.localhost"
PAYMENT_APPROVER = "synora-p1-payment-approver@dev.localhost"
PO_APPROVER = "synora-p1-approver@dev.localhost"
ADMINISTRATOR = "Administrator"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"
PAID_FROM = "Cash - SP1"
PAID_TO = "Creditors - SP1"

ACTION_SPECS = (
    ("SUBMIT_PO", "Purchase Order", BUYER, PO_APPROVER),
    ("CREATE_PR_DRAFT", "Purchase Order", BUYER, RECEIVER),
    ("SUBMIT_PR", "Purchase Receipt", BUYER, RECEIVER),
    ("CREATE_PI_DRAFT", "Purchase Receipt", BUYER, ACCOUNTANT),
    ("SUBMIT_PI", "Purchase Invoice", BUYER, ACCOUNTANT),
    ("CREATE_PAYMENT_ENTRY_DRAFT", "Purchase Invoice", BUYER, ACCOUNTANT),
    ("SUBMIT_PAYMENT_ENTRY", "Payment Entry", PAYMENT_OPERATOR, PAYMENT_APPROVER),
)
BOUNDARIES = (
    "reservation_committed",
    "before_erp_call",
    "after_erp_call",
    "before_receipt",
)


def _container_id() -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "-q", "bench"],
        check=True,
        capture_output=True,
        text=True,
    )
    container_id = result.stdout.strip()
    if not container_id:
        raise RuntimeError("bench container is not running")
    return container_id


def _copy_scripts() -> None:
    container_id = _container_id()
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "bench",
            "mkdir",
            "-p",
            CONTAINER_DIR,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for local, remote in (
        (REPO / "env" / "dev" / "p10" / "phase10_fault_matrix.py", MATRIX_REMOTE),
        (REPO / "env" / "dev" / "p10" / "phase10_fault_worker.py", WORKER_REMOTE),
    ):
        subprocess.run(
            ["docker", "cp", str(local), f"{container_id}:{remote}"],
            check=True,
            capture_output=True,
            text=True,
        )


def _bench_console(function: str, *args: object) -> tuple[int, str, str]:
    expression = (
        f"globals()['_P10_MATRIX_LIBRARY_EXECUTION'] = True; "
        f"exec(open({MATRIX_REMOTE!r}).read(), globals()); "
        f"{function}({', '.join(repr(value) for value in args)})"
    )
    command = (
        "cd /home/frappe/bench && "
        f"printf '%s\\n' {shlex.quote(expression)} | "
        f"bench --site {shlex.quote(SITE)} console"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "bench",
            "bash",
            "-lc",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _marker(output: str, prefix: str) -> dict[str, Any]:
    values = [line.split(prefix, 1)[1] for line in output.splitlines() if prefix in line]
    if not values:
        raise RuntimeError(f"bench console did not emit {prefix}; output tail={output[-4_000:]!r}")
    return json.loads(values[-1])


def _assert_ok(response: dict[str, Any], label: str) -> dict[str, Any]:
    if not response.get("ok"):
        error = response.get("error")
        raise RuntimeError(f"{label} failed: {error!r}")
    return response


def _make_item(item_code: str) -> None:
    import frappe

    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": ITEM_GROUP,
            "stock_uom": STOCK_UOM,
            "is_stock_item": 1,
        }
    ).insert(ignore_permissions=True)
    frappe.get_doc(
        {
            "doctype": "Item Price",
            "item_code": item_code,
            "price_list": PRICE_LIST,
            "price_list_rate": 10,
            "currency": "CNY",
            "uom": STOCK_UOM,
            "supplier": SUPPLIER,
            "buying": 1,
            "selling": 0,
            "valid_from": "2026-01-01",
        }
    ).insert(ignore_permissions=True)


def _make_po(item_code: str, *, submitted: bool) -> tuple[str, str]:
    import frappe

    po = frappe.get_doc(
        {
            "doctype": "Purchase Order",
            "supplier": SUPPLIER,
            "company": COMPANY,
            "transaction_date": "2026-09-11",
            "schedule_date": "2026-09-20",
            "currency": "CNY",
            "buying_price_list": PRICE_LIST,
            "items": [
                {
                    "item_code": item_code,
                    "qty": 2,
                    "uom": STOCK_UOM,
                    "conversion_factor": 1,
                    "rate": 10,
                    "warehouse": WAREHOUSE,
                    "schedule_date": "2026-09-20",
                }
            ],
        }
    )
    po.set_missing_values()
    po.insert(ignore_permissions=True)
    if submitted:
        po.submit()
    return str(po.name), str(po.items[0].name)


def _make_pr(po_name: str, *, submitted: bool) -> tuple[str, str]:
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    pr = make_purchase_receipt(po_name)
    pr.items[0].qty = 2
    pr.insert(ignore_permissions=True)
    if submitted:
        pr.submit()
    return str(pr.name), str(pr.items[0].name)


def _make_pi(pr_name: str, *, submitted: bool) -> tuple[str, str]:
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

    pi = make_purchase_invoice(pr_name)
    pi.items[0].qty = 2
    pi.items[0].rate = 10
    pi.insert(ignore_permissions=True)
    if submitted:
        pi.submit()
    return str(pi.name), str(pi.items[0].name)


def _make_payment_entry(pi_name: str) -> str:
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    payment = get_payment_entry(
        "Purchase Invoice",
        pi_name,
        party_amount=10,
        bank_account=PAID_FROM,
        party_type="Supplier",
        payment_type="Pay",
        reference_date="2026-09-11",
    )
    payment.paid_from = PAID_FROM
    payment.paid_to = PAID_TO
    payment.posting_date = "2026-09-11"
    payment.paid_amount = 10
    payment.received_amount = 10
    payment.set_missing_ref_details(force=True)
    payment.set_amounts()
    payment.paid_amount = 10
    payment.received_amount = 10
    payment.insert(ignore_permissions=True)
    return str(payment.name)


def _fixture(action_type: str) -> dict[str, str]:
    import frappe

    frappe.set_user(ADMINISTRATOR)
    item_code = f"SYNORA-R104-FAULT-{uuid4().hex[:16]}"
    _make_item(item_code)
    po_name, po_row = _make_po(item_code, submitted=action_type != "SUBMIT_PO")
    result: dict[str, str] = {
        "item_code": item_code,
        "po_name": po_name,
        "po_row": po_row,
    }
    if action_type == "SUBMIT_PO":
        frappe.db.commit()
        return {**result, "source_doctype": "Purchase Order", "source_name": po_name}
    if action_type == "CREATE_PR_DRAFT":
        frappe.db.commit()
        return {**result, "source_doctype": "Purchase Order", "source_name": po_name}

    pr_name, pr_row = _make_pr(po_name, submitted=action_type not in {"SUBMIT_PR"})
    result.update({"pr_name": pr_name, "pr_row": pr_row})
    if action_type == "SUBMIT_PR":
        frappe.db.commit()
        return {**result, "source_doctype": "Purchase Receipt", "source_name": pr_name}
    if action_type == "CREATE_PI_DRAFT":
        frappe.db.commit()
        return {**result, "source_doctype": "Purchase Receipt", "source_name": pr_name}

    pi_name, pi_row = _make_pi(pr_name, submitted=action_type not in {"SUBMIT_PI"})
    result.update({"pi_name": pi_name, "pi_row": pi_row})
    if action_type == "SUBMIT_PI":
        frappe.db.commit()
        return {**result, "source_doctype": "Purchase Invoice", "source_name": pi_name}

    if action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
        frappe.db.commit()
        return {**result, "source_doctype": "Purchase Invoice", "source_name": pi_name}

    payment_name = _make_payment_entry(pi_name)
    result["payment_name"] = payment_name
    frappe.db.commit()
    return {**result, "source_doctype": "Payment Entry", "source_name": payment_name}


def _proposal(
    action_type: str,
    source_doctype: str,
    source_name: str,
    fixture: dict[str, str],
    initiator: str,
) -> dict[str, Any]:
    from synora_agentic_erp.api import confirm_p2p_goal, decide_action, evaluate_proposal, issue_run

    frappe_set_user = __import__("frappe").set_user
    frappe_set_user(initiator)
    issued = _assert_ok(
        issue_run(
            COMPANY,
            f"Phase 10 fault matrix {action_type} {source_name}",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
            execution_mode="PLAN_EXECUTE",
            purpose="P2P_EXECUTION",
        ),
        f"issue {action_type}",
    )
    run_id = str(issued["run"]["run_id"])
    goal = {
        "source_doctype": "Purchase Order",
        "source_name": fixture["po_name"],
        "source_rows": [
            {
                "source_row": fixture["po_row"],
                "item_code": fixture["item_code"],
                "target_qty": "1",
            }
        ],
    }
    _assert_ok(confirm_p2p_goal(run_id, goal, str(uuid4())), f"confirm {action_type}")
    payload: dict[str, Any] = {
        "company": COMPANY,
        "source_doctype": source_doctype,
        "source_name": source_name,
    }
    if action_type == "CREATE_PR_DRAFT":
        payload.update(
            {
                "transaction_date": "2026-09-11",
                "items": [
                    {
                        "source_row": fixture["po_row"],
                        "item_code": fixture["item_code"],
                        "qty": "1",
                        "uom": STOCK_UOM,
                        "warehouse": WAREHOUSE,
                    }
                ],
            }
        )
    elif action_type == "CREATE_PI_DRAFT":
        payload.update(
            {
                "transaction_date": "2026-09-11",
                "items": [
                    {
                        "source_row": fixture["pr_row"],
                        "item_code": fixture["item_code"],
                        "qty": "1",
                        "uom": STOCK_UOM,
                        "warehouse": WAREHOUSE,
                        "rate": "10",
                    }
                ],
            }
        )
    elif action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
        payload.update(
            {
                "party_type": "Supplier",
                "party": SUPPLIER,
                "payment_type": "Pay",
                "posting_date": "2026-09-11",
                "paid_from": PAID_FROM,
                "paid_to": PAID_TO,
                "paid_amount": "10",
                "received_amount": "10",
                "source_currency": "CNY",
                "target_currency": "CNY",
                "references": [
                    {
                        "reference_doctype": "Purchase Invoice",
                        "reference_name": source_name,
                        "allocated_amount": "10",
                    }
                ],
            }
        )
    proposal = {
        "schema_version": "2",
        "action_type": action_type,
        "run_id": run_id,
        "action_id": str(uuid4()),
        "initiator": initiator,
        "payload": payload,
        "evidence_refs": [f"erp:{source_doctype}:{source_name}"],
        "calculation_refs": [f"phase10-fault:{action_type}"],
        "risk_class": "HIGH",
        "approval_class": "INDEPENDENT_APPROVER",
        "snapshot_ref": f"snapshot:{uuid4()}",
        "idempotency_key": f"p10-fault-{action_type.lower()}-{uuid4().hex}",
        "expires_at": "2030-01-01T00:00:00Z",
        "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_P2P_V1",
        "summary": f"Phase 10 fault matrix {action_type}",
        "correlation_id": str(uuid4()),
    }
    frappe_set_user(initiator)
    reviewed = _assert_ok(evaluate_proposal(proposal), f"evaluate {action_type}")
    if reviewed["action"]["state"] != "AWAITING_APPROVAL":
        raise RuntimeError(
            f"evaluate {action_type} did not authorize approval: "
            f"state={reviewed['action']['state']!r}; policy={reviewed.get('policy')!r}"
        )
    approver = next(spec[3] for spec in ACTION_SPECS if spec[0] == action_type)
    frappe_set_user(approver)
    approved = _assert_ok(
        decide_action(
            proposal["action_id"],
            "ALLOW",
            reviewed["action"]["proposal_digest"],
            f"Phase 10 fault matrix independent approval for {action_type}",
            str(uuid4()),
        ),
        f"approve {action_type}",
    )
    frappe_set_user("Administrator")
    return {
        "action_id": proposal["action_id"],
        "action_type": action_type,
        "run_id": run_id,
        "initiator": initiator,
        "approver": approver,
        "proposal_digest": reviewed["action"]["proposal_digest"],
        "idempotency_key": proposal["idempotency_key"],
        "correlation_id": proposal["correlation_id"],
        "source_doctype": source_doctype,
        "source_name": source_name,
        "approval_id": approved["approval"]["decision_id"],
        "approval_decision": approved["approval"]["decision"],
        "workflow_deadline_used": True,
    }


def collect_fixtures() -> None:
    import frappe

    records: list[dict[str, Any]] = []
    for action_type, _target_doctype, initiator, _approver in ACTION_SPECS:
        for boundary in BOUNDARIES:
            fixture = _fixture(action_type)
            proposal = _proposal(
                action_type,
                fixture["source_doctype"],
                fixture["source_name"],
                fixture,
                initiator,
            )
            records.append({"boundary": boundary, "fixture": fixture, "proposal": proposal})
    frappe.db.commit()
    print(
        "P10_FIXTURES "
        + json.dumps(
            {"schema_version": "1", "records": records},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def create_browser_fixture() -> None:
    import frappe

    frappe.set_user(ADMINISTRATOR)
    item_code = f"SYNORA-R104-BROWSER-{uuid4().hex[:16]}"
    _make_item(item_code)
    po_name, po_row = _make_po(item_code, submitted=True)
    frappe.db.commit()
    print(
        "P10_BROWSER_FIXTURE "
        + json.dumps(
            {
                "item_code": item_code,
                "po_name": po_name,
                "po_row": po_row,
                "qty": "2",
                "warehouse": WAREHOUSE,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def invoke_worker(function: str, *args: object) -> None:
    """Load the isolated worker and invoke one function in this console."""

    namespace: dict[str, Any] = {}
    exec(open(WORKER_REMOTE).read(), namespace)
    namespace[function](*args)


def _compact_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    reservation = value.get("reservation") or {}
    receipt = value.get("receipt") or {}
    return {
        "action_id": value.get("action_id"),
        "action_type": value.get("action_type"),
        "action_state": value.get("action_state"),
        "action_state_version": value.get("action_state_version"),
        "run_state": value.get("run_state"),
        "reservation": {
            key: reservation.get(key)
            for key in (
                "reservation_id",
                "status",
                "attempt",
                "target_name",
                "receipt",
                "response_category",
                "failure_category",
            )
        }
        if reservation
        else None,
        "receipt": {
            key: receipt.get(key)
            for key in (
                "receipt_id",
                "final_state",
                "response_category",
                "failure_category",
                "target_doctype",
                "target_name",
            )
        }
        if receipt
        else None,
        "target": value.get("target"),
    }


def _compact_reconcile(value: dict[str, Any]) -> dict[str, Any]:
    reservation = value.get("reservation") or {}
    receipt = value.get("receipt") or {}
    action = value.get("action") or {}
    run = value.get("run") or {}
    return {
        "ok": value.get("ok"),
        "result_status": value.get("result_status"),
        "can_retry": value.get("can_retry"),
        "run": {key: run.get(key) for key in ("run_id", "run_state", "state_version")},
        "action": {key: action.get(key) for key in ("action_id", "action_type", "state")},
        "reservation": {
            key: reservation.get(key)
            for key in ("reservation_id", "status", "target_name", "receipt", "failure_category")
        },
        "receipt": {
            key: receipt.get(key)
            for key in ("receipt_id", "final_state", "target_doctype", "target_name")
        }
        if receipt
        else None,
        "target": value.get("target"),
        "reconciliation": value.get("reconciliation"),
    }


def _run_worker(function: str, proposal: dict[str, Any]) -> tuple[int, str, dict[str, Any] | None]:
    args: tuple[object, ...]
    if function == "snapshot":
        args = (proposal["action_id"],)
    else:
        args = (
            proposal["action_id"],
            proposal["proposal_digest"],
            proposal["idempotency_key"],
            proposal["correlation_id"],
            proposal["approver"],
        )
    exit_code, stdout, stderr = _bench_console("invoke_worker", function, *args)
    marker: dict[str, Any] | None = None
    if function == "reconcile":
        marker = _compact_reconcile(_marker(stdout, "P10_RECONCILE "))
    elif function == "snapshot":
        marker = _compact_snapshot(_marker(stdout, "P10_SNAPSHOT "))
    else:
        expected = {
            "crash_after_reservation": "P10_RESERVATION_COMMITTED_EXIT",
            "crash_after_erp_call": "P10_NATIVE_ERP_COMMITTED_EXIT",
        }.get(function)
        if expected and expected in stdout:
            marker = {"marker": expected}
        elif "P10_ATOMIC_ERP_INSERT_EXIT" in stdout:
            marker = {"marker": "P10_ATOMIC_ERP_INSERT_EXIT"}
        elif "P10_EXPECTED_EXCEPTION " in stdout:
            marker = {"marker": "P10_EXPECTED_EXCEPTION"}
    if exit_code != 0 and function not in {
        "crash_after_reservation",
        "crash_after_erp_call",
    }:
        raise RuntimeError(f"{function} console failed: {stderr[-500:]}")
    return exit_code, str(exit_code), marker


def run_matrix() -> None:
    _copy_scripts()
    exit_code, stdout, stderr = _bench_console("collect_fixtures")
    if exit_code != 0:
        raise RuntimeError(
            f"fixture preparation failed: stdout={stdout[-2_000:]!r}; stderr={stderr[-2_000:]!r}"
        )
    fixture_data = _marker(stdout, "P10_FIXTURES ")
    cases: list[dict[str, Any]] = []
    for record in fixture_data["records"]:
        proposal = record["proposal"]
        boundary = str(record["boundary"])
        case = {
            "action_type": proposal["action_type"],
            "boundary": boundary,
            "run_id": proposal["run_id"],
            "action_id": proposal["action_id"],
            "approval_id": proposal["approval_id"],
            "approval_decision": proposal["approval_decision"],
            "proposal_digest": proposal["proposal_digest"],
            "source_doctype": proposal["source_doctype"],
            "source_name": proposal["source_name"],
            "fixture": record["fixture"],
            "worker": {},
        }
        function = {
            "reservation_committed": "crash_after_reservation",
            "before_erp_call": "fail_before_erp_call",
            "after_erp_call": "crash_after_erp_call",
            "before_receipt": "fail_before_receipt",
        }[boundary]
        worker_exit, nested_exit, marker = _run_worker(function, proposal)
        case["worker"] = {
            "function": function,
            "console_exit_code": worker_exit,
            "worker_process_exit_code": int(nested_exit),
            "marker": marker,
        }
        snapshot_exit, _nested_snapshot_exit, snapshot_value = _run_worker("snapshot", proposal)
        case["snapshot"] = {
            "console_exit_code": snapshot_exit,
            "readback": snapshot_value,
        }
        if boundary in {"reservation_committed", "after_erp_call", "before_receipt"}:
            reconcile_exit, _nested_reconcile_exit, reconcile_value = _run_worker(
                "reconcile", proposal
            )
            case["reconcile"] = {
                "console_exit_code": reconcile_exit,
                "readback": reconcile_value,
            }
            second_snapshot_exit, _nested_second_snapshot_exit, second_snapshot = _run_worker(
                "snapshot", proposal
            )
            case["post_reconcile_snapshot"] = {
                "console_exit_code": second_snapshot_exit,
                "readback": second_snapshot,
            }
        cases.append(case)
        print(
            f"phase10 fault case {proposal['action_type']} / {boundary}: "
            f"worker={worker_exit}, snapshot={snapshot_exit}",
            flush=True,
        )
    artifact = {
        "schema_version": "phase10-r104-fault-matrix-v1",
        "captured_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_revision": _git_output("rev-parse", "HEAD"),
        "working_tree_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--binary"], cwd=REPO)
        ).hexdigest(),
        "execution": {
            "compose_file": str(COMPOSE_FILE),
            "site": SITE,
            "worker_module": "env/dev/p10/phase10_fault_worker.py",
            "fault_control": "isolated bench console subprocess only; no HTTP parameter",
            "readback": "new bench console subprocess after every worker invocation",
            "cases": len(cases),
        },
        "action_types": [spec[0] for spec in ACTION_SPECS],
        "boundaries": list(BOUNDARIES),
        "cases": cases,
    }
    output_path = REPO / "output" / "phase10" / "phase10-r104-real-fault-matrix-v1.json"
    report_path = REPO / "output" / "phase10" / "phase10-r104-real-fault-matrix-v1.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    report_path.write_text(_report(artifact))
    print(json.dumps({"status": "PASS", "cases": len(cases), "output": str(output_path)}))


def _git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _report(artifact: dict[str, Any]) -> str:
    rows = [
        "# Phase 10 R10.4 真实 P2P 进程故障矩阵",
        "",
        f"- 捕获时间: `{artifact['captured_at']}`",
        f"- 基线提交: `{artifact['source_revision']}`",
        f"- 案例数: `{artifact['execution']['cases']}` (7 个动作 x 4 个故障位置)",
        (
            "- 故障注入: 仅通过测试进程内 monkeypatch 与独立 `bench console` 子进程; "
            "生产 HTTP API 没有故障参数。"
        ),
        (
            "- 回读: 每次故障后另起 `bench console` 连接读取 Action、Reservation、Receipt、Run "
            "与 ERP 目标。"
        ),
        "",
        "| 动作 | 故障位置 | worker 退出 | 回读 Action | Reservation | Receipt | ERP 目标 |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for case in artifact["cases"]:
        readback = (
            case["post_reconcile_snapshot"]["readback"]
            if "post_reconcile_snapshot" in case
            else case["snapshot"]["readback"]
        )
        reservation = readback.get("reservation") or {}
        receipt = readback.get("receipt") or {}
        target = readback.get("target") or {}
        rows.append(
            (
                "| `{action}` | `{boundary}` | `{exit}` | `{action_state}` | "
                "`{reservation}` | `{receipt}` | `{target}` |"
            ).format(
                action=case["action_type"],
                boundary=case["boundary"],
                exit=case["worker"]["worker_process_exit_code"],
                action_state=readback.get("action_state"),
                reservation=reservation.get("status"),
                receipt=receipt.get("final_state"),
                target=target.get("name") or "—",
            )
        )
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        run_matrix()
    else:
        parser.error("use --run")


if __name__ == "__main__" and not globals().get("_P10_MATRIX_LIBRARY_EXECUTION"):
    main()
