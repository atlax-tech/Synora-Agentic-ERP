"""Command-line entry points for the bounded Phase 11 experiment."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import uvicorn

from labs.web_gui.benchmark import (
    CASES,
    _fixture_api,
    _hybrid_script,
    _lab_server,
    _spec,
    _visual_script,
    run_erp_benchmark,
    run_synthetic_benchmark,
    write_report,
)
from labs.web_gui.browser import BrowserUnavailable, _playwright_sync, run_dom_task
from labs.web_gui.fixtures import FIXTURE_ORDERS, create_app
from labs.web_gui.gui import VisualRun, run_visual_task
from labs.web_gui.hybrid import HybridRun, run_hybrid_task
from labs.web_gui.vision import probe_vision


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2))


def _smoke(mode: str) -> dict[str, object]:
    case = CASES[0]
    if mode == "api":
        return {"mode": mode, "result": _fixture_api(case).model_dump(mode="json")}
    with _lab_server() as base_url:
        spec = _spec(case, mode)
        if mode in {"dom", "aria"}:
            dom_run = run_dom_task(base_url, spec)
            return {
                "mode": mode,
                "result": dom_run.result.model_dump(mode="json"),
                "observations": len(dom_run.observations),
                "security_violations": list(dom_run.security_violations),
            }
        if mode == "vision":
            visual_run: VisualRun = run_visual_task(base_url, spec, _visual_script(case))
            return {
                "mode": mode,
                "result": visual_run.result.model_dump(mode="json"),
                "observations": len(visual_run.observations),
                "model_calls": visual_run.model_calls,
                "security_violations": list(visual_run.security_violations),
                "model": "scripted-fixture-replay",
            }
        hybrid_run: HybridRun = run_hybrid_task(base_url, spec, _hybrid_script(case))
        return {
            "mode": mode,
            "result": hybrid_run.result.model_dump(mode="json"),
            "frames": len(hybrid_run.frames),
            "model_calls": hybrid_run.model_calls,
            "security_violations": list(hybrid_run.security_violations),
            "model": "scripted-fixture-replay",
        }


def _probe_vision() -> dict[str, object]:
    try:
        sync_playwright = _playwright_sync()
    except BrowserUnavailable:
        return {"status": "VISION_PROVIDER_UNAVAILABLE", "failure_code": "PLAYWRIGHT_UNAVAILABLE"}
    with _lab_server() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                service_workers="block",
                accept_downloads=False,
                viewport={"width": 1024, "height": 768},
            )
            try:
                page = context.new_page()
                images: list[bytes] = []
                for purchase_order in ("PUR-ORD-0001", "PUR-ORD-0002"):
                    page.goto(
                        f"{base_url}/?q={purchase_order}",
                        wait_until="domcontentloaded",
                        timeout=10_000,
                    )
                    images.append(page.screenshot(type="png", animations="disabled"))
            finally:
                context.close()
                browser.close()
    result = probe_vision(
        "Read the two synthetic procurement screenshots. Return exactly JSON with "
        "an observations array containing one object per screenshot. Each object must "
        "contain purchase_order, supplier, status, currency, complete. Do not infer "
        "or use outside data.",
        images,
        expected_observations=tuple(
            {
                "purchase_order": order.purchase_order,
                "supplier": order.supplier,
                "status": order.status,
                "currency": order.currency,
            }
            for order in FIXTURE_ORDERS
        ),
    )
    return asdict(result)


def _benchmark(suite: str, repeats: int, output: str | None) -> dict[str, object]:
    if suite == "synthetic":
        report = run_synthetic_benchmark(repeats=repeats)
        default_path = Path("output/phase11/phase11-benchmark-synthetic.json")
    else:
        report = run_erp_benchmark(repeats=repeats)
        default_path = Path("output/phase11/phase11-benchmark-erp-readonly.json")
    path = Path(output) if output else default_path
    path.parent.mkdir(parents=True, exist_ok=True)
    write_report(report, path)
    return {
        "suite": suite,
        "repeats": repeats,
        "output": str(path),
        "status": "WRITTEN",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 11 bounded Web/GUI lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="serve the synthetic procurement page")
    serve.add_argument("--port", type=int, default=8765)

    smoke = subparsers.add_parser("smoke", help="run one bounded read task")
    smoke.add_argument("--mode", choices=("api", "dom", "aria", "vision", "hybrid"), required=True)

    subparsers.add_parser("probe-vision", help="probe configured image providers")

    benchmark = subparsers.add_parser("benchmark", help="run a reproducible comparison suite")
    benchmark.add_argument("--suite", choices=("synthetic", "erp-readonly"), required=True)
    benchmark.add_argument("--repeats", type=int, choices=(1, 2, 3), default=3)
    benchmark.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        if not 1 <= args.port <= 65_535:
            raise SystemExit("--port must be between 1 and 65535")
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")
        return 0
    if args.command == "smoke":
        _json_print(_smoke(args.mode))
        return 0
    if args.command == "probe-vision":
        _json_print(_probe_vision())
        return 0
    _json_print(_benchmark(args.suite, args.repeats, args.output))
    return 0


__all__ = ["main"]
