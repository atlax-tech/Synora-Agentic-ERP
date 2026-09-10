"""Write the recorded or real Phase 10 P2P orchestration lab artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUNTIME_SRC = ROOT / "services" / "agent_runtime" / "src"
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from labs.p2p_orchestration.phase10_comparison import (  # noqa: E402
    P2PExperimentReport,
    report_as_json,
    run_phase10_comparison,
)


def _markdown(report: P2PExperimentReport) -> str:
    mode_label = "LAB_ONLY / recorded" if report.mode == "recorded" else "REAL / provider"
    lines = [
        "# Phase 10 P2P 编排恢复对照实验",
        "",
        f"执行模式: `{mode_label}`; 运行状态: `{report.status}`。",
        "",
        f"数据集: `{report.dataset_id}`; digest: `{report.dataset_digest}`; "
        f"模型标识: `{report.model_id}`; provider role: `{report.provider_role or 'n/a'}`; "
        f"provider model: `{report.provider_model or 'n/a'}`; "
        f"context budget: `{report.context_budget or 'unavailable'}`。",
        "事件只负责唤醒和重检, 事件中的授权提示被忽略; 三种策略读取相同的内存 ERP 事实快照。",
        "",
        "| Strategy | Quality | Safety violations | Latency (ms) | "
        "Tokens | Recovery | Complexity |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {row.strategy} | {row.quality:.3f} | {row.safety_violations} | "
        f"{row.latency_ms} | {row.token_count} | {row.recovery_rate:.3f} | "
        f"{row.operational_complexity} |"
        for row in report.results
    )
    if report.trials:
        lines.extend(
            [
                "",
                f"真实固定矩阵: `{len(report.trials)}` trials; 每个案例只执行一次, "
                "模式顺序按案例轮换。",
                "",
                "| Trial | Strategy | Case | Status | Correct | Safe | Latency (ms) | "
                "Tokens | Failure |",
                "| ---: | --- | --- | --- | :---: | :---: | ---: | ---: | --- |",
            ]
        )
        for index, trial in enumerate(report.trials, start=1):
            token_values = (
                trial.prompt_tokens,
                trial.completion_tokens,
                trial.reasoning_tokens,
            )
            tokens = (
                "unavailable"
                if any(value is None for value in token_values)
                else str(sum(value or 0 for value in token_values))
            )
            lines.append(
                f"| {index} | {trial.strategy} | {trial.case_id} | {trial.status} | "
                f"{'yes' if trial.task_correct else 'no'} | "
                f"{'yes' if trial.safety_pass else 'no'} | {trial.latency_ms} | {tokens} | "
                f"{trial.failure_code or ''} |"
            )
    lines.extend(
        [
            "",
            "事件矩阵覆盖重复、乱序、延迟定时唤醒和进程重启。单 Agent 作为原始基线保留失败; "
            "固定 Workflow 使用持久步骤和依赖重检, 多 Agent 增加角色协调但不获得 ERP 写权限。",
            "",
            f"结论: `{report.adoption}`。本实验不授权任何生产采用; "
            "完整 JSON 与失败数据按原样保留。运行状态与采用证据分开解释; "
            "小样本不构成自动采用依据。",
            "",
            report.artifact_policy + "。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("recorded", "real"), default="recorded")
    parser.add_argument(
        "--provider-role",
        choices=("primary", "assist", "backup", "last_local"),
        default="assist",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    suffix = "real-glm-v1" if args.mode == "real" else "recorded-v1"
    output = args.output or ROOT / "output" / "phase10" / f"phase10-p2p-orchestration-{suffix}.json"
    report = args.report or ROOT / "output" / "phase10" / f"phase10-p2p-orchestration-{suffix}.md"
    result = run_phase10_comparison(mode=args.mode, provider_role=args.provider_role)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report_as_json(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({"status": result.status, "adoption": result.adoption}, ensure_ascii=False))
    return 0 if result.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
