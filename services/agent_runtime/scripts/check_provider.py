"""BYOK 连通性自检: 向一个指定角色发一个最小请求并打印脱敏结构。

用法:
    uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py --role primary
    uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py --role assist
    uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py --role backup
    # 若环境变量未设置, 可用 --env 让脚本自行加载 env 文件:
    uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py \
        --env env/dev/.env --role assist

--env 只把文件中的 provider 配置项注入脚本进程环境 (不覆盖已设置的
环境变量, 不打印明文), 供自检使用; 生产代码仍只从环境变量读取。

每次只构造并请求指定角色一次; 自检不会遍历 FailoverProvider, 不会因为
该角色的协议/响应问题额外调用 GLM、Grok 或其他付费模型。

退出码 0 = 连通成功; 非 0 = 失败。任何输出都不包含 API Key。
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "services/agent_runtime/src")

from agent_runtime.providers import (
    ASSIST_API_KEY_ENV,
    ASSIST_BASE_URL_ENV,
    ASSIST_MODEL_ENV,
    BACKUP_API_KEY_ENV,
    BACKUP_BASE_URL_ENV,
    BACKUP_MODEL_ENV,
    BACKUP_OLLAMA_API_KEY_ENV,
    BACKUP_OLLAMA_BASE_URL_ENV,
    BACKUP_OLLAMA_MODEL_ENV,
    OLLAMA_API_KEY_ENV,
    OLLAMA_BASE_URL_ENV,
    OLLAMA_MODEL_ENV,
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URL_ENV,
    PROVIDER_MAX_OUTPUT_TOKENS_ENV,
    PROVIDER_MODEL_ENV,
    PROVIDER_PROXY_ENV,
    PROVIDER_REASONING_EFFORT_ENV,
    PROVIDER_THINKING_ENV,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderMessage,
    ProviderRole,
    provider_for_role,
)

_PROVIDER_ENV_NAMES = (
    OLLAMA_BASE_URL_ENV,
    OLLAMA_API_KEY_ENV,
    OLLAMA_MODEL_ENV,
    ASSIST_BASE_URL_ENV,
    ASSIST_API_KEY_ENV,
    ASSIST_MODEL_ENV,
    BACKUP_BASE_URL_ENV,
    BACKUP_API_KEY_ENV,
    BACKUP_MODEL_ENV,
    BACKUP_OLLAMA_BASE_URL_ENV,
    BACKUP_OLLAMA_API_KEY_ENV,
    BACKUP_OLLAMA_MODEL_ENV,
    PROVIDER_BASE_URL_ENV,
    PROVIDER_API_KEY_ENV,
    PROVIDER_MODEL_ENV,
    PROVIDER_REASONING_EFFORT_ENV,
    PROVIDER_THINKING_ENV,
    PROVIDER_PROXY_ENV,
    PROVIDER_MAX_OUTPUT_TOKENS_ENV,
)


def _load_env_file(path: str) -> None:
    """从 env 文件加载已知 provider 项 (不覆盖已设置项, 不打印值)。"""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key in _PROVIDER_ENV_NAMES:
            os.environ.setdefault(key, value.strip())


async def main() -> None:
    parser = argparse.ArgumentParser(description="Synora provider self-check")
    parser.add_argument("--env", help="load provider settings from this file (e.g. env/dev/.env)")
    parser.add_argument(
        "--role",
        choices=("primary", "assist", "backup", "last_local"),
        default="primary",
        help="check exactly this named provider role (no fallback)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=32,
        help="bounded output cap for this minimal request (default: 32)",
    )
    args = parser.parse_args()
    if args.env:
        _load_env_file(args.env)
    provider: OpenAICompatibleProvider | None = None
    try:
        role: ProviderRole = args.role
        provider = provider_for_role(role)
        response = await provider.complete(
            [ProviderMessage(role="user", content="ping")],
            tools=[],
            max_tokens=args.max_tokens,
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
    finally:
        if provider is not None:
            await provider.aclose()
    assert provider is not None
    model = " ".join(provider._model.split())[:120] or "<unset>"
    print(
        "PROVIDER-OK:",
        f"role={args.role}",
        f"model={model}",
        f"wire_api={provider._wire_api}",
        f"max_output_tokens={args.max_tokens}",
        f"reasoning_effort={provider._reasoning_effort or 'none'}",
        f"content_present={'YES' if response.text else 'NO'}",
        f"reasoning_content_present={'YES' if response.reasoning_content_present else 'NO'}",
        f"tokens_in={response.prompt_tokens}",
        f"tokens_out={response.completion_tokens}",
        f"reasoning_tokens={response.reasoning_tokens}",
        "fallback=NO",
    )


if __name__ == "__main__":
    asyncio.run(main())
