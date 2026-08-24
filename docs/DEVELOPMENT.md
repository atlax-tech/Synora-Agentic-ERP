# Development

Status: `CONFIRMED` engineering policy; implementation commands are `UNRESOLVED`.

## Change Protocol

Before implementation:

1. Identify the product requirement and acceptance condition.
2. Read only relevant product, architecture, design, specification, decision, and domain evidence.
3. Locate relevant Frappe/ERPNext source and tests when behavior depends on upstream.
4. Classify claims as `CONFIRMED`, `INFERRED`, `UNRESOLVED`, or `CONFLICTED`.
5. State the intended file perimeter, risks, failure paths, and verification plan.
6. For non-trivial work, maintain an execution plan and Context Receipt.

During implementation:

- Prefer the smallest complete solution that preserves validation, security, accessibility, observability, and failure handling.
- Do not modify upstream Frappe/ERPNext.
- Do not delete or demote approved requirements to simplify a milestone; record staged implementation explicitly.
- Keep changes small and commit one coherent, verified increment at a time.
- Record every change in `docs/development-log/` in clear Chinese, explaining what changed, why, validation evidence, limitations, and manual acceptance.

Before a release or product/dependency version update:

- Run required automated and manual checks.
- Use an independent adversarial sub-agent that receives the requirement, diff, tests, and runtime evidence rather than the builder's explanation.
- Record `PASS`, `CHANGES_REQUIRED`, or `BLOCKED` with evidence.

## Evidence Rules

- Source tests and runtime behavior outrank summaries for ERP behavior.
- Do not report a configured command, client, platform, or integration as verified unless it actually ran.
- Benchmark and resume claims require reproducible inputs and raw results.

## Verified Commands

```bash
python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .
```

### Phase 1 environment commands (verified 2026-08-24, exit 0)

Candidate environment is a Docker Bench inside `env/dev/` (see `docs/decisions/ADR-0001-docker-bench-environment.md`); the frozen baseline pair is recorded in `docs/decisions/ADR-0002-frozen-baseline-pair.md`.

```bash
bash env/dev/scripts/dev/env.sh up          # 启动依赖服务并等待健康
bash env/dev/scripts/dev/env.sh bootstrap   # 空卷重建候选环境（含双 SHA 断言；P1.1 证据）
bash env/dev/scripts/dev/env.sh seed        # 确定性主数据（期望 SEED-OK）
bash env/dev/scripts/dev/env.sh p2p-users   # 命名测试用户（期望 P2P-USERS-OK）
bash env/dev/scripts/dev/env.sh p2p-run     # 人工 P2P 基线（期望 P2P-RUN-OK）
bash env/dev/scripts/dev/env.sh bash \
  "cd /home/frappe/bench && git -C apps/frappe rev-parse HEAD && git -C apps/erpnext rev-parse HEAD && git -C apps/frappe status --porcelain && git -C apps/erpnext status --porcelain"
# 期望：两 SHA 与 ADR-0002 一致，两仓无输出
```

这些是 Phase 1 环境命令，不是产品命令。

> **UNRESOLVED:** What are the product format, lint, type-check, unit-test, integration-test, and runtime commands?
>
> Impact: agents cannot prove implementation completion until the code and dependency manifests exist.
> Required evidence: executable project configuration and successful command output.

## Sources

- `docs/PRD.md` — approved maintainability, testing, acceptance, and unresolved-decision requirements.
- `docs/ARCHITECTURE.md` — implementation boundaries and open architecture decisions.
