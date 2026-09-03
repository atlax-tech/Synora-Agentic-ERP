"""Run the Phase 9 P9.5 same-model A/B suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "services" / "agent_runtime" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_runtime.evaluation.phase9_ab import (  # noqa: E402
    BASELINE_CASE_SPEC_PATH,
    render_ab_decision_package,
    run_phase9_ab,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("recorded", "real"), default="recorded")
    parser.add_argument("--case-spec", type=Path, default=BASELINE_CASE_SPEC_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "phase9" / "phase9-ab-recorded-v1.json",
    )
    parser.add_argument(
        "--decision-package",
        type=Path,
        default=ROOT / "output" / "phase9" / "phase9-ab-recorded-v1.md",
    )
    args = parser.parse_args()
    report = run_phase9_ab(case_spec_path=args.case_spec, mode=args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.decision_package.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.decision_package.write_text(
        render_ab_decision_package(report),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "result": report.status,
                "mode": report.manifest.provider_mode,
                "single": report.single_metrics.model_dump(mode="json"),
                "multi": report.multi_metrics.model_dump(mode="json"),
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
