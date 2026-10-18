"""跑团 Agent 基类。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」与「DM Agent 工作流」：
入场流程中的每个生成环节（世界 / 故事 / 区域 / 地点 / 场景）与 DM 主持，
都用这里声明的 :class:`TrpgAgent` 描述。

约定：

- 生成类 agent（世界 / 故事 / 区域 / 地点 / 场景）使用
  :data:`~ATRI.system.agent.llm.ModelType.WORK`（工作模型），只负责产出文档；
- :class:`~.dm.DungeonMasterAgent` 使用
  :data:`~ATRI.system.agent.llm.ModelType.CHAT`（聊天模型），负责实际主持。

模型统一走 ATRI 的 ``llm_manager``；没有可用模型、缺少输入或调用失败时
抛出 :class:`AgentError`，由调用方决定如何降级。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.llm import (
    LLMContents,
    LLMMessage,
    LLMMessages,
    ModelRequestError,
    ModelType,
    llm_manager,
)

__all__ = [
    "AgentError",
    "AgentInput",
    "AgentResult",
    "TrpgAgent",
    "UsageEntry",
]


class AgentError(Exception):
    """Agent 无法完成本次任务（缺模型 / 缺少输入 / 调用失败）。"""


@dataclass(frozen=True, slots=True)
class AgentInput:
    """agent 的一项输入声明，供拼装提示词与校验使用。"""

    name: str
    label: str
    required: bool = True
    description: str = ""


@dataclass(frozen=True, slots=True)
class UsageEntry:
    """一次模型调用的用量记录（模型名 + token 数），供计费统计使用。"""

    #: 实际应答的模型名（``llm_manager`` 里注册的名字）
    model: str = ""
    #: 本次调用的 token 用量
    usage: Any = field(default=None)


@dataclass(slots=True)
class AgentResult:
    """agent 一次运行的结果。"""

    agent: str
    title: str
    content: str
    model_type: ModelType = ModelType.WORK
    usage: Any = field(default=None)
    #: 本次运行里**每一次**模型调用的用量（思考循环可能调用多次）
    usages: list[UsageEntry] = field(default_factory=list)

    def __str__(self) -> str:
        return self.content


class TrpgAgent:
    """跑团 Agent 基类：声明式定义 + 统一的模型调用。

    子类通过类属性声明元信息、提示词与输入，通常无需覆写任何方法；
    需要更复杂的输入拼装时覆盖 :meth:`build_prompt` 即可。
    """

    #: 英文标识（小写下划线），在同一插件内唯一
    name: ClassVar[str] = ""
    #: 中文名，用于展示与日志
    title: ClassVar[str] = ""
    #: 一句话说明
    description: ClassVar[str] = ""
    #: 系统提示词（内联）
    system_prompt: ClassVar[str] = ""
    #: 使用的模型类型
    model_type: ClassVar[ModelType] = ModelType.WORK
    #: 输入声明
    inputs: ClassVar[tuple[AgentInput, ...]] = ()

    # ---- 输入 ----

    def check_inputs(self, values: Mapping[str, object]) -> None:
        """校验必填输入，缺失时抛出 :class:`AgentError`。"""
        missing = [
            item.label
            for item in self.inputs
            if item.required and not str(values.get(item.name, "") or "").strip()
        ]
        if missing:
            raise AgentError(f"{self.title}缺少必要输入：" + "、".join(missing))

    def build_prompt(self, values: Mapping[str, object]) -> str:
        """把输入按声明顺序拼成给模型的用户消息。子类可覆盖。"""
        blocks: list[str] = []
        for item in self.inputs:
            if item.name not in values:
                continue
            text = str(values[item.name] or "").strip()
            if not text:
                continue
            blocks.append(f"# {item.label}\n{text}")
        return "\n\n".join(blocks)

    # ---- 调用 ----

    def build_messages(self, values: Mapping[str, object]) -> LLMMessages:
        """组装系统提示词与用户输入。"""
        messages = LLMMessages()
        if self.system_prompt.strip():
            messages.add_message(
                LLMMessage(
                    role="system", content=LLMContents().text(self.system_prompt)
                )
            )
        messages.add_message(
            LLMMessage(
                role="user", content=LLMContents().text(self.build_prompt(values))
            )
        )
        return messages

    def ensure_available(self) -> None:
        """确认所需类型的模型已注册。"""
        if not llm_manager.has_type(self.model_type):
            raise AgentError(
                f"{self.title}需要 {self.model_type.value} 类型模型，当前没有可用模型"
            )

    async def run(self, **values: object) -> AgentResult:
        """按声明拼装输入并调用模型，返回 :class:`AgentResult`。"""
        self.check_inputs(values)
        self.ensure_available()
        try:
            resp = await llm_manager.call_model_by_type(
                self.model_type, self.build_messages(values), f"跑团-{self.title}"
            )
        except ModelRequestError as e:
            raise AgentError(f"{self.title}调用模型失败：{e}") from e
        except Exception as e:
            log.warning(f"跑团 {self.title} 生成失败：{str_traceback(e)}")
            raise AgentError(f"{self.title}生成失败：{e}") from e

        content = resp.content
        if not isinstance(content, str):
            content = str(content)
        usage = getattr(resp, "usage", None)
        return AgentResult(
            agent=self.name,
            title=self.title,
            content=content,
            model_type=self.model_type,
            usage=usage,
            usages=[
                UsageEntry(model=getattr(resp, "model", None) or "", usage=usage)
            ]
            if usage
            else [],
        )

    def describe(self) -> str:
        """给帮助 / 日志用的一行说明。"""
        return f"{self.title}（{self.name}）：{self.description}"
