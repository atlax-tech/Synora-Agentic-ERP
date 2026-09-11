from __future__ import annotations

import json

from labs.web_gui.cli import main


def test_cli_api_smoke_returns_structured_result(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["smoke", "--mode", "api"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "api"
    assert output["result"]["status"] == "SUCCEEDED"
    assert output["result"]["fields"]["purchase_order"] == "PUR-ORD-0001"


def test_cli_parser_requires_explicit_command() -> None:
    from labs.web_gui.cli import _parser

    parser = _parser()
    assert {"serve", "smoke", "probe-vision", "benchmark"} <= set(
        parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    )
