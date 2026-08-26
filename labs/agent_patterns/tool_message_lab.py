"""Beginner-friendly Assignment 3: build one provider ``tool`` message.

This lab receives an already-redacted Observation.  It never receives a
capability, API key, HTTP client, or raw ERP response.
"""

from __future__ import annotations

from agent_runtime.agent.contracts import Observation, ToolName
from agent_runtime.providers import ProviderMessage


def build_learning_tool_message(
    *,
    provider_tool_call_id: str,
    tool_name: ToolName,
    observation: Observation,
) -> ProviderMessage:
    """Return an OpenAI-compatible ``tool`` role message.

    业务背景: provider 发出一次 function call 后, 下一轮必须知道“这是哪一
    次调用的结果”和“哪个只读工具返回了什么有界摘要”。如果漏掉 call id,
    provider 可能无法把结果配回原始调用; 如果把完整 ERP 返回塞进去, 上下文
    和敏感数据边界都会失控.

    输入已经是脱敏、限长的 Observation. 你只需要把三个身份字段和
    ``observation.summary`` 放进 ProviderMessage, 不要自行拼 JSON 或处理密钥.

    传统半成品 sample (不是答案):

        return ProviderMessage(
            role="______",                    # OpenAI 的工具结果角色
            tool_call_id=______,                # provider 返回的 call id
            name=______,                        # 当前只读工具名
            content=______,                     # 只能放有界摘要
        )

    对照表:

    - ``provider_tool_call_id`` -> ``ProviderMessage.tool_call_id``
    - ``tool_name`` -> ``ProviderMessage.name``
    - ``observation.summary`` -> ``ProviderMessage.content``
    """
    # 不要改 ProviderMessage 的契约, 不要把 digest 或完整 Observation 序列化进 content.
    return ProviderMessage(
        role="tool",
        tool_call_id=provider_tool_call_id,  # provider 返回的 call id
        name=tool_name,  # 当前只读工具名
        content=observation.summary,  # 只能放有界摘要
    )
