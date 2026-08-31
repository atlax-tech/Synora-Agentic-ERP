"""BYOK 连通性自检: 向已配置的 model provider 发一个最小请求并打印脱敏结构。

用法:
    uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py
    # 若环境变量未设置, 可用 --env 让脚本自行加载 env 文件:
    uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py --env env/dev/.env

--env 只把文件中的 SYNORA_PROVIDER_* 项注入脚本进程环境 (不覆盖已设置的
环境变量, 不打印明文), 供自检使用; 生产代码仍只从环境变量读取。

退出码 0 = 连通成功; 非 0 = 失败。任何输出都不包含 API Key。
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "services/agent_runtime/src")

from agent_runtime.providers import (
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URL_ENV,
    PROVIDER_MAX_OUTPUT_TOKENS_ENV,
    PROVIDER_MODEL_ENV,
    PROVIDER_PROXY_ENV,
    PROVIDER_REASONING_EFFORT_ENV,
    PROVIDER_THINKING_ENV,
    ProviderError,
    ProviderMessage,
    provider_from_environment,
    provider_max_output_tokens,
    provider_thinking_mode,
)

_PROVIDER_ENV_NAMES = (
    PROVIDER_BASE_URL_ENV,
    PROVIDER_API_KEY_ENV,
    PROVIDER_MODEL_ENV,
    PROVIDER_REASONING_EFFORT_ENV,
    PROVIDER_THINKING_ENV,
    PROVIDER_PROXY_ENV,
    PROVIDER_MAX_OUTPUT_TOKENS_ENV,
)


def _load_env_file(path: str) -> None:
    """从 env 文件加载 SYNORA_PROVIDER_* 项 (不覆盖已设置项, 不打印值)。"""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key in _PROVIDER_ENV_NAMES:
            os.environ.setdefault(key, value.strip())


async def main() -> None:
    parser = argparse.ArgumentParser(description="Synora BYOK provider self-check")
    parser.add_argument("--env", help="load SYNORA_PROVIDER_* from this file (e.g. env/dev/.env)")
    args = parser.parse_args()
    if args.env:
        _load_env_file(args.env)
    try:
        provider = provider_from_environment()
        max_output_tokens = provider_max_output_tokens()
        thinking = provider_thinking_mode()
        response = await provider.complete(
            [ProviderMessage(role="user", content="ping")],
            max_tokens=max_output_tokens,
        )
    except ProviderError as error:
        print(
            "PROVIDER-FAIL:",
            f"code={error.failure_code}",
            f"prompt={error.prompt_tokens}",
            f"completion={error.completion_tokens}",
            f"reasoning={error.reasoning_tokens}",
        )
        raise SystemExit(1) from error
    except ValueError as error:
        print(f"PROVIDER-CONFIG-FAIL: {error}")
        raise SystemExit(1) from error
    model = " ".join(os.environ.get(PROVIDER_MODEL_ENV, "").split())[:120] or "<unset>"
    print(
        "PROVIDER-OK:",
        f"model={model}",
        f"max_output_tokens={max_output_tokens}",
        f"thinking={thinking or 'disabled'}",
        f"content_present={'YES' if response.text else 'NO'}",
        f"reasoning_content_present={'YES' if response.reasoning_content_present else 'NO'}",
        f"tokens_in={response.prompt_tokens}",
        f"tokens_out={response.completion_tokens}",
        f"reasoning_tokens={response.reasoning_tokens}",
    )


if __name__ == "__main__":
    asyncio.run(main())
