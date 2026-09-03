"""Run and render the frozen Phase 9 single-agent baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "services" / "agent_runtime" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_runtime.evaluation.phase9_baseline import (  # noqa: E402
    BASELINE_CASE_SPEC_PATH,
    BaselineReport,
    render_baseline_decision_package,
    run_phase9_single_agent_baseline,
)


def _write_report(output: Path, decision_package: Path, report: BaselineReport) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    decision_package.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    decision_package.write_text(render_baseline_decision_package(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("recorded", "real"), default="recorded")
    parser.add_argument("--case-spec", type=Path, default=BASELINE_CASE_SPEC_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "phase9" / "phase9-single-agent-baseline.json",
    )
    parser.add_argument(
        "--decision-package",
        type=Path,
        default=ROOT / "output" / "phase9" / "phase9-single-agent-baseline.md",
    )
    parser.add_argument("--code-head", default=None)
    parser.add_argument("--model-name", default=None)
    args = parser.parse_args()
    report = run_phase9_single_agent_baseline(
        case_spec_path=args.case_spec,
        mode=args.mode,
        code_head=args.code_head,
        model_name=args.model_name,
    )
    _write_report(args.output, args.decision_package, report)
    print(
        json.dumps(
            {
                "result": "PASS" if report.all_security_passed else "FAIL",
                "suite": report.manifest.suite,
                "mode": report.manifest.provider_mode,
                "path": str(args.output),
                "decision_package": str(args.decision_package),
                "fingerprint": report.deterministic_fingerprint,
                "task_correctness_rate": report.metrics.task_correctness_rate,
                "security_violations": report.metrics.security_violations,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report.all_security_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
