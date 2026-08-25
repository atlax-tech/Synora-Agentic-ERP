"""P3.5 模型增强自检: 用真实/确定性 provider 把确定性计划增强为自然语言解释。

用法:
    uv run --python 3.14 python services/agent_runtime/scripts/enhance_plan.py \
        --env env/dev/.env --plan services/agent_runtime/scripts/example_plan.json

--env 可选: 不传则用已设置的环境变量 (未配置时提示)。输出包含解释与证据
(token/耗时/状态), 任何输出都不包含 API Key。成本护栏 max_tokens=256。
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "services/agent_runtime/src")

from agent_runtime.agent.enhance import enhance_plan
from agent_runtime.providers import (
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URL_ENV,
    PROVIDER_MODEL_ENV,
    provider_from_environment,
)

_PROVIDER_ENV_NAMES = (PROVIDER_BASE_URL_ENV, PROVIDER_API_KEY_ENV, PROVIDER_MODEL_ENV)


def _load_env_file(path: str) -> None:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key in _PROVIDER_ENV_NAMES:
            os.environ.setdefault(key, value.strip())


async def main() -> None:
    parser = argparse.ArgumentParser(description="Synora plan enhancement self-check")
    parser.add_argument("--env", help="load SYNORA_PROVIDER_* from this file")
    parser.add_argument(
        "--plan", required=True, help="path to plan JSON (Synora Run Plan.plan_json)"
    )
    args = parser.parse_args()
    if args.env:
        _load_env_file(args.env)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))

    provider = provider_from_environment()
    explanation, evidence = await enhance_plan(plan, provider, provider_name="real-byok")
    print("EXPLANATION:", explanation[:200])
    print(
        f"EVIDENCE: provider={evidence.provider} status={evidence.status} "
        f"tokens=in:{evidence.prompt_tokens} out:{evidence.completion_tokens} "
        f"elapsed_ms={evidence.elapsed_ms} fallback={evidence.fallback_reason}"
    )


if __name__ == "__main__":
    asyncio.run(main())
