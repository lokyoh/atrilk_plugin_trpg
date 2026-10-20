"""通用「带循环 + 功能调用」的 Agent 基类。

机制参考 ``ATRI/system/agent/brain/thinking.py``：模型在一次输出里先给出
「思考内容」，再用一段 ```text 代码块列出要调用的功能（每行一个
``功能名(参数名=参数值, ...)``）。解析后逐个执行，把结果回填给模型继续思考，
直到某个功能结束循环（:class:`AgentFinishError`）或模型不再给出功能调用。

解析层复用主 Agent 的解释器（``ATRI.system.agent.brain.action_parser``），
下面这些容错都在那边实现、两边共享：

- **多个 ```text 代码块**：逐块解析并合并调用（约定仍是「一个文本区」，
  提示词里会要求模型写在同一个块里），解析不出调用的块按正文保留；
- **DSML 原生工具调用**：部分模型不按约定输出
  ``<｜｜DSML｜｜ invoke name="...">`` 这类原生调用 DSL，共享解释器把它转成
  同样的调用结构，并连同参数值从正文里剥除，避免原样泄漏成给玩家看的叙述；
- **坏调用不再整块作废**：单处调用的问题作为「待回填的解析问题」返回，
  由循环回填给模型重写，而不是丢掉同块的其它调用。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.brain import action_parser
from ATRI.system.agent.llm import (
    LLMContents,
    LLMMessage,
    LLMMessages,
    ModelRequestError,
    llm_manager,
)

from .base import AgentError, AgentResult, TrpgAgent, UsageEntry
from ..trpg.text import strip_safety_blocks

__all__ = [
    "AgentContext",
    "AgentFinishError",
    "AgentTool",
    "ThinkingAgent",
    "ToolArg",
]


class AgentFinishError(Exception):
    """功能用它提前结束思考循环（**不是错误**：只是用异常短路循环），并携带最终要返回的内容。"""

    def __init__(self, output: str = ""):
        super().__init__(output)
        self.output = output


@dataclass(slots=True)
class AgentContext:
    """功能执行时的上下文。

    只携带定位数据所需的标识（如 ``user_id``）；功能据此自行到存储层取数据，
    与「场景 / 玩家数据内联进提示词」相互独立。
    """

    #: 玩家 ID，供功能读取角色卡等存储
    user_id: str = ""
    #: 存档 ID；DM 的角色卡读写与事件日志都落在存档里
    save_id: str = ""
    #: 会话 ID，备用
    chat_id: str = ""
    #: 其它临时数据
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolArg:
    """功能的一项参数声明，用于生成给模型的调用说明。"""

    name: str
    type: str
    description: str


class AgentTool:
    """可被 :class:`ThinkingAgent` 调用的一项功能。

    子类用类属性声明名称、说明与参数，用 :meth:`call` 实现逻辑。需要结束整轮
    思考时抛出 :class:`AgentFinishError`。
    """

    #: 供模型调用的功能名（小写下划线）
    function_name: ClassVar[str] = ""
    #: 功能说明
    description: ClassVar[str] = ""
    #: 参数声明
    args: ClassVar[tuple[ToolArg, ...]] = ()
    #: 返回给模型的最大长度：超了截断，免得多次思考把上下文撑爆（0 = 不截）
    #: 需要整篇返回的功能（如读场景文档）在自己的类里调大
    max_result_chars: ClassVar[int] = 600

    @staticmethod
    async def call(context: AgentContext, **kwargs: Any) -> Any:
        """执行功能，返回给模型的文本反馈。"""
        raise NotImplementedError

    @classmethod
    def prompt_block(cls) -> str:
        """一行式功能说明：``- 名称：说明｜参数：a（类型）=说明，b（类型）=说明``。

        原来是「名称 / 说明 / 参数」四五行缩进块，22 个功能光缩进与换行就吃掉上千字；
        合成一行后含义不变，提示词短一截。
        """
        line = f"- {cls.function_name}：{cls.description}"
        if cls.args:
            args = "，".join(
                f"{arg.name}（{arg.type}）={arg.description}" for arg in cls.args
            )
            line += f"｜参数：{args}"
        return line


class ThinkingAgent(TrpgAgent):
    """带思考循环与功能调用的 Agent 基类。

    子类在 :class:`TrpgAgent` 的基础上再用 ``tools`` 声明可用功能、用
    ``max_steps`` 声明最大思考轮数；``system_prompt`` 里只写角色与原则，
    功能清单与调用格式由本基类自动追加。
    """

    #: 可用功能（:class:`AgentTool` 子类）
    tools: ClassVar[tuple[type[AgentTool], ...]] = ()
    #: 单次运行的最大思考轮数
    max_steps: ClassVar[int] = 8

    # ---- 功能与提示词 ----

    def active_tools(self, values: Mapping[str, Any]) -> tuple[type[AgentTool], ...]:
        """本次运行可用的功能；默认全给，子类可按局面裁剪（省提示词、少误调用）。"""
        return self.tools

    def tool_map(
        self, tools: tuple[type[AgentTool], ...] | None = None
    ) -> dict[str, type[AgentTool]]:
        """功能名 -> 功能类。"""
        return {tool.function_name: tool for tool in (tools or self.tools)}

    def function_prompt(self, tools: tuple[type[AgentTool], ...] | None = None) -> str:
        """把功能清单与调用要求拼成一段提示词。"""
        blocks = "\n".join(tool.prompt_block() for tool in (tools or self.tools))
        return (
            "你可以调用以下功能，禁止调用未列出的功能:\n"
            f"{blocks}\n\n"
            "功能调用格式：\n"
            "``` text\n"
            "功能名(参数名=参数值, ...)\n"
            "```\n\n"
            "调用要求：\n"
            "- 每行一个调用，无参数写成 功能名()\n"
            "- 字符串用双引号，内容需要换行时写 \\n，禁止在引号内直接换行\n"
            "- 只使用上面列出的参数名，不要虚构参数\n"
            "- 所有调用写在**同一个** ```text 代码块里，不要拆成多个代码块\n"
            "- **分轮调用**：需要先看某个功能返回结果（如检定 / 掷骰成败）再写的内容，"
            "必须等结果返回、下一轮再写；结束本次思考的功能（如 narrate）**永远单独一轮调用**，"
            "不要与其它功能写在同一次输出里\n"
            "- 只用 ```text 代码块输出调用，禁止使用 XML / DSML 等其它调用格式"
        )

    def build_messages(self, values) -> LLMMessages:
        """系统提示词（角色 + 功能说明）与用户输入。"""
        messages = LLMMessages()
        system = self.system_prompt.strip()
        tools = self.active_tools(values)
        if tools:
            function_prompt = self.function_prompt(tools)
            system = f"{system}\n\n{function_prompt}" if system else function_prompt
        if system:
            messages.add_message(LLMMessage("system", LLMContents().text(system)))
        messages.add_message(
            LLMMessage("user", LLMContents().text(self.build_prompt(values)))
        )
        return messages

    # ---- 解析 ----

    @classmethod
    def parse_dsl(cls, text: str) -> list[dict[str, Any]]:
        """把 DSL 文本解析成 ``[{"function": ..., "args": {...}}, ...]``。"""
        return action_parser.parse_calls(text)

    @classmethod
    def split_response(cls, text: str) -> tuple[str, list[dict[str, Any]], list[str]]:
        """把模型输出拆成 ``(叙述内容, 功能调用列表, 待回填的解析问题)``。

        多代码块合并、DSML 原生调用、坏调用容错都在共享解释器里，这里只转发，
        避免与主 Agent 各自维护一套解析。
        """
        return action_parser.split_response(text)

    # ---- 循环 ----

    async def run(
        self, context: AgentContext | None = None, **values: Any
    ) -> AgentResult:
        """跑完一轮「思考 → 调用功能 → 回填」的循环，返回最终结果。"""
        self.check_inputs(values)
        self.ensure_available()
        context = context or AgentContext()
        tools = self.tool_map(self.active_tools(values))

        messages = self.build_messages(values)
        last_content = ""
        usages: list[UsageEntry] = []

        for _step in range(self.max_steps):
            try:
                resp = await llm_manager.call_model_by_type(
                    self.model_type, messages, f"跑团-{self.title}"
                )
            except ModelRequestError as e:
                raise AgentError(f"{self.title}调用模型失败：{e}") from e
            except Exception as e:
                log.warning(f"跑团 {self.title} 思考失败：{str_traceback(e)}")
                raise AgentError(f"{self.title}思考失败：{e}") from e

            self._collect_usage(usages, resp)
            raw = resp.content if isinstance(resp.content, str) else str(resp.content)
            content, calls, issues = self.split_response(raw)
            # 审查元信息（<ds_safety>…）不是正文：这里就剥掉，别带进最终叙述
            content = strip_safety_blocks(content)
            if content:
                last_content = content

            # 没有功能调用也没有解析问题：把当前内容当作最终叙述结束。
            if not calls and not issues:
                return self._result(last_content, resp, usages)

            messages.add_message(LLMMessage("assistant", LLMContents().text(raw)))

            if issues:
                detail = "；".join(issues)
                feedback = [
                    f"本次输出有 {len(issues)} 处调用无法解析：{detail}。"
                    "请严格按「功能名(参数名=参数值, ...)」格式重写：字符串用双引号，"
                    "所有调用写在同一个代码块里。"
                ]
            else:
                feedback = []

            finish: AgentFinishError | None = None
            for call in calls:
                name = call["function"]
                tool = tools.get(name)
                if tool is None:
                    feedback.append(f"未知的功能：{name}")
                    continue
                # 与主 Agent 共用同一套参数校验：问题回填让模型重写，
                # 而不是等 tool.call 抛 TypeError 只报「执行失败」
                args, arg_issues = action_parser.normalize_args(tool, call["args"])
                if arg_issues:
                    log.debug(f"跑团功能 {name} 参数不合规：{arg_issues}")
                    feedback.append(
                        f"功能 {name} 的参数不合规：{'；'.join(arg_issues)}。"
                        "请对照功能说明里的参数重写本次调用"
                    )
                    continue
                try:
                    result = await tool.call(context, **args)
                except AgentFinishError as raised:
                    if len(calls) == 1:
                        return self._result(raised.output, resp, usages)
                    # 结束调用的文本是在**没看到**同批功能返回的情况下写出来的：
                    # 暂存不发，把同批其它功能的返回结果回填给模型，
                    # 要求结合结果单独重写（如 narrate 要如实写检定成败）。
                    finish = raised
                    continue
                except Exception as e:
                    log.warning(f"跑团功能 {name} 执行失败：{str_traceback(e)}")
                    feedback.append(f"功能 {name} 执行失败，请勿再次调用")
                    continue
                # 记下功能输出的文本摘要：调用方据此写复盘里的「裁定」一行。
                # 过长的返回会截断——每一步思考都会把这段重新发给模型，不截会越滚越大。
                text = str(result)
                limit = getattr(tool, "max_result_chars", 0) or 0
                if limit and len(text) > limit:
                    text = (
                        text[:limit].rstrip()
                        + "\n……（返回过长已截断；需要更多就缩小查询范围）"
                    )
                context.data.setdefault("tool_log", []).append(
                    {"function": name, "result": text}
                )
                feedback.append(f"调用 {name} 返回：{text}")

            if finish is not None:
                feedback.append(
                    "注意：结束循环的功能（如 narrate）不能与其它功能写在同一次输出里，"
                    "本次叙述**尚未发送**。上面是同批其它功能的返回结果——"
                    "请结合它们（尤其是检定 / 掷骰的成败）重新决定剧情走向，"
                    "不要重复调用本轮已调用过的功能，"
                    "然后**单独**调用结束功能输出最终内容。"
                )

            messages.add_message(
                LLMMessage("user", LLMContents().text("\n".join(feedback)))
            )

        raise AgentError(f"{self.title}超过最大思考轮数（{self.max_steps}）")

    @staticmethod
    def _collect_usage(usages: list[UsageEntry], resp: Any) -> None:
        """把一次模型调用的用量记进本轮清单（供计费统计）。"""
        usage = getattr(resp, "usage", None)
        if usage:
            usages.append(
                UsageEntry(model=getattr(resp, "model", None) or "", usage=usage)
            )

    def _result(
        self, content: str, resp: Any, usages: list[UsageEntry] | None = None
    ) -> AgentResult:
        # narrate 等结束功能的输出同样可能夹带审查元信息，统一在出口清掉
        return AgentResult(
            agent=self.name,
            title=self.title,
            content=strip_safety_blocks(content or ""),
            model_type=self.model_type,
            usage=getattr(resp, "usage", None),
            usages=list(usages or []),
        )
