"""Synora Phase 1 人工 P2P 运行器（P1.3 / Inc-3）。

纯 HTTP 客户端（requests），模拟人工在 UI 的操作路径：
MR 创建→提交 → 官方 maker 生成 PO → 提交 → Purchase Receipt → 提交
→ Purchase Invoice → 提交；另含 4 个失败用例（权限读/写拒绝、必填缺失、
提交后非法更新）。所有操作以命名测试用户会话进行（Administrator 不参与）。

端点均为上游官方 API：
- REST /api/resource/<doctype>（创建/读取/更新）
- /api/method/login、/api/method/ping（会话与 CSRF）
- /api/method/erpnext.*.make_purchase_order / make_purchase_receipt / make_purchase_invoice
- /api/method/frappe.desk.form.save.savedocs（UI 提交单据的同一端点）

容器内用法（env.sh p2p-run 已封装）：
  cd /home/frappe/bench && env/bin/python /tmp/synora_p2p/p2p_run.py
证据：/tmp/p2p-evidence.json（含每步 user/HTTP 状态/单据名/最终状态/错误类型）。
"""

import json
import os

import requests

BASE = os.environ.get("P2P_BASE_URL", "http://localhost:8000")
PWD = os.environ["SYNORA_P2P_USER_PWD"]
BUYER = "synora-p1-buyer@dev.localhost"
APPROVER = "synora-p1-approver@dev.localhost"
RECEIVER = "synora-p1-receiver@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
ITEM = "SYNORA-P1-Item-1001"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
TRANSACTION_DATE = "2026-08-24"
TIMEOUT_SECONDS = 30

EVIDENCE = []
CREATED = {}


def log(step, user, **kw):
    rec = {"step": step, "user": user, **kw}
    EVIDENCE.append(rec)
    print(f"[p2p] {step:<30} {kw}")


def session_for(email):
    s = requests.Session()
    s.synora_user = email
    r, body = request(s, "POST", "/api/method/login", {"usr": email, "pwd": PWD})
    # 无角色用户登录后 message 为 "No App"（无 desk），但会话已建立（返回 full_name）
    if not r.ok or not body.get("full_name"):
        log("login failed", email, result=outcome(r, body))
        raise RuntimeError(f"login {email} failed: HTTP {r.status_code}")
    r, body = request(s, "GET", "/api/method/ping")
    if not r.ok:
        raise RuntimeError(f"ping {email} failed: HTTP {r.status_code}")
    s.headers["X-Frappe-CSRF-Token"] = r.headers.get("X-Frappe-CSRF-Token", "")
    log("login", email, message=body.get("message", "?"))
    return s


def request(s, method, path, payload=None):
    try:
        r = s.request(method, f"{BASE}{path}", json=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log("transport failure", getattr(s, "synora_user", "unknown"), method=method, path=path,
            error=type(exc).__name__, reconciliation_required=method != "GET")
        raise RuntimeError(f"{method} {path} transport failure: {type(exc).__name__}") from exc
    try:
        body = r.json()
    except ValueError as exc:
        log("invalid response", getattr(s, "synora_user", "unknown"), method=method, path=path,
            status=r.status_code, content_type=r.headers.get("content-type", ""))
        raise RuntimeError(f"{method} {path} returned non-JSON HTTP {r.status_code}") from exc
    if not isinstance(body, dict):
        log("invalid response", getattr(s, "synora_user", "unknown"), method=method, path=path,
            status=r.status_code, body_type=type(body).__name__)
        raise RuntimeError(f"{method} {path} returned non-object JSON")
    return r, body


def outcome(r, body):
    if r.status_code // 100 == 2:
        return "ok"
    error = body.get("exc_type") or body.get("exception") or body.get("message") or "?"
    return f"http_{r.status_code}:{str(error)[:60]}"


def require_ok(step, user, r, body):
    if r.ok:
        return
    detail = str(body.get("exception") or body.get("message") or "")[:200]
    log(step, user, result=outcome(r, body), detail=detail)
    raise RuntimeError(f"{step} failed: {outcome(r, body)}")


def require_object(step, user, body, key):
    value = body.get(key)
    if isinstance(value, dict):
        return value
    log(step, user, result="invalid_response", missing_object=key)
    raise RuntimeError(f"{step} response missing object {key!r}")


def require_value(step, user, body, key):
    value = body.get(key)
    if value is not None:
        return value
    log(step, user, result="invalid_response", missing_value=key)
    raise RuntimeError(f"{step} response missing value {key!r}")


def require_error(step, user, r, body, status, exc_type):
    detail = str(body.get("exception") or body.get("message") or "")[:200]
    log(step, user, result=outcome(r, body), detail=detail,
        expect=f"{status} {exc_type}")
    if r.status_code != status or body.get("exc_type") != exc_type:
        raise RuntimeError(
            f"{step} expected {status} {exc_type}, got {outcome(r, body)}"
        )


def create_doc(s, doctype, vals):
    return request(s, "POST", "/api/resource/" + requests.utils.quote(doctype), vals)


def get_doc(s, doctype, name):
    return request(s, "GET", f"/api/resource/{requests.utils.quote(doctype)}/{requests.utils.quote(name)}")


def submit_doc(s, doctype, name):
    r, doc = get_doc(s, doctype, name)
    if not r.ok:
        return r, doc
    doc_data = require_object(f"get {doctype} before submit", s.synora_user, doc, "data")
    return request(s, "POST", "/api/method/frappe.desk.form.save.savedocs",
                   {"doc": json.dumps(doc_data), "action": "Submit"})


def make(s, method_path, source_name):
    return request(s, "POST", f"/api/method/{method_path}", {"source_name": source_name})


def main():
    buyer = session_for(BUYER)
    approver = session_for(APPROVER)
    receiver = session_for(RECEIVER)
    accountant = session_for(ACCOUNTANT)
    viewer = session_for(VIEWER)

    # ---------- 正常路径 ----------
    r, body = create_doc(buyer, "Material Request", {
        "doctype": "Material Request", "naming_series": "MAT-MR-.YYYY.-",
        "material_request_type": "Purchase", "company": COMPANY,
        "transaction_date": TRANSACTION_DATE,
        "items": [{"item_code": ITEM, "qty": 5, "warehouse": WAREHOUSE,
                   "schedule_date": TRANSACTION_DATE}],
    })
    require_ok("MR create", BUYER, r, body)
    mr_data = require_object("MR create", BUYER, body, "data")
    mr = require_value("MR create", BUYER, mr_data, "name")
    CREATED["Material Request"] = mr
    log("MR create", BUYER, result=outcome(r, body), name=mr,
        status=mr_data["status"], docstatus=mr_data["docstatus"])

    r, body = submit_doc(buyer, "Material Request", mr)
    require_ok("MR submit", BUYER, r, body)
    log("MR submit", BUYER, result=outcome(r, body), name=mr,
        docstatus=(body.get("doc") or {}).get("docstatus", body.get("docstatus")))

    r, body = make(buyer, "erpnext.stock.doctype.material_request.material_request.make_purchase_order", mr)
    require_ok("PO make from MR", BUYER, r, body)
    po = require_object("PO make from MR", BUYER, body, "message")
    po.update({"supplier": SUPPLIER, "schedule_date": TRANSACTION_DATE})
    for it in po["items"]:
        it["rate"] = 100
    r2, b2 = create_doc(buyer, "Purchase Order", po)
    require_ok("PO create (from MR)", BUYER, r2, b2)
    po_data = require_object("PO create (from MR)", BUYER, b2, "data")
    po_name = require_value("PO create (from MR)", BUYER, po_data, "name")
    CREATED["Purchase Order"] = po_name
    log("PO create (from MR)", BUYER, result=outcome(r2, b2), name=po_name,
        status=po_data["status"], mr_ref=po["items"][0].get("material_request"))

    r, body = submit_doc(approver, "Purchase Order", po_name)
    require_ok("PO submit", APPROVER, r, body)
    log("PO submit", APPROVER, result=outcome(r, body), name=po_name)

    r, body = make(receiver, "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", po_name)
    require_ok("PR make from PO", RECEIVER, r, body)
    pr = require_object("PR make from PO", RECEIVER, body, "message")
    r2, b2 = create_doc(receiver, "Purchase Receipt", pr)
    require_ok("PR create (from PO)", RECEIVER, r2, b2)
    pr_data = require_object("PR create (from PO)", RECEIVER, b2, "data")
    pr_name = require_value("PR create (from PO)", RECEIVER, pr_data, "name")
    CREATED["Purchase Receipt"] = pr_name
    log("PR create (from PO)", RECEIVER, result=outcome(r2, b2), name=pr_name,
        status=pr_data["status"], po_ref=pr["items"][0].get("purchase_order"))

    r, body = submit_doc(receiver, "Purchase Receipt", pr_name)
    require_ok("PR submit", RECEIVER, r, body)
    log("PR submit", RECEIVER, result=outcome(r, body), name=pr_name)

    r, body = make(accountant, "erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice", pr_name)
    require_ok("PI make from PR", ACCOUNTANT, r, body)
    pi = require_object("PI make from PR", ACCOUNTANT, body, "message")
    r2, b2 = create_doc(accountant, "Purchase Invoice", pi)
    require_ok("PI create (from PR)", ACCOUNTANT, r2, b2)
    pi_data = require_object("PI create (from PR)", ACCOUNTANT, b2, "data")
    pi_name = require_value("PI create (from PR)", ACCOUNTANT, pi_data, "name")
    CREATED["Purchase Invoice"] = pi_name
    log("PI create (from PR)", ACCOUNTANT, result=outcome(r2, b2), name=pi_name,
        status=pi_data["status"], pr_ref=pi["items"][0].get("purchase_receipt"))

    r, body = submit_doc(accountant, "Purchase Invoice", pi_name)
    require_ok("PI submit", ACCOUNTANT, r, body)
    log("PI submit", ACCOUNTANT, result=outcome(r, body), name=pi_name)

    # ---------- 最终 ERP 状态 ----------
    expected_states = {
        "Material Request": {"status": "Received"},
        "Purchase Order": {"status": "Completed", "currency": "CNY"},
        "Purchase Receipt": {"status": "Completed", "currency": "CNY"},
        "Purchase Invoice": {"status": "Unpaid", "currency": "CNY", "outstanding_amount": 500.0},
    }
    expected_links = {
        "Purchase Order": ("material_request", mr),
        "Purchase Receipt": ("purchase_order", po_name),
        "Purchase Invoice": ("purchase_receipt", pr_name),
    }
    for dt, name in CREATED.items():
        r, body = get_doc(buyer if dt != "Purchase Invoice" else accountant, dt, name)
        require_ok(f"final {dt}", "reader", r, body)
        d = require_object(f"final {dt}", "reader", body, "data")
        log("final state", "reader", doctype=dt, name=name, docstatus=d.get("docstatus"),
            status=d.get("status"), outstanding=d.get("outstanding_amount"), currency=d.get("currency"),
            buying_price_list=d.get("buying_price_list"))
        if d.get("docstatus") != 1:
            raise RuntimeError(f"{dt} {name} final docstatus is not submitted")
        for field, expected in expected_states[dt].items():
            if d.get(field) != expected:
                raise RuntimeError(f"{dt} {name} {field}={d.get(field)!r}, expected {expected!r}")
        if dt in expected_links:
            field, expected = expected_links[dt]
            actual = (d.get("items") or [{}])[0].get(field)
            if actual != expected:
                raise RuntimeError(f"{dt} {name} {field}={actual!r}, expected {expected!r}")

    # ---------- 失败路径 ----------
    # F1 无权限写：viewer（无业务角色）创建 MR → 期望 PermissionError
    r, body = create_doc(viewer, "Material Request", {
        "doctype": "Material Request", "material_request_type": "Purchase",
        "company": COMPANY, "transaction_date": TRANSACTION_DATE,
        "items": [{"item_code": ITEM, "qty": 1}],
    })
    require_error("F1 viewer write MR", VIEWER, r, body, 403, "PermissionError")

    # F2 无权限读：accountant（Accounts User 对 PO 无权限行）读已提交 PO → 期望 403
    r, body = get_doc(accountant, "Purchase Order", po_name)
    require_error("F2 accountant read PO", ACCOUNTANT, r, body, 403, "PermissionError")

    # F3 必填缺失：buyer 创建无 items 的 MR → 期望 MandatoryError
    r, body = create_doc(buyer, "Material Request", {
        "doctype": "Material Request", "material_request_type": "Purchase",
        "company": COMPANY, "transaction_date": TRANSACTION_DATE,
    })
    require_error("F3 MR missing items", BUYER, r, body, 417, "MandatoryError")

    # F4 非法状态转换：buyer 直接 REST 修改已提交 MR → 期望 UpdateAfterSubmit 校验失败
    r, body = request(buyer, "PUT", f"/api/resource/Material%20Request/{requests.utils.quote(mr)}",
                      {"transaction_date": "2026-08-23"})
    require_error("F4 update submitted MR", BUYER, r, body, 417, "UpdateAfterSubmitError")
    print("P2P-RUN-OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("run failed", "runner", error=type(exc).__name__, detail=str(exc)[:200],
            created=CREATED, recovery="只读回查已知单据；结果不明时禁止盲目重跑")
        raise
    finally:
        with open("/tmp/p2p-evidence.json", "w") as evidence_file:
            json.dump(EVIDENCE, evidence_file, indent=1, ensure_ascii=False)
