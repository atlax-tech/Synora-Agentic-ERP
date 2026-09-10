"""Write the deterministic Phase 10 P2P orchestration lab artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from labs.p2p_orchestration.phase10_comparison import (  # noqa: E402
    P2PExperimentReport,
    report_as_json,
    run_phase10_comparison,
)


def _markdown(report: P2PExperimentReport) -> str:
    lines = [
        "# Phase 10 P2P 编排恢复对照实验",
        "",
        "状态: `LAB_ONLY / " + report.status + "`",
        "",
        f"数据集: `{report.dataset_id}`; digest: `{report.dataset_digest}`; "
        f"模型标识: `{report.model_id}`。",
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
    lines.extend(
        [
            "",
            "事件矩阵覆盖重复、乱序、延迟定时唤醒和进程重启。单 Agent 作为原始基线保留失败; "
            "固定 Workflow 使用持久步骤和依赖重检, 多 Agent 增加角色协调但不获得 ERP 写权限。",
            "",
            f"结论: `{report.adoption}`。本实验不授权任何生产采用; "
            "完整 JSON 与失败数据按原样保留。",
            "",
            report.artifact_policy + "。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "phase10" / "phase10-p2p-orchestration-recorded-v1.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "output" / "phase10" / "phase10-p2p-orchestration-recorded-v1.md",
    )
    args = parser.parse_args()
    result = run_phase10_comparison()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report_as_json(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.report.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({"status": result.status, "adoption": result.adoption}, ensure_ascii=False))
    return 0 if result.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
