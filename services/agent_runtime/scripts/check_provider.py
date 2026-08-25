"""BYOK 连通性自检: 向已配置的 model provider 发一个最小请求并打印响应片段。

用法:
    source env/dev/.env
    uv run --python 3.14 python services/agent_runtime/scripts/check_provider.py

退出码 0 = 连通成功; 非 0 = 失败。任何输出都不包含 API Key。
"""

import asyncio
import sys

sys.path.insert(0, "services/agent_runtime/src")

from agent_runtime.providers import ProviderMessage, ProviderError, provider_from_environment


async def main() -> None:
    try:
        provider = provider_from_environment()
        # 成本护栏: 连通测试只发 1 条最小消息, 输出上限 16 token。
        response = await provider.complete(
            [ProviderMessage(role="user", content="ping")], max_tokens=16
        )
    except ProviderError as error:
        print(f"PROVIDER-FAIL: {error}")
        raise SystemExit(1) from error
    except ValueError as error:
        print(f"PROVIDER-CONFIG-FAIL: {error}")
        raise SystemExit(1) from error
    print("PROVIDER-OK:", response.text[:80])


if __name__ == "__main__":
    asyncio.run(main())
