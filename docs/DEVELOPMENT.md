# Development

Status: `CONFIRMED` engineering policy; P2.1 implementation commands are `CONFIRMED` (verified 2026-08-24). Phase 9 implementation, evidence, and independent adversarial review are `PASS` as of 2026-09-04; final phase status remains pending the explicitly approved README/Harness synchronization.

## Change Protocol

Before implementation:

1. Identify the product requirement and acceptance condition.
2. Read only relevant product, architecture, design, specification, decision, and domain evidence.
3. Locate relevant Frappe/ERPNext source and tests when behavior depends on upstream.
4. Classify claims as `CONFIRMED`, `INFERRED`, `UNRESOLVED`, or `CONFLICTED`.
5. State the intended file perimeter, risks, failure paths, and verification plan.
6. For non-trivial work, maintain an execution plan and Context Receipt.
7. Create a bounded Agent-development Assignment before each phase step: explain the business reason, code entry, inputs/outputs, acceptance, tests, boundaries, hint ladder, expected time, and interview questions; record whether the user attempted it.

During implementation:

- Prefer the smallest complete solution that preserves validation, security, accessibility, observability, and failure handling.
- Do not modify upstream Frappe/ERPNext.
- Do not delete or demote approved requirements to simplify a milestone; record staged implementation explicitly.
- Keep changes small and commit one coherent, verified increment at a time.
- Work as a mentor: explain why each operation is necessary, let the user attempt safe work first, and take over only for an explicit request, a capability boundary, a security gate, or a production defect; record the reason when taking over.
- Record every change in `docs/development-log/` in clear Chinese, explaining what changed, why, validation evidence, limitations, and manual acceptance. Preserve each user question, doubt, or blocker verbatim, followed by evidence, explanation, conclusion, and a review action.

For Phase 4–13 Agent-learning work:

- Keep the real ERP business application layer and `labs/agent_patterns/` teaching layer in the same repository and development line; do not create long-lived branches or a second repository for the lab.
- Labs reuse public typed contracts, fixed datasets, and declared test doubles. They must not receive production credentials, import hidden ERP internals, bypass the Control Plane, or be reported as deployed business behavior.
- Learn each topic in this order: principle -> minimal lab -> open-source source comparison -> Synora business adaptation -> tests and trace -> trade-off review -> interview questions.
- Before moving a technique into the business path, write an Adoption Card covering `Problem`, `Preconditions`, `Minimal Lab`, `Alternatives`, `Evidence`, `Decision`, `Real-world Use`, and `Interview Answer`. A rejected technique keeps its runnable learning evidence.
- Do not let labs become unrelated demo collections: every lab uses an ERP/procurement task or explicitly explains why the technique cannot be meaningfully mapped to this domain.

Before a phase exit, release, or product/dependency version update:

- Run required automated and manual checks.
- Score the 9 phase-exit dimensions and record likelihood/impact plus P0–P3 disposition; no P0/P1 may remain open, and deferred P2 items need an owner, next gate, and re-test condition.
- Use an independent adversarial sub-agent after the final diff and full evidence are ready; it receives the requirement, diff, tests, runtime evidence, and report draft rather than relying on the builder's explanation.
- Record `PASS`, `CHANGES_REQUIRED`, or `BLOCKED` with evidence. Only final `PASS` permits the phase report; `CHANGES_REQUIRED` returns to fix/retest, and `BLOCKED` stops progression.
- Deliver at least five phase-specific project/interview questions; let the user answer first and mark unanswered items `待练习`.

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

## Model Provider 配置与 API Key 脱敏（P3.4 BYOK，2026-08-25 批准）

模型 Provider 采用 BYOK：**Base URL 由用户提供、API Key 由用户自行填写**，代码不持有明文。

### 配置方式

1. 复制 `env/dev/.env.example` 为 `env/dev/.env`（已被 gitignore，不会进入 Git）；
2. 在 `.env` 中按角色成组填写（真实值只存在于本机 `.env`）：
   - 主模型：`OLLAMA_BASE_URL`、`OLLAMA_API_KEY`、`OLLAMA_MODEL=qwen3:8b`；
   - 辅助模型：`ASSIST_BASE_URL`、`ASSIST_API_KEY`、`ASSIST_MODEL=glm-5.3-flash`；
   - 收费备用：`BACKUP_BASE_URL`、`BACKUP_API_KEY`、`BACKUP_MODEL=grok-4.5`；
   - 本地最后备用：`BACKUP_OLLAMA_BASE_URL`、`BACKUP_OLLAMA_API_KEY`、`BACKUP_OLLAMA_MODEL=qwen3.8:27b`；
   - 如需显式远端代理，再设置 `SYNORA_MODEL_PROXY`。代码固定 `trust_env=False`，不会读取系统代理。
3. Runtime 通过 `agent_runtime.providers.provider_from_environment()` 读取四个命名角色并构造有界回退链；旧 `SYNORA_PROVIDER_*` 配置不再支持。

### 代码中的脱敏保证

- **入口唯一**：Key 只从环境变量进入，`SecretStr` 保存；不写进代码、Git、日志、数据库或证据文档；
- **输出面**：`SecretStr` 的 repr/str 一律显示 `**********`；异常消息、HTTP 错误、测试断言均不含明文（有测试 `test_secret_never_appears_in_error` 守护）；
- **传输面**：`trust_env=False` 防止环境代理改写目标地址；base_url 必须是纯 origin，禁止 userinfo/query/fragment，防 Key 被拼进 URL；
- **未配置即失败**：任一命名角色槽位缺失或模型名不匹配时 `provider_from_environment()` 抛 `INVALID_CONFIGURATION`，不猜测默认地址或旧模型；
- **使用后即弃**：构造出的 provider 只存在进程内，不持久化。

### 验证

```bash
uv run --python 3.14 pytest services/agent_runtime/tests/test_providers.py -v
# 当前基线：67 passed（含 base_url 校验、命名角色路由、有限 deadline、fail-closed、secret 防泄漏用例）
```

### 连通性测试（填好 .env 后）

```bash
# 每次只检查指定角色一次，不遍历回退链：
uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py --env env/dev/.env --role primary
# 也可指定 --role assist|backup|last_local；成功必须有 content_present=YES。
# PROVIDER-FAIL / PROVIDER-CONFIG-FAIL = 失败原因; 任何输出都不包含 API Key
```

> 注意：不要用 `source env/dev/.env && uv run ...` —— shell `source` 只设置局部变量、不导出，
> `uv run` 的子进程看不到；用上面的 `--env` 参数即可。若环境变量已 export，可不带 `--env`。

### Phase 3 P3.5 real HTTP verification (verified 2026-08-25, exit 0)

This check exercises the read-only path `Buyer → Frappe → Runtime → BYOK →
Run Plan evidence → get_run`. It does not create ERP business documents. The
runtime must be reachable from the Bench container; when using the Docker host
gateway, set an ephemeral local token in both processes and do not commit or
print it:

```bash
export SYNORA_RUNTIME_URL=http://host.docker.internal:8001
export SYNORA_RUNTIME_ALLOW_HOST_GATEWAY=1
export SYNORA_RUNTIME_TOKEN=<ephemeral-local-token>
bash env/dev/scripts/dev/env.sh start

# In a separate host terminal, use the same token and the local .env key.
set -a; . env/dev/.env; set +a
SYNORA_RUNTIME_TOKEN=<ephemeral-local-token> \
UV_CACHE_DIR=/private/tmp/synora-uv-cache \
uv run --python 3.14 uvicorn agent_runtime.app:app --host 127.0.0.1 --port 8001

SYNORA_P2P_USER_PWD="$SYNORA_P2P_USER_PWD" \
  uv run --python 3.14 python env/dev/p35/p35_e2e.py
```

Observed evidence for the 2026-08-25 run was `P35-HTTP-OK` with
`provider=grok-4.5`, `run_state=SUCCEEDED`, and `status=fallback_error`: the
real provider request was made, but the returned output exceeded the combined
completion/reasoning-token budget and was safely replaced by the deterministic
summary. This is a successful fail-closed chain, not a claim that model prose
was accepted.

## Phase 8 Coach closure evidence (2026-09-03)

The current Coach route is frozen to the four named roles above. It uses a
request-scoped iterator, tries each candidate at most once, and only escalates
on malformed/strict `UNKNOWN` or transient unavailability. Authentication,
configuration, budget, context, and safety refusals remain fail-closed. The
last local `qwen3.8:27b` role and the Frappe-to-Runtime Coach call use a fixed
900-second deadline: this is deliberately long enough for the slow local model
while still preventing a permanently hung socket or Run. It is not an
invitation to proactively call paid fallbacks.

The final real evidence is bound to source HEAD
`562fc42671004e12c3f3b6ee9266d0385e03b04a`:

- representative `G1`: `PASS`, HTTP 200 `ANSWERED`, non-empty server-rebuilt
  answer, `ERP_FACT` and `LIVE_ERP` citation, `tools=[]`, positive prompt
  usage, unchanged ERP anchors, and no Secret;
- fixed order `G1,G2,G3,G4,C1,C2,C3,S1,S2,S3,U1,U2`: `12/12 PASS`, grounding
  `4/4`, citation `3/3` (positive `2/2`, safe refusal `1/1`), refusal/security
  `3/3`, usefulness `2/2`; each case ran once and the ten eligible cases have
  real usage;
- `mock_substitution=false`, `erp_business_zero_write=true`,
  `provider_tools_empty=true`, `repo_status_unchanged=true`,
  `secret_leak=false`, `selective_rerun=false`;
- immutable files are
  `output/phase8/phase8-manifest-562fc42.json` (SHA-256
  `1c3e88b23aa410afb2eb70f21de1f67df518a3071de58ae1e4a014246a884e39`),
  `output/phase8/phase8-representative-562fc42.json`, and
  `output/phase8/phase8-real-coach-acceptance-562fc42.json`; the sanitized
  role artifact is
  `output/playwright/phase8-role-acceptance-562fc42.json`, explicitly bound to
  the current backend evidence while retaining its historical browser-capture
  HEAD rather than claiming a fresh screenshot.

The GLM/Grok connectivity diagnosis is recorded separately in
`output/phase8/provider-connectivity-20260902.json`. GLM's billed HTTP 200
request once returned an empty final content envelope; the same configured
role then returned `OK` with `reasoning_effort=low`. Grok's initial 403 came
from the old gateway mapping; the corrected `cf.api.fan/v1` endpoint was
verified with the Grok Responses protocol (HTTP 200) and its key/model were
therefore not re-tested during final Coach acceptance.

## Phase 9 closure evidence (2026-09-04)

Implementation HEAD is `8b7ff1b1dc51449b51f0335ed63ae2c34bc5772e`; the evidence/report freeze is committed at `a87f254c2339ee9253d4c0802b38e4b9dcfb7103`. GLM v12 is the adopted Planner/Reviewer quality-first A/B (`ADOPT` for both); token totals are recorded but not a veto, and qwen3.8:27b was not called. GLM v13’s later stochastic failure and the qwen/Grok failures remain immutable evidence.

P9.6–P9.8 formal MCP stdio, real `127.0.0.1` TCP A2A, and fixed ANP acceptance passed. P9.9 real Buyer/Viewer/System Manager acceptance passed with genuine GLM calls, controlled recovery, three bound screenshots, unchanged ERP anchors, and zero ERP business writes. P9.10 format, lint, type, unit, integration, focused, Python 3.14, upstream, ponytail, and Harness read-only checks are recorded in `output/phase9/phase9-final-manifest-8b7ff1b.json`; the expected pre-sync Harness drift remains explicit. The sole final independent adversarial review returned `PASS`.

The stage report draft is `output/phase9/phase9-stage-report-draft-8b7ff1b.md` with status `PENDING INDEPENDENT REVIEW` retained until the review result is incorporated in the final status. README and `.harness/` remain unchanged pending the required file-level proposal and user approval.

## Sources

- `docs/项目方向纠偏.md` — approved learning workflow, same-repository layer boundary, and Adoption Card contract.
- `docs/PRD.md` — approved maintainability, testing, acceptance, and unresolved-decision requirements.
- `docs/ARCHITECTURE.md` — implementation boundaries and open architecture decisions.
