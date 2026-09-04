"""Run the Phase 9 P9.5 same-model A/B suite."""

from __future__ import annotations

import argparse
import json
import re
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
    parser.add_argument(
        "--model-role",
        choices=("primary", "assist", "backup", "last_local"),
        default=None,
        help="real-mode named provider role; recorded mode ignores it",
    )
    parser.add_argument(
        "--completion-token-cap",
        type=int,
        default=128,
        help="bounded completion cap for both A/B arms",
    )
    parser.add_argument(
        "--threshold-profile",
        choices=("approved-qwen-v1", "relative-model-v1", "quality-first-model-v1"),
        default="approved-qwen-v1",
    )
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
    try:
        report = run_phase9_ab(
            case_spec_path=args.case_spec,
            mode=args.mode,
            model_role=args.model_role,
            completion_token_cap=args.completion_token_cap,
            threshold_profile=args.threshold_profile,
        )
    except Exception as error:
        # Preserve a bounded first-failure artifact without echoing provider
        # URLs, exception messages, prompts, or response text.
        raw_code = getattr(error, "failure_code", "EVALUATION_FAILED")
        failure_code = (
            raw_code
            if isinstance(raw_code, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", raw_code)
            else "EVALUATION_FAILED"
        )
        failure_output = args.output.with_name(f"{args.output.stem}-failure{args.output.suffix}")
        failure_package = args.decision_package.with_name(
            f"{args.decision_package.stem}-failure{args.decision_package.suffix}"
        )
        failure_output.parent.mkdir(parents=True, exist_ok=True)
        failure_package.parent.mkdir(parents=True, exist_ok=True)
        failure_output.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "suite": "P9.5-multi-agent-ab",
                    "provider_mode": args.mode,
                    "status": "BLOCKED",
                    "failure_code": failure_code,
                    "artifact_policy": "first failure preserved; no provider text persisted",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        failure_package.write_text(
            "# Phase 9 P9.5 A/B 首次失败\n\n"
            f"状态：`BLOCKED`；模式：`{args.mode}`；失败代码：`{failure_code}`。\n\n"
            "已保留脱敏失败 artifact；未写入 Prompt、响应原文、URL、Secret 或隐藏推理。\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "result": "BLOCKED",
                    "mode": args.mode,
                    "failure_code": failure_code,
                    "path": str(failure_output),
                    "decision_package": str(failure_package),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
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
