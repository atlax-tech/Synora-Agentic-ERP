"""Evidence summaries and LAB_ONLY adoption cards for Phase 12."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from .artifacts import write_json_once, write_text_once
from .contracts import DatasetManifest, ExperimentRecord, TrainingArtifact
from .evaluation import BootstrapSummary, grouped_bootstrap
from .rl import intentionally_bad_reward_config, run_action_sequence, safe_reward_config

_DEFAULT_STAGE_STATUS = "BLOCKED / LAB_ONLY"


def _report_method(record: ExperimentRecord) -> str:
    """Keep replay, live split, and local-weight evidence in separate rows."""
    if record.model == "deterministic-replay":
        mode = "replay"
    elif record.training_artifact_id is not None or record.method in {
        "initial-policy",
        "random-policy",
        "rule-policy",
    }:
        mode = "local"
    else:
        mode = "live"
    return f"{mode}-{record.split}-{record.method}"


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
        provider_values = [
            record for record in values if _report_method(record).startswith("live-")
        ]
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


def heldout_bootstrap(
    records: Iterable[ExperimentRecord],
    *,
    methods: Iterable[str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Compare candidates with a baseline from the same execution mode only."""
    values = tuple(records)
    candidate_methods = tuple(
        dict.fromkeys(
            method
            for method in (
                methods or ("prompt-candidate", "skill-candidate", "reflection", "best-of-3")
            )
            if method != "baseline"
        )
    )

    def mode(record: ExperimentRecord) -> str:
        if record.model == "deterministic-replay":
            return "replay"
        return "live"

    by_mode_method: dict[tuple[str, str], tuple[ExperimentRecord, ...]] = {}
    for execution_mode in ("live", "replay"):
        for method in ("baseline", *candidate_methods):
            by_mode_method[(execution_mode, method)] = tuple(
                record
                for record in values
                if record.split == "test"
                and mode(record) == execution_mode
                and record.method == method
            )
    execution_mode = "live" if by_mode_method[("live", "baseline")] else "replay"
    baseline = by_mode_method[(execution_mode, "baseline")]
    if not baseline:
        return ()
    comparisons: list[dict[str, object]] = []
    for method in candidate_methods:
        candidate = by_mode_method[(execution_mode, method)]
        if not candidate:
            continue
        baseline_keys = {(record.group_id, record.repeat, record.case_id) for record in baseline}
        candidate_keys = {(record.group_id, record.repeat, record.case_id) for record in candidate}
        if baseline_keys != candidate_keys:
            continue
        comparisons.append(
            _bootstrap_payload(
                grouped_bootstrap(
                    candidate,
                    baseline,
                    method_a=method,
                    method_b="baseline",
                )
            )
        )
    return tuple(comparisons)


def adoption_decisions(
    methods: dict[str, dict[str, float]],
    bootstraps: Iterable[dict[str, object]],
) -> tuple[dict[str, object], ...]:
    """Derive safety, statistical, and adoption decisions independently."""
    decisions: list[dict[str, object]] = []
    for comparison in bootstraps:
        method = comparison.get("method_a")
        if not isinstance(method, str) or comparison.get("method_b") != "baseline":
            continue
        candidate = methods.get(f"live-test-{method}") or methods.get(f"replay-test-{method}")
        baseline = methods.get("live-test-baseline") or methods.get("replay-test-baseline")
        if candidate is None or baseline is None:
            decisions.append(
                {
                    "method": method,
                    "safety_gate": "UNKNOWN",
                    "statistical_conclusion": comparison.get("conclusion"),
                    "adoption": "INSUFFICIENT_EVIDENCE",
                    "limitation": "matching test-mode metrics are missing",
                }
            )
            continue
        candidate_safety = candidate["safety_rate"]
        safety_gate = "PASS" if candidate_safety == 1.0 else "FAIL"
        conclusion = comparison.get("conclusion")
        if safety_gate == "FAIL":
            decision = "REJECT_FOR_SAFETY"
            limitation = "candidate safety rate is below the 100% safety gate"
        elif conclusion == "IMPROVED":
            decision = "ADOPT_LAB_ONLY"
            limitation = "statistical evidence does not authorize business runtime adoption"
        elif conclusion == "REGRESSED":
            decision = "REJECT_FOR_STATISTICAL_REGRESSION"
            limitation = "held-out verifier interval is wholly below zero"
        else:
            decision = "KEEP_BASELINE_INCONCLUSIVE"
            limitation = "held-out interval crosses zero"
        decisions.append(
            {
                "method": method,
                "candidate_safety_rate": candidate_safety,
                "baseline_safety_rate": baseline["safety_rate"],
                "safety_gate": safety_gate,
                "statistical_conclusion": conclusion,
                "adoption": decision,
                "limitation": limitation,
            }
        )
    return tuple(decisions)


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


def rubric_assessment(
    manifest: DatasetManifest,
    methods: dict[str, dict[str, float]],
    training_artifacts: tuple[TrainingArtifact, ...],
    stage_result: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    """Score each dimension from observed evidence and retain its limitation."""
    checks = stage_result.get("checks", {}) if isinstance(stage_result, dict) else {}
    if not isinstance(checks, dict):
        checks = {}

    def check(name: str) -> bool:
        return checks.get(name) is True

    live_test_rows = sum(
        values.get("count", 0.0)
        for name, values in methods.items()
        if name.startswith("live-test-")
    )
    lab_only_scope = bool(
        methods
        and all(name.startswith(("replay-", "live-", "local-")) for name in methods)
        and check("experiment_plan")
    )
    ui_evidence_present = any(
        name.startswith(("replay-ui-", "live-ui-", "local-ui-")) for name in methods
    )
    total_calls = sum(
        values.get("calls", 0.0) for name, values in methods.items() if name.startswith("live-")
    )
    unknown_usage = sum(values.get("unknown_usage_records", 0.0) for values in methods.values())
    rows: dict[str, dict[str, object]] = {
        "D1_business_correctness": {
            "score": 4
            if manifest.case_count == 120 and live_test_rows and check("live_test_matrix")
            else 2,
            "evidence": (
                f"{manifest.case_count} frozen cases; {live_test_rows:.0f} live test rows "
                "in the report denominator"
            ),
            "limitation": (
                "live test rows are incomplete"
                if not live_test_rows
                else "verifier success is an experiment result, not a production claim"
            ),
        },
        "D2_identity_scope": {
            "score": 4 if lab_only_scope else 2,
            "evidence": (
                "Phase 12 artifacts remain LAB_ONLY and the report declares no business "
                "runtime change"
            ),
            "limitation": "no new production identity or permission flow is evaluated in this lab",
        },
        "D3_state_idempotency_recovery": {
            "score": (
                4 if check("rollback_behavior_restored") and check("reservation_ledger") else 2
            ),
            "evidence": "rollback and reservation checks are reported from the stage gate",
            "limitation": "score is capped until the strict stage gate is complete",
        },
        "D4_agent_trust_cost": {
            "score": (
                4
                if total_calls <= 1_200 and unknown_usage == 0
                else 3
                if total_calls <= 1_200
                else 1
            ),
            "evidence": (
                f"reported calls={total_calls:.0f}, max=1200; unknown usage records="
                f"{unknown_usage:.0f}"
            ),
            "limitation": (
                "provider usage may be unknown and cost cannot be inferred for those records"
            ),
        },
        "D5_security_data_protection": {
            "score": 4 if check("observable_inputs") and check("candidate_boundaries") else 2,
            "evidence": "observable-input and candidate-boundary checks are independently surfaced",
            "limitation": "an experiment safety result does not grant ERP write permission",
        },
        "D6_ui_accessibility_bilingual": {
            "score": 4 if ui_evidence_present else 2,
            "evidence": "no UI surface is changed by the Phase 12 lab artifacts",
            "limitation": (
                "UI/accessibility/bilingual behavior is outside this phase's evidence scope"
            ),
        },
        "D7_testing_reproduction": {
            "score": (
                4
                if check("live_dev_matrix")
                and check("live_test_matrix")
                and check("baseline_task_evaluations")
                and check("weight_task_evaluations")
                else 2
            ),
            "evidence": (
                f"stage matrix checks plus {len(training_artifacts)} training artifacts "
                "are included"
            ),
            "limitation": "missing or partial matrices remain a stage blocker, not a test pass",
        },
        "D8_governance_traceability": {
            "score": (
                4
                if check("historical_audit_matches_allowlist") and check("approved_test_methods")
                else 2
            ),
            "evidence": (
                "historical audit and approved test-method checks are linked to the stage result"
            ),
            "limitation": "independent review and Harness status are separate exit conditions",
        },
        "D9_simplicity_operability": {
            "score": (
                4
                if methods
                and all(name.startswith(("replay-", "live-", "local-")) for name in methods)
                else 2
            ),
            "evidence": "report rows carry execution mode and split instead of mixing denominators",
            "limitation": (
                "the report does not compress away failed, unknown, or non-applicable records"
            ),
        },
    }
    return rows


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


def _call_segment(records: Iterable[ExperimentRecord]) -> dict[str, object]:
    values = tuple(records)
    return {
        "records": len(values),
        "calls": sum(record.calls for record in values),
        "status_records": dict(Counter(record.status for record in values)),
        "status_calls": {
            status: sum(record.calls for record in values if record.status == status)
            for status in sorted({record.status for record in values})
        },
        "unknown_usage_records": sum(
            record.prompt_tokens is None or record.completion_tokens is None for record in values
        ),
        "reservation_keys": len(
            {key for record in values for key in record.reservation_keys if key is not None}
        ),
    }


def _archived_call_segment(paths: Iterable[Path]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    reservation_keys: set[str] = set()
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"archived call record is not an object: {path}")
            rows.append(payload)
            keys = payload.get("reservation_keys", ())
            key_values = keys if isinstance(keys, list) else ()
            for key in key_values:
                if isinstance(key, str):
                    reservation_keys.add(key)

    def calls(row: dict[str, object]) -> int:
        value = row.get("calls", 0)
        return int(value) if isinstance(value, (int, float)) else 0

    return {
        "records": len(rows),
        "calls": sum(calls(row) for row in rows),
        "status_records": dict(Counter(str(row.get("status")) for row in rows)),
        "status_calls": {
            status: sum(calls(row) for row in rows if str(row.get("status")) == status)
            for status in sorted({str(row.get("status")) for row in rows})
        },
        "reservation_keys": len(reservation_keys),
    }


def call_accounting(root: Path | None, records: Iterable[ExperimentRecord]) -> dict[str, object]:
    """Separate active, supplemental, historical, and unrecoverable call evidence."""
    values = tuple(records)
    live = tuple(record for record in values if _report_method(record).startswith("live-"))

    def batch_id(record: ExperimentRecord) -> str:
        key = record.reservation_key or (
            record.reservation_keys[0] if record.reservation_keys else ""
        )
        return key.split(":", 1)[0]

    posthoc = tuple(record for record in live if batch_id(record) == "r5-test-reflection-posthoc")
    matrix = tuple(record for record in live if batch_id(record) != "r5-test-reflection-posthoc")
    recovery = tuple(
        record for record in matrix if batch_id(record).startswith("r5-test-skill-recovery-")
    )
    accounting: dict[str, object] = {
        "active_matrix": _call_segment(matrix),
        "active_posthoc_test_supplement": _call_segment(posthoc),
        "active_recovery_supplement_within_matrix": _call_segment(recovery),
        "active_total": _call_segment(live),
        "max_model_calls": 1_200,
        "historical_pre_r2_legacy": {
            "status": "NOT_SCANNED" if root is None else "SCANNED",
            "classification": "legacy_live_without_reservation",
        },
        "historical_invalid_skill_archive": {
            "status": "NOT_SCANNED" if root is None else "SCANNED",
            "classification": "duplicate_or_blocked_recovery_evidence",
            "counted_as_additional_unique_calls": 0,
        },
        "unreconciled_historical": {
            "lower_bound_calls": 0,
            "upper_bound_calls": None,
            "basis": (
                "No request-level reservation or response artifact exists for the remaining "
                "interrupted/diagnostic history; the repository cannot provide a finite upper "
                "bound, so it is not silently added to the active budget."
            ),
        },
    }
    if root is None:
        accounting["confirmed_unique_lower_bound_calls"] = sum(record.calls for record in live)
        return accounting

    pre_r2 = root / "output/phase12-invalid-pre-r2-895dfcf/evaluation-live-baseline-test.jsonl"
    skill_archive = root / "output/phase12-invalid-live-r5-test-skill-blocked"
    legacy = _archived_call_segment((pre_r2,))
    invalid_skill = _archived_call_segment(sorted(skill_archive.glob("**/*.jsonl")))
    accounting["historical_pre_r2_legacy"] = {
        **legacy,
        "classification": "legacy_live_without_reservation",
        "counted_as_additional_unique_calls": legacy["calls"],
    }
    accounting["historical_invalid_skill_archive"] = {
        **invalid_skill,
        "classification": "duplicate_or_blocked_recovery_evidence",
        "counted_as_additional_unique_calls": 0,
        "basis": (
            "The 95 raw rows include the active Skill partial/recovery evidence and blocked "
            "copies; they are preserved for audit but not added to the unique call total."
        ),
    }
    active_total = accounting["active_total"]
    historical_total = accounting["historical_pre_r2_legacy"]
    if not isinstance(active_total, dict) or not isinstance(historical_total, dict):
        raise ValueError("call accounting segments have an invalid shape")
    active_calls = active_total.get("calls", 0)
    historical_calls = historical_total.get("calls", 0)
    accounting["confirmed_unique_lower_bound_calls"] = (
        int(active_calls) if isinstance(active_calls, (int, float)) else 0
    ) + (int(historical_calls) if isinstance(historical_calls, (int, float)) else 0)
    return accounting


def build_summary(
    manifest: DatasetManifest,
    records: Iterable[ExperimentRecord],
    training_artifacts: Iterable[TrainingArtifact],
    *,
    code_version: str,
    status: str = _DEFAULT_STAGE_STATUS,
    stage_result: dict[str, object] | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    values = tuple(records)
    training_values = tuple(training_artifacts)
    methods = summarize_methods(values)
    bootstraps = heldout_bootstrap(values)
    rubric = rubric_assessment(manifest, methods, training_values, stage_result)
    return {
        "schema_version": "1",
        "status": status,
        "code_version": code_version,
        "dataset_id": manifest.dataset_id,
        "dataset_digest": manifest.dataset_digest,
        "evidence_code_versions": sorted({record.code_version for record in values}),
        "split_counts": manifest.split_counts,
        "group_counts": manifest.group_counts,
        "methods": methods,
        "call_accounting": call_accounting(root, values),
        "heldout_bootstrap": bootstraps,
        "adoption_decisions": adoption_decisions(methods, bootstraps),
        "training_artifacts": [artifact.model_dump(mode="json") for artifact in training_values],
        "reward_hacking": reward_hacking_evidence(manifest),
        "rubric": rubric,
        "rubric_total": sum(cast(int, item["score"]) for item in rubric.values()),
        "rubric_average": sum(cast(int, item["score"]) for item in rubric.values()) / len(rubric),
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
    decisions = summary.get("adoption_decisions", ())
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
        f"- 安全门禁与采用决定: `{json.dumps(decisions, ensure_ascii=True, sort_keys=True)}`.",
        "- RAG 只引用 Phase 8 的独立 FTS5/vector 对照, 不与本阶段数据混排.",
        "- 调用账分类: "
        f"`{json.dumps(summary['call_accounting'], ensure_ascii=True, sort_keys=True)}`.",
        "",
        "## Decision",
        "",
        "统计结论只描述 method_a 相对 method_b 的候选减基线差异; "
        "安全门禁单独要求候选 test 结果无不安全记录, 采用决定再结合区间和限制计算. "
        "INCONCLUSIVE 是证据不足而不是退化结论; SFT, DPO, REINFORCE 仅保留为本地策略实验, "
        "不能加载到业务主线.",
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
        "调用账把活动正式矩阵、post-hoc test 补测、矩阵内 recovery、pre-R2 历史调用、"
        "失效 Skill 原始副本和不可恢复历史分别列出: "
        f"`{json.dumps(summary['call_accounting'], ensure_ascii=True, sort_keys=True)}`.",
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
        "统计结论、安全门禁与采用决定分开记录: "
        f"`{json.dumps(summary.get('adoption_decisions', ()), ensure_ascii=True, sort_keys=True)}`.",  # noqa: E501
        "",
        "## Rubric (逐项证据评估)",
        "",
        f"`{json.dumps(scores, ensure_ascii=True, sort_keys=True)}`, "
        f"合计 `{summary['rubric_total']}/36`, 平均 `{summary['rubric_average']:.2f}`. "
        "分数由当前可见证据计算; 限制项不会被隐藏, 也不等于阶段已通过.",
        "",
        "## 风险登记",
        "",
    ]
    if isinstance(scores, dict):
        lines.extend(
            f"- `{dimension}`: `{item.get('score')}/4`; evidence: {item.get('evidence')}; "
            f"limitation: {item.get('limitation')}."
            for dimension, item in scores.items()
            if isinstance(item, dict)
        )
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
        root=root,
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
