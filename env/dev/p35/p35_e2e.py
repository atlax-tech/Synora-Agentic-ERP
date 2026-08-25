"""P3.5 Buyer -> Frappe -> Runtime -> BYOK 的真实 HTTP 验收。

前置条件:
- Bench Web 在 127.0.0.1:8000 运行, 且以 host-gateway + Runtime token 配置启动;
- Agent Runtime 在 127.0.0.1:8001 运行并加载 env/dev/.env;
- SYNORA_P2P_USER_PWD 由 env/dev/.env 注入, 不打印。

运行:
    uv run --python 3.14 python env/dev/p35/p35_e2e.py

成功输出 P35-HTTP-OK。`ok` 表示模型解释通过校验; `fallback_validation` 或预算
触发的 `fallback_error` 也表示真实 BYOK 已调用且安全回退生效。脚本只创建
Synora 自有 Run/分析/计划记录, 不写 ERPNext 业务单据, 不输出凭据。
"""

import os
from uuid import uuid4

import httpx

BASE_URL = os.environ.get("SYNORA_GATEWAY_ORIGIN", "http://127.0.0.1:8000")
BUYER = "synora-p1-buyer@dev.localhost"
PASSWORD = os.environ.get("SYNORA_P2P_USER_PWD", "")
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"


def _message(response: httpx.Response) -> dict[str, object]:
    response.raise_for_status()
    body = response.json().get("message")
    assert isinstance(body, dict), "Frappe response message must be an object"
    assert body.get("ok") is True, body
    return body


def main() -> None:
    if not PASSWORD:
        raise SystemExit("SYNORA_P2P_USER_PWD is required (value is never printed)")
    with httpx.Client(base_url=BASE_URL, timeout=45.0, trust_env=False) as client:
        login = client.post("/api/method/login", data={"usr": BUYER, "pwd": PASSWORD})
        login.raise_for_status()
        correlation = str(uuid4())
        issued = _message(
            client.post(
                "/api/method/synora_agentic_erp.api.issue_run",
                data={
                    "company": COMPANY,
                    "warehouse": WAREHOUSE,
                    "goal": GOAL,
                    "correlation_id": correlation,
                },
            )
        )
        run_id = issued["run"]["run_id"]
        analyzed = _message(
            client.post(
                "/api/method/synora_agentic_erp.api.analyze_run",
                data={"run_id": run_id, "correlation_id": str(uuid4())},
            )
        )
        assert analyzed["analysis"]["run_state"] == "PROPOSED"
        planned = _message(
            client.post(
                "/api/method/synora_agentic_erp.api.plan_run",
                data={"run_id": run_id, "correlation_id": str(uuid4())},
            )
        )
        plan_result = planned["plan"]
        plan = plan_result["plan"]
        evidence = plan["evidence"]
        assert plan_result["run_state"] == "SUCCEEDED"
        assert isinstance(plan["enhanced_text"], str) and plan["enhanced_text"].strip()
        assert evidence["status"] in {"ok", "fallback_validation", "fallback_error"}, evidence
        # 真实 provider 已被调用; 若模型文本未通过确定性校验, 页面必须展示回退摘要。
        assert evidence["provider"] not in {"runtime", "byok-runtime"}, evidence
        if evidence["status"] == "fallback_error":
            assert "budget" in evidence["fallback_reason"], evidence
        else:
            assert evidence["prompt_tokens"] > 0, evidence
        detail = _message(
            client.get(
                "/api/method/synora_agentic_erp.api.get_run",
                params={"run_id": run_id},
            )
        )
        assert detail["run"]["run_state"] == "SUCCEEDED"
        print(
            "P35-HTTP-OK"
            f" run_prefix={str(run_id)[:8]}"
            f" run_state={detail['run']['run_state']}"
            f" provider={evidence['provider']}"
            f" status={evidence['status']}"
            f" prompt_tokens={evidence['prompt_tokens']}"
            f" completion_tokens={evidence['completion_tokens']}"
            f" reasoning_tokens={evidence['reasoning_tokens']}"
            f" elapsed_ms={evidence['elapsed_ms']}"
        )


if __name__ == "__main__":
    main()
