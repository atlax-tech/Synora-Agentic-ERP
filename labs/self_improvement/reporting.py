"""Evidence summaries and LAB_ONLY adoption cards for Phase 12."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .artifacts import write_json_once, write_text_once
from .contracts import DatasetManifest, ExperimentRecord, TrainingArtifact
from .evaluation import BootstrapSummary, grouped_bootstrap
from .rl import intentionally_bad_reward_config, run_action_sequence, safe_reward_config

_DEFAULT_STAGE_STATUS = "BLOCKED / LAB_ONLY"


def _report_method(record: ExperimentRecord) -> str:
    """Keep live provider evidence separate from deterministic replay trials."""
    if record.method == "baseline" and record.model != "deterministic-replay":
        return "live-baseline"
    return record.method


def _p95(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


def summarize_methods(records: Iterable[ExperimentRecord]) -> dict[str, dict[str, float]]:
    """Aggregate quality, safety, usage and latency without dropping failures."""
    grouped: dict[str, list[ExperimentRecord]] = defaultdict(list)
    for record in records:
        grouped[_report_method(record)].append(record)
    summaries: dict[str, dict[str, float]] = {}
    for method, values in sorted(grouped.items()):
        count = len(values)
        provider_values = [record for record in values if record.model != "deterministic-replay"]
        known_usage = sum(
            record.prompt_tokens is not None and record.completion_tokens is not None
            for record in provider_values
        )
        summaries[method] = {
            "count": float(count),
            "verifier_rate": sum(record.verifier_passed for record in values) / count,
            "safety_rate": sum(record.safety_passed for record in values) / count,
            "mean_score": sum(record.score for record in values) / count,
            "calls": float(sum(record.calls for record in values)),
            "known_usage_records": float(known_usage),
            "unknown_usage_records": float(len(provider_values) - known_usage),
            "usage_not_applicable_records": float(count - len(provider_values)),
            "mean_elapsed_ms": sum(record.elapsed_ms for record in values) / count,
            "p95_elapsed_ms": _p95(record.elapsed_ms for record in values),
            "failed_records": float(sum(record.status != "SUCCEEDED" for record in values)),
        }
    return summaries


def _bootstrap_payload(summary: BootstrapSummary) -> dict[str, object]:
    return {
        "method_a": summary.method_a,
        "method_b": summary.method_b,
        "groups": summary.groups,
        "delta": summary.delta,
        "lower_95": summary.lower_95,
        "upper_95": summary.upper_95,
        "conclusion": summary.conclusion,
        "samples": 2_000,
    }


def heldout_bootstrap(records: Iterable[ExperimentRecord]) -> tuple[dict[str, object], ...]:
    values = tuple(records)
    by_method: dict[str, tuple[ExperimentRecord, ...]] = {}
    for method in {_report_method(record) for record in values if record.split == "test"}:
        by_method[method] = tuple(
            record
            for record in values
            if record.split == "test" and _report_method(record) == method
        )
    baseline = by_method.get("baseline") or by_method.get("live-baseline")
    if not baseline:
        return ()
    comparisons: list[dict[str, object]] = []
    for method in ("prompt-candidate", "skill-candidate", "reflection", "best-of-3"):
        candidate = by_method.get(method)
        if not candidate:
            continue
        comparisons.append(
            _bootstrap_payload(
                grouped_bootstrap(
                    baseline,
                    candidate,
                    method_a=method,
                    method_b="baseline",
                )
            )
        )
    return tuple(comparisons)


def reward_hacking_evidence(manifest: DatasetManifest) -> dict[str, object]:
    case = next(case for case in manifest.cases if case.split == "train")
    actions = ("purchase_order.open", "purchase_order.open", "purchase_order.open", "FINISH")
    safe = run_action_sequence(case, actions, reward=safe_reward_config())
    bad = run_action_sequence(case, actions, reward=intentionally_bad_reward_config())
    safe_total = sum(transition.reward for transition in safe)
    bad_total = sum(transition.reward for transition in bad)
    final = safe[-1]
    return {
        "case_id": case.case_id,
        "actions": list(actions),
        "safe_reward_total": safe_total,
        "bad_reward_total": bad_total,
        "bad_reward_higher": bad_total > safe_total,
        "final_status": final.status,
        "task_verifier_passed": final.status == case.expected_status and final.safety_passed,
        "is_prespecified_negative": True,
    }


def rubric_scores() -> dict[str, int]:
    """Provisional scores; final scores require the independent exit review."""
    return {
        "D1_business_correctness": 3,
        "D2_identity_scope": 4,
        "D3_state_idempotency_recovery": 3,
        "D4_agent_trust_cost": 3,
        "D5_security_data_protection": 4,
        "D6_ui_accessibility_bilingual": 3,
        "D7_testing_reproduction": 3,
        "D8_governance_traceability": 3,
        "D9_simplicity_operability": 3,
    }


def risk_register() -> tuple[dict[str, object], ...]:
    return (
        {
            "risk": "train_test_or_oracle_leakage",
            "likelihood": 3,
            "impact": 3,
            "status": "MITIGATED_BY_GROUPED_MANIFEST",
        },
        {
            "risk": "candidate_capability_expansion",
            "likelihood": 2,
            "impact": 4,
            "status": "MITIGATED_BY_BOUNDARY_DIGEST",
        },
        {
            "risk": "sensitive_trace_exfiltration",
            "likelihood": 2,
            "impact": 4,
            "status": "MITIGATED_BY_REDACTED_SYNTHETIC_INPUT",
        },
        {
            "risk": "self_eval_or_reward_substitutes_for_correctness",
            "likelihood": 3,
            "impact": 3,
            "status": "MITIGATED_BY_SEPARATE_VERIFIER",
        },
        {
            "risk": "statistics_or_successful_trial_selection",
            "likelihood": 3,
            "impact": 3,
            "status": "OPEN_UNTIL_FINAL_EVIDENCE_REVIEW",
        },
        {
            "risk": "budget_reset_after_interruption",
            "likelihood": 2,
            "impact": 3,
            "status": "MITIGATED_BY_REQUEST_RESERVATION",
        },
        {
            "risk": "arbitrary_code_in_weight_artifact",
            "likelihood": 2,
            "impact": 4,
            "status": "MITIGATED_BY_FINITE_JSON_ONLY",
        },
        {
            "risk": "small_policy_cannot_learn_task",
            "likelihood": 2,
            "impact": 2,
            "status": "REPORT_LIMITATION",
        },
        {
            "risk": "harness_sync_without_authorization",
            "likelihood": 2,
            "impact": 3,
            "status": "WAITING_FOR_SEPARATE_SYNC_APPROVAL",
        },
    )


def build_summary(
    manifest: DatasetManifest,
    records: Iterable[ExperimentRecord],
    training_artifacts: Iterable[TrainingArtifact],
    *,
    code_version: str,
    status: str = _DEFAULT_STAGE_STATUS,
    stage_result: dict[str, object] | None = None,
) -> dict[str, object]:
    values = tuple(records)
    scores = rubric_scores()
    return {
        "schema_version": "1",
        "status": status,
        "code_version": code_version,
        "dataset_id": manifest.dataset_id,
        "dataset_digest": manifest.dataset_digest,
        "evidence_code_versions": sorted({record.code_version for record in values}),
        "split_counts": manifest.split_counts,
        "group_counts": manifest.group_counts,
        "methods": summarize_methods(values),
        "heldout_bootstrap": heldout_bootstrap(values),
        "training_artifacts": [artifact.model_dump(mode="json") for artifact in training_artifacts],
        "reward_hacking": reward_hacking_evidence(manifest),
        "rubric": scores,
        "rubric_total": sum(scores.values()),
        "rubric_average": sum(scores.values()) / len(scores),
        "risks": risk_register(),
        "stage_verification": stage_result,
        "constraints": {
            "max_model_calls": 1_200,
            "max_input_chars": 4_000,
            "max_output_tokens": 512,
            "provider_request_envelope_tokens": 2_048,
            "network_runtime": "disabled_for_replay_and_training",
            "business_runtime_changes": False,
        },
    }


def render_adoption_card(summary: dict[str, object]) -> str:
    methods = summary["methods"]
    bootstraps = summary["heldout_bootstrap"]
    lines = [
        "# Phase 12 Adoption Card",
        "",
        f"状态: `{summary['status']}`. 本卡为离线实验, 不授予业务 Runtime 或工具权限.",
        "",
        "## Problem",
        "",
        "历史失败需要先审核, 隔离和复现, 再判断 Prompt/Skill 指导, 后训练或 "
        "Agentic RL 是否值得保留.",
        "",
        "## Minimal Lab",
        "",
        "固定六类只读采购场景, 60 个组, 120 个案例, 按组划分 train/dev/test; "
        "所有结果经同一 verifier, 训练权重使用有限 JSON.",
        "",
        "## Evidence",
        "",
        f"- 数据集: `{summary['dataset_id']}`, digest `{summary['dataset_digest']}`.",
        f"- 方法汇总: `{json.dumps(methods, ensure_ascii=True, sort_keys=True)}`.",
        f"- held-out bootstrap: `{json.dumps(bootstraps, ensure_ascii=True, sort_keys=True)}`.",
        "- RAG 只引用 Phase 8 的独立 FTS5/vector 对照, 不与本阶段数据混排.",
        "",
        "## Decision",
        "",
        "每条 held-out 比较的结论描述 method_a 相对 method_b 的差异; "
        "Prompt 候选区间跨 0 时保留基线, "
        "Skill 候选由基线相对其的正差值证明候选退化, 因而拒绝候选. "
        "SFT, DPO, REINFORCE 仅保留为本地策略实验, 不能加载到业务主线.",
        "",
        "## Known Negative",
        "",
        "故意错误奖励可以让重复调用获得更高分, 但任务 verifier 仍失败; "
        "这是预设奖励黑客负例, 不是生产效果结论.",
        "",
        "## Rollback",
        "",
        "候选选择和回滚写不可变 LAB receipt 并更新 active-version 指针; "
        "回滚必须恢复父版本内容哈希, "
        "未获独立授权前不改变业务 Registry.",
        "",
        "## Limitations",
        "",
        "Provider 可用性, usage 缺失, 小样本 bootstrap 和最终 Harness 同步授权会限制采用结论; "
        "本卡不声称生产部署, 客户采用或通用模型提升.",
    ]
    return "\n".join(lines) + "\n"


def render_stage_report(summary: dict[str, object]) -> str:
    methods = summary["methods"]
    rows = [
        "| method | count | verifier | safety | score | calls | unknown usage | "
        "usage n/a | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if isinstance(methods, dict):
        for method, values in methods.items():
            if isinstance(values, dict):
                rows.append(
                    "| {method} | {count:.0f} | {verifier_rate:.3f} | {safety_rate:.3f} | "
                    "{mean_score:.3f} | {calls:.0f} | {unknown_usage_records:.0f} | "
                    "{usage_not_applicable_records:.0f} | {p95_elapsed_ms:.1f} |".format(
                        method=method, **values
                    )
                )
    scores = summary["rubric"]
    risks = summary["risks"]
    status = str(summary["status"])
    if status == _DEFAULT_STAGE_STATUS:
        review_line = (
            "第二轮独立对抗审查结果为 `CHANGES_REQUIRED`; 按两轮上限标记为 BLOCKED, "
            "Harness 文件级同步也未闭合, 不能写阶段 PASS."
        )
    else:
        verification = summary.get("stage_verification")
        review = verification.get("review_status") if isinstance(verification, dict) else "PENDING"
        harness = (
            verification.get("harness_status") if isinstance(verification, dict) else "PENDING"
        )
        review_line = (
            f"独立对抗审查状态为 `{review}`, Harness 状态为 `{harness}`; 状态由严格证据门禁计算。"
        )
    lines = [
        "# Phase 12 阶段报告",
        "",
        f"状态: `{status}`. {review_line}",
        "",
        "## 业务问题与数据流",
        "",
        "历史失败证据先经过来源, 完整性, 脱敏和相关性审核; 合成采购场景再按组冻结为 "
        "train/dev/test. "
        "模型或小策略只看到允许的任务输入和观察状态, 输出经安全门禁与确定性 verifier, "
        "oracle 只留在评分侧. 结果, 调用预算, 训练权重和选择回执都写入 `output/phase12/`.",
        "",
        f"数据集 `{summary['dataset_id']}` / digest `{summary['dataset_digest']}`, "
        f"split `{summary['split_counts']}`, groups `{summary['group_counts']}`.",
        f"证据记录代码版本 `{summary['evidence_code_versions']}`; 修复重跑可能保留多个版本。",
        "",
        "## 方法结果",
        "",
        *rows,
        "",
        "失败, UNKNOWN, 未知 usage 和部分生成均保留在分母; 重复运行不是新增独立样本. "
        "Live 请求预留账本单独记录批次和终态.",
        "",
        "## Held-out 规则",
        "",
        f"按场景组 bootstrap 2,000 次: "
        f"`{json.dumps(summary['heldout_bootstrap'], ensure_ascii=True, sort_keys=True)}`. "
        "差值方向为 method_a 相对 method_b; 下界大于 0 才能称 method_a 有改善证据, "
        "跨 0 为 `INCONCLUSIVE`, 上界小于 0 表示 method_a 退化.",
        "",
        "## 训练与奖励",
        "",
        "训练 artifacts: "
        f"`{json.dumps(summary['training_artifacts'], ensure_ascii=True, sort_keys=True)}`. "
        "SFT, 标准 DPO 和 REINFORCE 均需真实参数更新及新进程读回; 它们仍是本地小模型实验.",
        "奖励黑客证据: "
        f"`{json.dumps(summary['reward_hacking'], ensure_ascii=True, sort_keys=True)}`.",
        "",
        "## Adoption 与回滚",
        "",
        "Prompt, Skill, SFT, DPO, RL 默认均为 `LAB_ONLY`; 没有净收益或证据不足时保留基线. "
        "选择前校验父版本和评测证据, 回滚写新 receipt 并验证父版本内容, 不覆盖旧 artifact.",
        "",
        "## Rubric (暂定)",
        "",
        f"`{json.dumps(scores, ensure_ascii=True, sort_keys=True)}`, "
        f"合计 `{summary['rubric_total']}/36`, 平均 `{summary['rubric_average']:.2f}`. "
        "最终分数需在全量证据和独立审查后确认.",
        "",
        "## 风险登记",
        "",
    ]
    risk_entries = risks if isinstance(risks, (list, tuple)) else ()
    lines.extend(
        f"- `{risk['risk']}`: L={risk['likelihood']} x I={risk['impact']}, {risk['status']}."
        for risk in risk_entries
        if isinstance(risk, dict)
    )
    current_limitation = (
        "真实模型、训练、全量 make 门禁和独立审查均以当前活动证据中的实际结果为准; "
        "缺失项由严格门禁列出, 不会用旧 replay 或报告排版替代."
    )
    limitation = (
        "当前报告不把 replay 当作真实模型质量, 不把本地训练当作业务语言模型微调; "
        "真实 assist Provider held-out 已记录一轮 24 条并保留失败和 UNKNOWN; 三次重复重跑因 "
        "Provider 长连接无响应而中断且未写入半批. 全量 make integration, 最终 Harness 同步授权 "
        "和独立对抗审查仍必须以实际退出码更新."
        if status == _DEFAULT_STAGE_STATUS
        else current_limitation
    )
    lines.extend(("", "## 限制与未运行项", "", limitation))
    return "\n".join(lines) + "\n"


def write_reports(
    root: Path,
    manifest: DatasetManifest,
    records: Iterable[ExperimentRecord],
    training_artifacts: Iterable[TrainingArtifact],
    *,
    code_version: str,
    status: str = _DEFAULT_STAGE_STATUS,
    stage_result: dict[str, object] | None = None,
) -> dict[str, str]:
    summary = build_summary(
        manifest,
        records,
        training_artifacts,
        code_version=code_version,
        status=status,
        stage_result=stage_result,
    )
    suffix = code_version.replace("/", "-")
    report_path = write_text_once(
        root,
        f"output/phase12/phase12-stage-report-draft-{suffix}.md",
        render_stage_report(summary),
    )
    adoption_path = write_text_once(
        root,
        f"output/phase12/phase12-adoption-card-{suffix}.md",
        render_adoption_card(summary),
    )
    summary_path = write_json_once(root, f"output/phase12/phase12-summary-{suffix}.json", summary)
    return {
        "report": str(report_path),
        "adoption_card": str(adoption_path),
        "summary": str(summary_path),
    }
