from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from labs.web_gui.cli import main


def test_cli_api_smoke_returns_structured_result(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["smoke", "--mode", "api"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "api"
    assert output["result"]["status"] == "SUCCEEDED"
    assert output["result"]["fields"]["purchase_order"] == "PUR-ORD-0001"


def test_cli_parser_requires_explicit_command() -> None:
    from labs.web_gui.cli import _parser

    parser = _parser()
    assert parser.parse_args(["smoke", "--mode", "api"]).command == "smoke"
    assert parser.parse_args(["probe-vision"]).command == "probe-vision"
    assert parser.parse_args(["benchmark", "--suite", "synthetic"]).command == "benchmark"


def test_cli_module_resolves_workspace_runtime_without_pythonpath() -> None:
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-m", "labs.web_gui", "benchmark", "--help"],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--engine {deterministic,live}" in completed.stdout
