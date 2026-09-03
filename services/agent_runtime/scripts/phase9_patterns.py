"""Run and render the Phase 9 P9.4 recorded pattern comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "services" / "agent_runtime" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labs.agent_patterns.phase9_patterns import (  # noqa: E402
    BASELINE_CASE_SPEC_PATH,
    PatternLabDependencyError,
    render_pattern_decision_package,
    run_phase9_pattern_comparison,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-spec", type=Path, default=BASELINE_CASE_SPEC_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "phase9" / "phase9-pattern-comparison.json",
    )
    parser.add_argument(
        "--decision-package",
        type=Path,
        default=ROOT / "output" / "phase9" / "phase9-pattern-comparison.md",
    )
    args = parser.parse_args()
    try:
        report = run_phase9_pattern_comparison(
            case_spec_path=args.case_spec,
            require_graph=True,
        )
    except PatternLabDependencyError as error:
        print(f"P9.4 dependency error: {error}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.decision_package.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.decision_package.write_text(
        render_pattern_decision_package(report),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result": "PASS" if report.all_security_passed else "FAIL",
                "suite": report.manifest.suite,
                "patterns": list(report.manifest.patterns),
                "cases": len(report.cases),
                "trajectory_cases": len(report.trajectories),
                "path": str(args.output),
                "decision_package": str(args.decision_package),
                "fingerprint": report.deterministic_fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report.all_security_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
