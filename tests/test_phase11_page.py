from __future__ import annotations

from fastapi.testclient import TestClient

from labs.web_gui.contracts import ActionProposal, TaskSpec
from labs.web_gui.fixtures import FIXTURE_ORDERS, create_app


def test_fixture_api_and_page_share_the_same_read_fact() -> None:
    client = TestClient(create_app())

    api = client.get("/api/purchase-orders/PUR-ORD-0001")
    page = client.get("/purchase-orders/PUR-ORD-0001")

    assert api.status_code == 200
    assert page.status_code == 200
    order = api.json()["order"]
    assert order["purchase_order"] == "PUR-ORD-0001"
    assert order["supplier"] in page.text
    assert order["currency"] in page.text
    assert "LAB_ONLY / SYNTHETIC DATA" in page.text


def test_search_empty_and_unknown_are_explicit() -> None:
    client = TestClient(create_app())

    assert (
        client.get("/api/purchase-orders?q=Supplier%20B").json()["orders"][0]["purchase_order"]
        == "PUR-ORD-0002"
    )
    assert "No purchase orders found" in client.get("/?q=missing").text
    assert client.get("/api/purchase-orders/UNKNOWN").status_code == 404
    assert client.get("/purchase-orders/UNKNOWN").status_code == 404


def test_contracts_reject_untrusted_extra_fields_and_unbounded_budget() -> None:
    spec = TaskSpec(case_id="p11-synthetic-001", purchase_order=FIXTURE_ORDERS[0].purchase_order)
    assert spec.budget.max_actions == 12

    try:
        ActionProposal.model_validate(
            {
                "action_id": "00000000-0000-0000-0000-000000000001",
                "action_type": "click",
                "observation_id": "00000000-0000-0000-0000-000000000002",
                "target_ref": "e1",
                "script": "fetch('/write')",
            }
        )
    except ValueError:
        pass
    else:
        raise AssertionError("arbitrary action fields must be rejected")
