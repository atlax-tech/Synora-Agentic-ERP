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
7. Create a bounded Agent-development Assignment before each phase step: explain the business reason, code entry, inputs/outputs, acceptance, tests, boundaries, hint ladder, expected time, and interview questions; record whether the user attempted it.

During implementation:

- Prefer the smallest complete solution that preserves validation, security, accessibility, observability, and failure handling.
- Do not modify upstream Frappe/ERPNext.
- Do not delete or demote approved requirements to simplify a milestone; record staged implementation explicitly.
- Keep changes small and commit one coherent, verified increment at a time.
- Work as a mentor: explain why each operation is necessary, let the user attempt safe work first, and take over only for an explicit request, a capability boundary, a security gate, or a production defect; record the reason when taking over.
- Record every change in `docs/development-log/` in clear Chinese, explaining what changed, why, validation evidence, limitations, and manual acceptance. Preserve each user question, doubt, or blocker verbatim, followed by evidence, explanation, conclusion, and a review action.

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
2. 在 `.env` 中填写三项（真实值只存在于本机 `.env`）：
   - `SYNORA_PROVIDER_BASE_URL`：OpenAI 兼容 API 根地址，**通常带 `/v1`**，如 `https://api.example.com/v1`、`https://api.x.ai/v1`（纯域名不带路径段也会被拒绝，如 `https://api.example.com`）；
   - `SYNORA_PROVIDER_API_KEY`：你的 API Key；
   - `SYNORA_PROVIDER_MODEL`：模型 ID，如 `gpt-4o`、`grok-4.5`；
3. Runtime 通过 `agent_runtime.providers.provider_from_environment()` 读取并构造 provider。

### 代码中的脱敏保证

- **入口唯一**：Key 只从环境变量进入，`SecretStr` 保存；不写进代码、Git、日志、数据库或证据文档；
- **输出面**：`SecretStr` 的 repr/str 一律显示 `**********`；异常消息、HTTP 错误、测试断言均不含明文（有测试 `test_secret_never_appears_in_error` 守护）；
- **传输面**：`trust_env=False` 防止环境代理改写目标地址；base_url 必须是纯 origin，禁止 userinfo/query/fragment，防 Key 被拼进 URL；
- **未配置即失败**：`SYNORA_PROVIDER_BASE_URL` 未设置时 `provider_from_environment()` 抛错（fail closed），不猜测默认地址；
- **使用后即弃**：构造出的 provider 只存在进程内，不持久化。

### 验证

```bash
uv run --python 3.14 pytest services/agent_runtime/tests/test_providers.py -v
# 期望：18 passed（含 base_url 校验、fail-closed、secret 防泄漏用例）
```

### 连通性测试（填好 .env 后）

```bash
# 一行命令 (脚本自动加载 env/dev/.env 中的 SYNORA_PROVIDER_* 项, 不覆盖已设置的环境变量):
uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py --env env/dev/.env
# PROVIDER-OK: <响应文本前 80 字符> | tokens: in=X out=Y = 链接生效
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
SYNORA_PROVIDER_REASONING_EFFORT=low \
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

## Sources

- `docs/PRD.md` — approved maintainability, testing, acceptance, and unresolved-decision requirements.
- `docs/ARCHITECTURE.md` — implementation boundaries and open architecture decisions.
