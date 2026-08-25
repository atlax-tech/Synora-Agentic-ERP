"""Phase 2 P2.6 真实 Bench HTTP 端到端验证（宿主机运行）。

前置条件：
- bench web 已在 127.0.0.1:8000 监听（env/dev/scripts/dev/env.sh up + start）；
- `env/dev/p26/p26_data.py` 已在 bench console 内执行（P26-DATA-OK）；
- 环境变量 SYNORA_P2P_USER_PWD 已设置（测试用户密码）。

覆盖 PLAN P2.6 场景：正常路径、权限拒绝、scope 拒绝、分页（客户端/服务端
fail-closed）、超时、停用对象省略、取消单据排除、缺字段、版本差异。

运行：uv run --python 3.14 env/dev/scripts/p26_e2e.py（或在 services/agent_runtime
下以项目 venv 运行）。全部通过输出 P26-E2E-OK。
"""
import asyncio
import os
import sys
from uuid import UUID, uuid4

import httpx

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "..",
        "services",
        "agent_runtime",
        "src",
    ),
)

from agent_runtime.gateway import (
    GATEWAY_ORIGIN_ENV,
    GatewayClient,
    GatewayProtocolError,
    GatewayRejected,
    GatewayRequest,
    GatewayTimeoutError,
    ItemLookupCall,
    ItemLookupInput,
    OpenMaterialRequestCall,
    OpenMaterialRequestInput,
    OpenPurchaseOrderCall,
    OpenPurchaseOrderInput,
    ProjectedStockCall,
    ProjectedStockInput,
)

BASE = os.environ.get("SYNORA_GATEWAY_ORIGIN", "http://127.0.0.1:8000")
os.environ[GATEWAY_ORIGIN_ENV] = BASE
BUYER = "synora-p1-buyer@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
AONLY_USER = "synora-p26-aonly@dev.localhost"
PWD = os.environ["SYNORA_P2P_USER_PWD"]
COMPANY = "SYNORA-P1 Test Company"
COMPANY_B = "SYNORA-P26 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
ROOT_WAREHOUSE = "All Warehouses - SP1"
ITEM = "SYNORA-P1-Item-1001"
EXECUTE_PATH = "/api/method/synora_agentic_erp.api.execute"
EXECUTE_URL = f"{BASE}{EXECUTE_PATH}"

_results: list[tuple[str, bool, str]] = []


def _record(scenario: str, ok: bool, detail: str = "") -> None:
    _results.append((scenario, ok, detail))
    print(f"P26-{scenario}-{'OK' if ok else 'FAIL'} {detail}")


async def _login(client: httpx.AsyncClient, email: str) -> None:
    login_response = await client.post(
        "/api/method/login", data={"usr": email, "pwd": PWD}
    )
    login_response.raise_for_status()


async def _issue_run(client: httpx.AsyncClient, company: str, warehouse: str | None) -> dict[str, object]:
    data: dict[str, object] = {
        "company": company,
        "correlation_id": str(uuid4()),
    }
    if warehouse is not None:
        data["warehouse"] = warehouse
    response = await client.post(
        "/api/method/synora_agentic_erp.api.issue_run", data=data
    )
    response.raise_for_status()
    body = response.json()["message"]
    assert body["ok"], body
    return {
        **body["run"],
        "run_id": UUID(body["run"]["run_id"]),
        "correlation_id": UUID(body["correlation_id"]),
    }


async def _execute(payload: dict[str, object]) -> httpx.Response:
    """用独立无 cookie client 发送 execute（capability-only 路径拒绝用户凭据）。"""
    normalized = dict(payload)
    normalized["run_id"] = str(normalized["run_id"])
    normalized["correlation_id"] = str(normalized["correlation_id"])
    async with httpx.AsyncClient(base_url=BASE) as bare:
        return await bare.post(EXECUTE_PATH, json=normalized)


async def _client_request(
    request_factory: object, timeout_seconds: float = 10.0
) -> object:
    """用 GatewayClient 执行一次调用，返回响应对象或抛出的异常。"""
    async with GatewayClient(timeout_seconds=timeout_seconds) as gateway:
        return await gateway.execute(await request_factory())


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE) as session:
        # 1. 正常路径：Buyer run 调 item.lookup 得到真实 ERP 数据
        await _login(session, BUYER)
        run = await _issue_run(session, COMPANY, WAREHOUSE)

        async def item_request():
            return GatewayRequest(
                run_id=run["run_id"],
                capability=run["capability"],
                correlation_id=run["correlation_id"],
                tool=ItemLookupCall(
                    name="item.lookup", input=ItemLookupInput(query=ITEM)
                ),
            )

        try:
            result = await _client_request(item_request)
            ok = result.ok and result.data and result.data[0]["item_code"] == ITEM
            _record("BASIC", ok, f"tool={result.tool.name} rows={len(result.data)}")
        except Exception as error:  # noqa: BLE001
            _record("BASIC", False, repr(error))

        # 2. 权限拒绝：Accountant 无 Purchase Order 读权限
        await _login(session, ACCOUNTANT)
        accountant_run = await _issue_run(session, COMPANY, None)

        async def accountant_order_request():
            return GatewayRequest(
                run_id=accountant_run["run_id"],
                capability=accountant_run["capability"],
                correlation_id=accountant_run["correlation_id"],
                tool=OpenPurchaseOrderCall(
                    name="purchase_order.open",
                    input=OpenPurchaseOrderInput(supplier=None),
                ),
            )

        try:
            await _client_request(accountant_order_request)
            _record("PERMISSION_DENIED", False, "expected rejection")
        except GatewayRejected as error:
            _record(
                "PERMISSION_DENIED",
                error.code == "PERMISSION_DENIED" and not error.retryable,
                f"code={error.code}",
            )

        # 3. scope 拒绝：run 限定仓库后请求根仓库被拒
        await _login(session, BUYER)
        buyer_run = await _issue_run(session, COMPANY, WAREHOUSE)

        async def stock_request():
            return GatewayRequest(
                run_id=buyer_run["run_id"],
                capability=buyer_run["capability"],
                correlation_id=buyer_run["correlation_id"],
                tool=ProjectedStockCall(
                    name="stock.projected",
                    input=ProjectedStockInput(
                        item_code=ITEM, warehouse=ROOT_WAREHOUSE
                    ),
                ),
            )

        try:
            await _client_request(stock_request)
            _record("SCOPE_DENIED", False, "expected rejection")
        except GatewayRejected as error:
            _record(
                "SCOPE_DENIED",
                error.code == "SCOPE_DENIED" and not error.retryable,
                f"code={error.code}",
            )

        # 4. 分页客户端 fail-closed：limit=51 在模型层拒绝
        try:
            ItemLookupInput(limit=51)
            _record("PAGINATION_CLIENT", False, "expected ValidationError")
        except Exception as error:  # noqa: BLE001
            _record(
                "PAGINATION_CLIENT",
                "ValidationError" in type(error).__name__,
                type(error).__name__,
            )

        # 5. 分页服务端 fail-closed：绕过客户端 raw POST limit=51
        raw = await _execute(
            {
                "schema_version": "1",
                "run_id": buyer_run["run_id"],
                "capability": buyer_run["capability"],
                "correlation_id": buyer_run["correlation_id"],
                "tool": {"name": "item.lookup", "version": "1", "input": {"limit": 51}},
            },
        )
        raw_body = raw.json()
        code = raw_body.get("message", {}).get("error", {}).get("code")
        _record(
            "PAGINATION_SERVER",
            raw.status_code == 400 and code == "INVALID_INPUT",
            f"http={raw.status_code} code={code}",
        )

        # 6. 超时：极小 deadline 触发客户端 GatewayTimeoutError
        try:
            await _client_request(item_request, timeout_seconds=0.001)
            _record("TIMEOUT", False, "expected GatewayTimeoutError")
        except GatewayTimeoutError:
            _record("TIMEOUT", True, "GatewayTimeoutError")
        except Exception as error:  # noqa: BLE001
            _record("TIMEOUT", False, type(error).__name__)

        # 7. 停用对象省略：disabled supplier 的 open PO 显式省略
        # 11. 跨公司隔离：run 限定公司 A 时，结果不含公司 B 的 PO
        async def orders_request():
            return GatewayRequest(
                run_id=buyer_run["run_id"],
                capability=buyer_run["capability"],
                correlation_id=buyer_run["correlation_id"],
                tool=OpenPurchaseOrderCall(
                    name="purchase_order.open",
                    input=OpenPurchaseOrderInput(supplier=None),
                ),
            )

        try:
            orders = await _client_request(orders_request)
            omitted = orders.completeness.omissions.get(
                "inactive_supplier_documents", 0
            )
            supplier_rows = [
                row
                for row in orders.data
                if row.get("supplier", "") == "SYNORA-P26-Disabled-Supplier"
            ]
            _record(
                "DISABLED_SUPPLIER",
                orders.completeness.status == "PARTIAL"
                and omitted >= 1
                and not supplier_rows,
                f"omissions={omitted} supplier_rows={len(supplier_rows)}",
            )
            company_b_rows = [
                row
                for row in orders.data
                if row.get("supplier", "") == "SYNORA-P26-Supplier-1"
            ]
            _record(
                "CROSS_COMPANY",
                not company_b_rows,
                f"rows={len(orders.data)} company_b_rows={len(company_b_rows)}",
            )
        except Exception as error:  # noqa: BLE001
            _record("DISABLED_SUPPLIER", False, repr(error))
            _record("CROSS_COMPANY", False, repr(error))

        # 8. 取消单据排除：Cancelled MR 不出现在 open MR 结果
        async def requests_request():
            return GatewayRequest(
                run_id=buyer_run["run_id"],
                capability=buyer_run["capability"],
                correlation_id=buyer_run["correlation_id"],
                tool=OpenMaterialRequestCall(
                    name="material_request.open", input=OpenMaterialRequestInput()
                ),
            )

        try:
            requests = await _client_request(requests_request)
            cancelled_rows = [
                row
                for row in requests.data
                if row.get("status") in {"Stopped", "Cancelled"}
            ]
            _record(
                "CANCELLED_MR",
                not cancelled_rows,
                f"rows={len(requests.data)} cancelled={len(cancelled_rows)}",
            )
        except Exception as error:  # noqa: BLE001
            _record("CANCELLED_MR", False, repr(error))

        # 9. 缺字段：stock.projected 缺 item_code → 服务端 INVALID_INPUT
        missing = await _execute(
            {
                "schema_version": "1",
                "run_id": buyer_run["run_id"],
                "capability": buyer_run["capability"],
                "correlation_id": buyer_run["correlation_id"],
                "tool": {"name": "stock.projected", "version": "1", "input": {}},
            },
        )
        missing_code = missing.json().get("message", {}).get("error", {}).get("code")
        _record(
            "MISSING_FIELD",
            missing.status_code == 400 and missing_code == "INVALID_INPUT",
            f"http={missing.status_code} code={missing_code}",
        )

        # 10. 版本差异：schema_version=2 → 服务端 UNSUPPORTED_VERSION
        unsupported = await _execute(
            {
                "schema_version": "2",
                "run_id": buyer_run["run_id"],
                "capability": buyer_run["capability"],
                "correlation_id": buyer_run["correlation_id"],
                "tool": {"name": "item.lookup", "version": "1", "input": {}},
            },
        )
        unsupported_code = (
            unsupported.json().get("message", {}).get("error", {}).get("code")
        )
        _record(
            "UNSUPPORTED_VERSION",
            unsupported.status_code == 400
            and unsupported_code == "UNSUPPORTED_VERSION",
            f"http={unsupported.status_code} code={unsupported_code}",
        )

        # 12. 跨公司权限拒绝: aonly 用户有公司 A 权限、无公司 B 权限
        await _login(session, AONLY_USER)
        aonly_run_a = await _issue_run(session, COMPANY, None)

        async def aonly_item_request():
            return GatewayRequest(
                run_id=aonly_run_a["run_id"],
                capability=aonly_run_a["capability"],
                correlation_id=aonly_run_a["correlation_id"],
                tool=ItemLookupCall(
                    name="item.lookup", input=ItemLookupInput(query=ITEM)
                ),
            )

        try:
            aonly_result = await _client_request(aonly_item_request)
            _record(
                "AONLY_COMPANY_A_ACCESS",
                aonly_result.ok and aonly_result.data,
                f"rows={len(aonly_result.data)}",
            )
        except Exception as error:  # noqa: BLE001
            _record("AONLY_COMPANY_A_ACCESS", False, repr(error))

        # 无公司 B 权限的用户在发行阶段即被拒 (User Permission 生效于 get_list)
        denied = await session.post(
            "/api/method/synora_agentic_erp.api.issue_run",
            data={"company": COMPANY_B, "correlation_id": str(uuid4())},
        )
        denied_body = denied.json().get("message", {})
        denied_code = denied_body.get("error", {}).get("code")
        _record(
            "AONLY_COMPANY_B_DENIED",
            denied.status_code == 403 and denied_code == "SCOPE_DENIED",
            f"http={denied.status_code} code={denied_code}",
        )

    failed = [scenario for scenario, ok, _ in _results if not ok]
    if failed:
        print(f"P26-E2E-FAIL scenarios={failed}")
        raise SystemExit(1)
    print("P26-E2E-OK")


if __name__ == "__main__":
    asyncio.run(main())
