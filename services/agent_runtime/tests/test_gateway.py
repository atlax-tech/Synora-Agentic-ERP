import asyncio
import json
import os
from typing import Any
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from agent_runtime.gateway import (
    GATEWAY_ORIGIN_ENV,
    GATEWAY_PATH,
    GatewayClient,
    GatewayProtocolError,
    GatewayRejected,
    GatewayRequest,
    GatewayTimeoutError,
    GatewayTransportError,
    ItemLookupCall,
    ItemLookupInput,
)
from pydantic import ValidationError

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")
CAPABILITY = "A" * 43


def _request() -> GatewayRequest:
    return GatewayRequest(
        run_id=RUN_ID,
        capability=CAPABILITY,
        correlation_id=CORRELATION_ID,
        tool=ItemLookupCall(name="item.lookup", input=ItemLookupInput(query="bearing")),
    )


def _client(handler: Any, *, origin: str = "https://erp.example") -> GatewayClient:
    with patch.dict(os.environ, {GATEWAY_ORIGIN_ENV: origin}):
        return GatewayClient(transport=httpx.MockTransport(handler))


def _success() -> dict[str, Any]:
    return {
        "message": {
            "ok": True,
            "schema_version": "1",
            "run_id": str(RUN_ID),
            "state_version": 1,
            "correlation_id": str(CORRELATION_ID),
            "tool": {
                "name": "item.lookup",
                "version": "1",
                "risk": "READ",
                "caller_authorization": "FRAPPE_PERMISSION_AND_RUN_SCOPE",
                "timeout_ms": 5000,
                "max_page_size": 50,
            },
            "authorized_scope": {"company": "Acme", "warehouse": "Stores - A"},
            "snapshot": {
                "captured_at": "2026-08-25 10:00:00",
                "source_modified_at": "2026-08-25 09:00:00",
                "frappe_revision": "f" * 40,
                "erpnext_revision": "e" * 40,
            },
            "completeness": {"status": "COMPLETE", "omissions": {}},
            "page": {"offset": 0, "limit": 20, "returned": 1, "has_more": False},
            "data": [{"item_code": "BEARING-1"}],
        }
    }


def test_client_uses_only_fixed_gateway_path_and_no_user_credentials() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(f"https://erp.example{GATEWAY_PATH}")
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "x-frappe-csrf-token" not in request.headers
        payload = request.read().decode()
        assert CAPABILITY in payload
        assert "initiator" not in payload
        return httpx.Response(200, json=_success())

    async def run() -> None:
        async with _client(handler) as client:
            response = await client.execute(_request())
            assert response.data == [{"item_code": "BEARING-1"}]

    asyncio.run(run())
    assert CAPABILITY not in repr(_request())
    assert CAPABILITY not in str(_request().model_dump(mode="json"))


def test_unknown_tool_and_arbitrary_origin_parts_fail_validation() -> None:
    with pytest.raises(ValidationError):
        GatewayRequest.model_validate(
            {
                **_request().model_dump(mode="json"),
                "tool": {"name": "frappe.get_all", "version": "1", "input": {}},
            }
        )
    for origin in (
        "ftp://erp.example",
        "https://user:secret@erp.example",
        "https://erp.example/arbitrary",
        "https://erp.example?target=other",
    ):
        with patch.dict(os.environ, {GATEWAY_ORIGIN_ENV: origin}):
            with pytest.raises(ValueError):
                GatewayClient()
    with pytest.raises(TypeError):
        GatewayClient("https://evil.example")  # type: ignore[call-arg]


def test_default_client_ignores_ambient_proxy_configuration() -> None:
    async def run() -> None:
        environment = {
            GATEWAY_ORIGIN_ENV: "https://erp.example",
            "HTTP_PROXY": "socks5h://proxy.invalid:1080",
            "HTTPS_PROXY": "socks5h://proxy.invalid:1080",
            "ALL_PROXY": "socks5h://proxy.invalid:1080",
        }
        with patch.dict(os.environ, environment):
            async with GatewayClient():
                pass

    asyncio.run(run())


def test_timeout_configuration_requires_a_finite_positive_deadline() -> None:
    with patch.dict(os.environ, {GATEWAY_ORIGIN_ENV: "https://erp.example"}):
        for timeout in (0.0, -1.0, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                GatewayClient(timeout_seconds=timeout)


def test_timeout_and_protocol_errors_are_typed_and_redacted() -> None:
    async def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("transport detail")

    async def invalid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"ok": True}})

    async def run() -> None:
        for handler, expected in (
            (timeout_handler, GatewayTimeoutError),
            (invalid_handler, GatewayProtocolError),
        ):
            async with _client(handler) as client:
                with pytest.raises(expected) as captured:
                    await client.execute(_request())
                assert CAPABILITY not in str(captured.value)
                assert CAPABILITY not in repr(captured.value)
                assert captured.value.__cause__ is None
                traceback = captured.value.__traceback__
                while traceback:
                    assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                    traceback = traceback.tb_next

    asyncio.run(run())


def test_gateway_failure_is_typed_and_repeated_calls_are_independent() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                403,
                json={
                    "message": {
                        "ok": False,
                        "schema_version": "1",
                        "correlation_id": str(CORRELATION_ID),
                        "error": {
                            "code": "PERMISSION_DENIED",
                            "message": "requested resource is not available",
                            "retryable": False,
                        },
                    }
                },
            )
        return httpx.Response(200, json=_success())

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayRejected) as captured:
                await client.execute(_request())
            assert captured.value.code == "PERMISSION_DENIED"
            assert not captured.value.retryable
            assert CAPABILITY not in repr(captured.value)
            assert (await client.execute(_request())).ok

    asyncio.run(run())
    assert calls == 2


def test_set_cookie_is_never_replayed_on_repeated_calls() -> None:
    cookies: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        cookies.append(request.headers.get("cookie"))
        return httpx.Response(
            200,
            headers={"set-cookie": "session=must-not-be-replayed; Path=/; HttpOnly"},
            json=_success(),
        )

    async def run() -> None:
        async with _client(handler) as client:
            assert (await client.execute(_request())).ok
            assert (await client.execute(_request())).ok

    asyncio.run(run())
    assert cookies == [None, None]


def test_set_cookie_is_never_replayed_by_concurrent_calls() -> None:
    cookies: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        cookies.append(request.headers.get("cookie"))
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            headers={"set-cookie": "session=must-not-be-replayed; Path=/; HttpOnly"},
            json=_success(),
        )

    async def run() -> None:
        async with _client(handler) as client:
            responses = await asyncio.gather(*[client.execute(_request()) for _ in range(8)])
            assert all(response.ok for response in responses)

    asyncio.run(run())
    assert cookies == [None] * 8


def test_partial_response_transport_error_clears_secret_and_closes_stream() -> None:
    class PartialErrorStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            yield CAPABILITY.encode()
            raise httpx.ReadError(CAPABILITY)

        async def aclose(self) -> None:
            self.closed = True

    stream = PartialErrorStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayTransportError) as captured:
                await client.execute(_request())
            assert captured.value.__context__ is None
            traceback = captured.value.__traceback__
            while traceback:
                if traceback.tb_frame.f_code.co_name == "execute":
                    assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                traceback = traceback.tb_next

    asyncio.run(run())
    assert stream.closed


def test_cancelled_stream_clears_partial_secret_and_closes_response() -> None:
    class CancelledStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False
            self.waiting = asyncio.Event()

        async def __aiter__(self):
            yield CAPABILITY.encode()
            self.waiting.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.closed = True

    async def run() -> None:
        stream = CancelledStream()

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=stream)

        async with _client(handler) as client:
            task = asyncio.create_task(client.execute(_request()))
            await stream.waiting.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as captured:
                await task
            traceback = captured.value.__traceback__
            while traceback:
                if traceback.tb_frame.f_code.co_name == "execute":
                    assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                traceback = traceback.tb_next
        assert stream.closed

    asyncio.run(run())


def test_unexpected_stream_error_is_typed_and_clears_partial_secret() -> None:
    class UnexpectedErrorStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield CAPABILITY.encode()
            raise RuntimeError(CAPABILITY)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=UnexpectedErrorStream())

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayTransportError) as captured:
                await client.execute(_request())
            assert CAPABILITY not in str(captured.value)
            assert captured.value.__context__ is None
            traceback = captured.value.__traceback__
            while traceback:
                if traceback.tb_frame.f_code.co_name == "execute":
                    assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                traceback = traceback.tb_next

    asyncio.run(run())


def test_mismatched_or_oversized_response_fails_closed() -> None:
    mismatched = _success()
    mismatched["message"]["correlation_id"] = "2d2501e0-3d7c-47bb-8c4f-6782c59f292c"

    class OversizedStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False
            self.yielded = 0

        async def __aiter__(self):
            chunk = b"x" * 1_000_000
            for response_chunk in (chunk, chunk, CAPABILITY.encode()):
                self.yielded += 1
                yield response_chunk

        async def aclose(self) -> None:
            self.closed = True

    oversized_stream = OversizedStream()

    async def mismatch_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mismatched)

    async def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=oversized_stream)

    async def run() -> None:
        for handler in (mismatch_handler, oversized_handler):
            async with _client(handler) as client:
                with pytest.raises(GatewayProtocolError) as captured:
                    await client.execute(_request())
                assert captured.value.__context__ is None
                traceback = captured.value.__traceback__
                while traceback:
                    if traceback.tb_frame.f_code.co_name == "execute":
                        assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                    traceback = traceback.tb_next

    asyncio.run(run())
    assert oversized_stream.closed
    assert oversized_stream.yielded == 3


def test_run_id_and_tool_identity_mismatches_fail_closed() -> None:
    run_mismatch = _success()
    run_mismatch["message"]["run_id"] = str(UUID("8f0a7e0e-9a30-4b39-a5e3-6c2d4e5f6a7b"))
    tool_name_mismatch = _success()
    tool_name_mismatch["message"]["tool"]["name"] = "supplier.lookup"
    tool_version_mismatch = _success()
    tool_version_mismatch["message"]["tool"]["version"] = "2"
    bodies = {
        "run_id": run_mismatch,
        "tool_name": tool_name_mismatch,
        "tool_version": tool_version_mismatch,
    }

    async def run() -> None:
        for response_body in bodies.values():

            async def handler(_request: httpx.Request, body: Any = response_body) -> httpx.Response:
                return httpx.Response(200, json=body)

            async with _client(handler) as client:
                with pytest.raises(GatewayProtocolError):
                    await client.execute(_request())

    asyncio.run(run())


def test_empty_non_json_and_extra_top_level_keys_fail_closed() -> None:
    async def empty_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    async def non_json_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async def extra_key_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"extra": True, "message": _success()["message"]})

    async def run() -> None:
        for handler in (empty_handler, non_json_handler, extra_key_handler):
            async with _client(handler) as client:
                with pytest.raises(GatewayProtocolError):
                    await client.execute(_request())

    asyncio.run(run())


def test_gateway_failure_carries_retryable_flag() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "message": {
                    "ok": False,
                    "schema_version": "1",
                    "correlation_id": str(CORRELATION_ID),
                    "error": {"code": "ERP_ERROR", "message": "upstream busy", "retryable": True},
                }
            },
        )

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayRejected) as captured:
                await client.execute(_request())
            assert captured.value.code == "ERP_ERROR"
            assert captured.value.retryable

    asyncio.run(run())


def test_failure_correlation_and_success_schema_version_mismatch_fail_closed() -> None:
    failure_mismatch = {
        "message": {
            "ok": False,
            "schema_version": "1",
            "correlation_id": "2d2501e0-3d7c-47bb-8c4f-6782c59f292c",
            "error": {"code": "PERMISSION_DENIED", "message": "denied", "retryable": False},
        }
    }
    schema_version_mismatch = _success()
    schema_version_mismatch["message"]["schema_version"] = "2"

    async def run() -> None:
        for response_body in (failure_mismatch, schema_version_mismatch):

            async def handler(_request: httpx.Request, body: Any = response_body) -> httpx.Response:
                return httpx.Response(200, json=body)

            async with _client(handler) as client:
                with pytest.raises(GatewayProtocolError):
                    await client.execute(_request())

    asyncio.run(run())


def test_reflected_capability_in_unknown_error_is_not_exposed() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "message": {
                    "ok": False,
                    "schema_version": "1",
                    "correlation_id": str(CORRELATION_ID),
                    "error": {
                        "code": CAPABILITY,
                        "message": CAPABILITY,
                        "retryable": False,
                    },
                }
            },
        )

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayProtocolError) as captured:
                await client.execute(_request())
            assert CAPABILITY not in str(captured.value)
            assert CAPABILITY not in repr(captured.value)
            assert captured.value.__cause__ is None
            traceback = captured.value.__traceback__
            while traceback:
                assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                traceback = traceback.tb_next

    asyncio.run(run())


def test_reflected_capability_in_known_error_or_success_is_not_exposed() -> None:
    known_failure = {
        "message": {
            "ok": False,
            "schema_version": "1",
            "correlation_id": str(CORRELATION_ID),
            "error": {
                "code": "PERMISSION_DENIED",
                "message": CAPABILITY,
                "retryable": False,
            },
        }
    }
    mismatched_success = _success()
    mismatched_success["message"]["correlation_id"] = "2d2501e0-3d7c-47bb-8c4f-6782c59f292c"
    mismatched_success["message"]["data"] = [{"echo": CAPABILITY}]

    def handler_for(response_body: dict[str, Any]) -> Any:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json=response_body)

        return handler

    async def run() -> None:
        for response_body in (known_failure, mismatched_success):
            async with _client(handler_for(response_body)) as client:
                with pytest.raises(GatewayProtocolError) as captured:
                    await client.execute(_request())
                traceback = captured.value.__traceback__
                execute_frame_seen = False
                while traceback:
                    if traceback.tb_frame.f_code.co_name == "execute":
                        execute_frame_seen = True
                        assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                    traceback = traceback.tb_next
                assert execute_frame_seen

    asyncio.run(run())


def test_unicode_escaped_capability_reflection_is_not_exposed() -> None:
    mismatched_success = _success()
    mismatched_success["message"]["correlation_id"] = "2d2501e0-3d7c-47bb-8c4f-6782c59f292c"
    mismatched_success["message"]["data"] = [{"echo": CAPABILITY}]
    escaped_body = json.dumps(mismatched_success).replace(CAPABILITY, r"\u0041" * 43)
    assert CAPABILITY not in escaped_body

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=escaped_body.encode())

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayProtocolError) as captured:
                await client.execute(_request())
            traceback = captured.value.__traceback__
            execute_frame_seen = False
            while traceback:
                if traceback.tb_frame.f_code.co_name == "execute":
                    execute_frame_seen = True
                    assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                traceback = traceback.tb_next
            assert execute_frame_seen

    asyncio.run(run())


def test_unicode_escaped_capability_in_top_level_list_is_not_exposed() -> None:
    escaped_body = '[{"echo":"' + (r"\u0041" * 43) + '"}]'
    assert CAPABILITY not in escaped_body

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=escaped_body.encode())

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayProtocolError) as captured:
                await client.execute(_request())
            traceback = captured.value.__traceback__
            while traceback:
                if traceback.tb_frame.f_code.co_name == "execute":
                    assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                traceback = traceback.tb_next

    asyncio.run(run())


def test_non_standard_numbers_and_duplicate_keys_fail_closed() -> None:
    non_standard_number = json.dumps(_success()).replace(
        '"data": [{"item_code": "BEARING-1"}]',
        '"data": [{"value": NaN}]',
    )
    escaped_capability = r"\u0041" * 43
    duplicate_data = json.dumps(_success()).replace(
        '"data": [{"item_code": "BEARING-1"}]',
        f'"data": [{{"echo": "{escaped_capability}"}}], "data": [{{"item_code": "BEARING-1"}}]',
    )
    assert CAPABILITY not in duplicate_data

    def handler_for(body: str) -> Any:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body.encode())

        return handler

    async def run() -> None:
        for body in (non_standard_number, duplicate_data):
            async with _client(handler_for(body)) as client:
                with pytest.raises(GatewayProtocolError) as captured:
                    await client.execute(_request())
                traceback = captured.value.__traceback__
                while traceback:
                    if traceback.tb_frame.f_code.co_name == "execute":
                        assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                    traceback = traceback.tb_next

    asyncio.run(run())


def test_request_validation_errors_hide_capability_input() -> None:
    reflected_secret = "reflected-secret-" + "x" * 600
    payload = _request().model_dump(mode="json")
    payload["capability"] = reflected_secret
    payload["unexpected"] = reflected_secret

    with pytest.raises(ValidationError) as captured:
        GatewayRequest.model_validate(payload)

    assert reflected_secret not in str(captured.value)
    assert reflected_secret not in repr(captured.value)

    unicode_token = "é" * 43
    payload = _request().model_dump(mode="json")
    payload["capability"] = unicode_token
    with pytest.raises(ValidationError) as captured:
        GatewayRequest.model_validate(payload)
    assert unicode_token not in str(captured.value)


def test_capability_in_tool_input_is_rejected_without_traceback_leak() -> None:
    request = GatewayRequest(
        run_id=RUN_ID,
        capability=CAPABILITY,
        correlation_id=CORRELATION_ID,
        tool=ItemLookupCall(name="item.lookup", input=ItemLookupInput(query=CAPABILITY)),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("transport must not be called")

    async def run() -> None:
        async with _client(handler) as client:
            with pytest.raises(GatewayProtocolError) as captured:
                await client.execute(request)
            traceback = captured.value.__traceback__
            while traceback:
                if traceback.tb_frame.f_code.co_name == "execute":
                    assert CAPABILITY not in repr(traceback.tb_frame.f_locals)
                traceback = traceback.tb_next

    asyncio.run(run())
