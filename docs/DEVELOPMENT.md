# Development

Status: `CONFIRMED` engineering policy; P2.1 implementation commands are `CONFIRMED` (verified 2026-08-24).

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

### P2.1 project commands (verified 2026-08-24)

The root `Makefile` is the executable source of truth for the P2.1 project
checks. It selects Python 3.14, and the repository requires `>=3.14,<3.15`.

```bash
make setup
make format
make format-check
make lint
make type
make unit
make integration
make runtime
```

Observed results for the 2026-08-24 P2.1 evidence run:

- `make setup` exited 0; the frozen workspace resolved 30 packages and synced
  successfully.
- `make format` exited 0 with 52 files unchanged; `make format-check` exited
  0 with 52 files already formatted.
- `make lint` exited 0 with `All checks passed!`.
- `make type` exited 0 with mypy reporting no issues in 5 source files.
- `make unit` exited 0 with 4 tests passed.
- `make integration` exited 0; the Bench Frappe test reported `Ran 1 test ...
  OK` and the site listed `synora_agentic_erp` as installed.

`make runtime` is a long-running foreground command. Keep it running and, in
another terminal, verify the health contract:

```bash
curl --fail http://127.0.0.1:8001/healthz
```

The expected response is
`{"service":"synora-agent-runtime","status":"ok"}`. Stop the foreground
server with `Ctrl-C` after the check; its termination status is not the health
gate. The runtime exposes no additional HTTP documentation route (`/docs`
returns 404).

The full evidence, limitations, and repeatable manual acceptance steps are in
`docs/development-log/20260825-Phase-2-开发日志.md` and are
anchored to commit `e10a4dd`.

### Phase 2 P2.6 real HTTP verification (verified 2026-08-25, exit 0)

Requires the bench web server listening on `127.0.0.1:8000` (see `env.sh up` +
`start`) and `SYNORA_P2P_USER_PWD` set (test-user password, same as P2P users).

```bash
# 1) prepare boundary data inside bench console (idempotent; expect P26-DATA-OK)
#    - docker cp env/dev/p26/p26_data.py into bench, then run via bench console
# 2) host-side end-to-end (13 scenarios; expect 13x P26-*-OK and P26-E2E-OK):
SYNORA_P2P_USER_PWD=<pwd> uv run --python 3.14 python env/dev/p26/p26_e2e.py
```

Observed evidence for the 2026-08-25 run: `P26-E2E-OK` with all 13 scenarios
(BASIC, PERMISSION_DENIED, SCOPE_DENIED, PAGINATION_CLIENT/SERVER, TIMEOUT,
DISABLED_SUPPLIER, CROSS_COMPANY, CANCELLED_MR, MISSING_FIELD,
UNSUPPORTED_VERSION, AONLY_COMPANY_A_ACCESS, AONLY_COMPANY_B_DENIED). Full
evidence is in
`docs/development-log/20260825-Phase-2-开发日志.md`,
anchored to commit `733da89`.

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

## Sources

- `docs/PRD.md` — approved maintainability, testing, acceptance, and unresolved-decision requirements.
- `docs/ARCHITECTURE.md` — implementation boundaries and open architecture decisions.
