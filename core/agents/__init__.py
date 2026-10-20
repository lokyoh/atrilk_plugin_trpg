"""跑团 AI（LLM）框架。

- :mod:`.base`：声明式 Agent 基类（单次生成的 agent 用它）
- :mod:`.thinking`：带思考循环与功能调用的 Agent 基类（DM 等用它）

具体规则体系的 agent 放在各自子包里（如 :mod:`.dnd`），这里只放与规则体系
无关的框架，避免相互耦合。
"""

from __future__ import annotations

from .base import AgentError, AgentInput, AgentResult, TrpgAgent
from .thinking import (
    AgentContext,
    AgentFinishError,
    AgentTool,
    ThinkingAgent,
    ToolArg,
)

__all__ = [
    "AgentContext",
    "AgentError",
    "AgentFinishError",
    "AgentInput",
    "AgentResult",
    "AgentTool",
    "ThinkingAgent",
    "ToolArg",
    "TrpgAgent",
]
