"""通用「带循环 + 功能调用」的 Agent 基类。

机制参考 ``ATRI/system/agent/brain/thinking.py``：模型在一次输出里先给出
「思考内容」，再用一段 ```text 代码块列出要调用的功能（每行一个
``功能名(参数名=参数值, ...)``）。解析后逐个执行，把结果回填给模型继续思考，
直到某个功能结束循环（:class:`AgentFinish`）或模型不再给出功能调用。

解析层做了两处兼容（对应 ``.agents/trpg_issues.md``）：

- **多个 ```text 代码块**：逐块解析并合并调用（约定仍是「一个文本区」，
  提示词里会要求模型写在同一个块里），单个坏块只忽略该块；
- **DSML 原生工具调用**：部分模型不按约定输出
  ``<｜｜DSML｜｜ invoke name="...">`` 这类原生调用 DSL，这里转成同样的调用
  结构，并把它连同参数值从正文里剥除，避免原样泄漏成给玩家看的叙述。

与框架的区别：这里的功能（工具）注册表完全由本插件维护，不接触主 Agent 的
``FunctionCallingManager``，避免跑团工具污染主 Agent。DSL 格式与框架保持一致。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.llm import (
    LLMContents,
    LLMMessage,
    LLMMessages,
    ModelRequestError,
    llm_manager,
)

from .base import AgentError, AgentResult, TrpgAgent

__all__ = [
    "AgentContext",
    "AgentFinish",
    "AgentTool",
    "ThinkingAgent",
    "ToolArg",
]


class AgentFinish(Exception):
    """功能用它提前结束循环，并携带最终要返回的内容（如 ``narrate``）。"""

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
    思考时抛出 :class:`AgentFinish`。
    """

    #: 供模型调用的功能名（小写下划线）
    function_name: ClassVar[str] = ""
    #: 功能说明
    description: ClassVar[str] = ""
    #: 参数声明
    args: ClassVar[tuple[ToolArg, ...]] = ()

    @staticmethod
    async def call(context: AgentContext, **kwargs: Any) -> Any:
        """执行功能，返回给模型的文本反馈。"""
        raise NotImplementedError

    @classmethod
    def prompt_block(cls) -> str:
        """生成一段功能说明，形如 ``名称 / 说明 / 参数``。"""
        lines = [f"{cls.function_name}:", f"    说明: {cls.description}"]
        if cls.args:
            lines.append("    参数:")
            lines.extend(
                f"        -{arg.name} {arg.type}: {arg.description}" for arg in cls.args
            )
        return "\n".join(lines)


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

    #: 从模型输出里提取 ```text 代码块（兼容无标注）
    _CALL_BLOCK_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"```(?:text)?\s*\n?([\s\S]*?)\n?```"
    )
    #: ``` 代码块首行的语言标注（如 ```python），解析调用前应跳过
    _LANG_TAG_RE: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z][\w+-]*")
    #: 模型自发的 DSML 原生工具调用：invoke 块（功能名 + 参数体）
    _DSML_INVOKE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"<[｜|]+DSML[｜|]+\s*invoke\s+name=\"(?P<name>[^\"]+)\"[^>]*>"
        r"(?P<body>[\s\S]*?)"
        r"</[｜|]+DSML[｜|]+\s*invoke\s*>"
    )
    #: DSML 参数：属性里带 ``string="true"`` 时表示该值按字符串原样保留
    _DSML_PARAM_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"<[｜|]+DSML[｜|]+\s*parameter\s+name=\"(?P<name>[^\"]+)\""
        r"(?P<attrs>[^>]*)>(?P<value>[\s\S]*?)"
        r"</[｜|]+DSML[｜|]+\s*parameter\s*>"
    )
    #: 残留的 DSML 标签（如未配对的 calls 包裹），清理正文时直接剥除
    _DSML_TAG_RE: ClassVar[re.Pattern[str]] = re.compile(r"</?[｜|]+DSML[｜|]+[^>]*>")

    # ---- 功能与提示词 ----

    def tool_map(self) -> dict[str, type[AgentTool]]:
        """功能名 -> 功能类。"""
        return {tool.function_name: tool for tool in self.tools}

    def function_prompt(self) -> str:
        """把功能清单与调用要求拼成一段提示词。"""
        blocks = "\n".join(tool.prompt_block() for tool in self.tools)
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
            "- 只用 ```text 代码块输出调用，禁止使用 XML / DSML 等其它调用格式"
        )

    def build_messages(self, values) -> LLMMessages:
        """系统提示词（角色 + 功能说明）与用户输入。"""
        messages = LLMMessages()
        system = self.system_prompt.strip()
        if self.tools:
            function_prompt = self.function_prompt()
            system = f"{system}\n\n{function_prompt}" if system else function_prompt
        if system:
            messages.add_message(LLMMessage("system", LLMContents().text(system)))
        messages.add_message(
            LLMMessage("user", LLMContents().text(self.build_prompt(values)))
        )
        return messages

    # ---- 解析 ----

    @staticmethod
    def parse_dsl(text: str) -> list[dict[str, Any]]:
        """把 DSL 文本解析成 ``[{"function": ..., "args": {...}}, ...]``。"""
        calls: list[dict[str, Any]] = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                tree = ast.parse(line, mode="eval")
            except SyntaxError as e:
                raise AgentError(f"第 {line_no} 行：语法错误：{e}") from e
            node = tree.body
            if not isinstance(node, ast.Call):
                raise AgentError(f"第 {line_no} 行：不是函数调用")
            if not isinstance(node.func, ast.Name):
                raise AgentError(f"第 {line_no} 行：功能名必须是简单标识符")
            func_name = node.func.id
            if node.args:
                raise AgentError(f"第 {line_no} 行：`{func_name}` 只允许关键字参数")
            args: dict[str, Any] = {}
            for kw in node.keywords:
                if kw.arg is None:
                    raise AgentError(f"第 {line_no} 行：不允许 **kwargs")
                try:
                    args[kw.arg] = ast.literal_eval(kw.value)
                except (ValueError, SyntaxError) as e:
                    raise AgentError(
                        f"第 {line_no} 行：参数 `{kw.arg}` 不是字面量：{e}"
                    ) from e
            calls.append({"function": func_name, "args": args, "line": line_no})
        return calls

    @classmethod
    def split_response(cls, text: str) -> tuple[str, list[dict[str, Any]]]:
        """把模型输出拆成 ``(思考 / 叙述内容, 功能调用列表)``。

        兼容三种写法：约定的单个 ```text 代码块、拆成多个的代码块（逐块解析、
        合并调用），以及模型自发的 DSML 原生工具调用 DSL（转成同样的调用结构，
        并把它连同参数值从正文里剥除）。
        """
        raw = text or ""
        calls: list[dict[str, Any]] = []
        for block in cls._CALL_BLOCK_RE.findall(raw):
            try:
                calls.extend(cls.parse_dsl(cls._strip_lang_tag(block)))
            except AgentError as e:
                # 单个坏块只忽略该块，不影响同轮其它合法调用。
                log.warning(f"功能调用解析失败，已忽略该代码块：{e}")

        dsml_calls, cleaned = cls.extract_dsml(raw)
        calls.extend(dsml_calls)
        content = cls._CALL_BLOCK_RE.sub("", cleaned).strip()
        return content, calls

    @classmethod
    def _strip_lang_tag(cls, block: str) -> str:
        """去掉 ``` 代码块首行的语言标注（如 ``python``），只留调用正文。"""
        lines = block.splitlines()
        if lines and cls._LANG_TAG_RE.fullmatch(lines[0].strip()):
            return "\n".join(lines[1:])
        return block

    @classmethod
    def extract_dsml(cls, text: str) -> tuple[list[dict[str, Any]], str]:
        """解析模型输出里的 DSML 原生工具调用，并把这段 DSL 从正文里剥除。

        部分模型不按约定写 ```text 代码块，而是输出形如
        ``<｜｜DSML｜｜ invoke name="功能名">…</｜｜DSML｜｜ invoke>`` 的原生调用
        DSL；这里转成与 :meth:`parse_dsl` 相同的结构并返回，同时把 invoke 块
        （连同参数值）与残留标签从文本里移除，避免原样泄漏成给玩家看的叙述。
        返回 ``(调用列表, 清理后的文本)``。
        """
        calls: list[dict[str, Any]] = []
        for match in cls._DSML_INVOKE_RE.finditer(text):
            func_name = match.group("name").strip()
            if not func_name:
                continue
            args: dict[str, Any] = {}
            for param in cls._DSML_PARAM_RE.finditer(match.group("body")):
                key = param.group("name").strip()
                if not key:
                    continue
                attrs = param.group("attrs")
                is_string = 'string="true"' in attrs
                args[key] = (
                    param.group("value").strip()
                    if is_string
                    else cls._dsml_value(param.group("value"))
                )
            calls.append({"function": func_name, "args": args, "line": 0})

        cleaned = cls._DSML_INVOKE_RE.sub("", text)
        cleaned = cls._DSML_TAG_RE.sub("", cleaned)
        if calls:
            log.debug(
                "识别到 DSML 原生工具调用 "
                + "、".join(call["function"] for call in calls)
            )
        return calls, cleaned

    @staticmethod
    def _dsml_value(raw: str) -> Any:
        """DSML 参数值还原：数字 / 布尔等字面量转回原类型，其余按字符串保留。"""
        value = (raw or "").strip()
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
        if isinstance(parsed, (bool, int, float, list, tuple, dict)):
            return parsed
        return value

    # ---- 循环 ----

    async def run(
        self, context: AgentContext | None = None, **values: Any
    ) -> AgentResult:
        """跑完一轮「思考 → 调用功能 → 回填」的循环，返回最终结果。"""
        self.check_inputs(values)
        self.ensure_available()
        context = context or AgentContext()
        tools = self.tool_map()

        messages = self.build_messages(values)
        last_content = ""

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

            raw = resp.content if isinstance(resp.content, str) else str(resp.content)
            content, calls = self.split_response(raw)
            if content:
                last_content = content

            # 没有功能调用：把当前内容当作最终叙述结束。
            if not calls:
                return self._result(last_content, resp)

            messages.add_message(LLMMessage("assistant", LLMContents().text(raw)))

            feedback: list[str] = []
            for call in calls:
                name = call["function"]
                tool = tools.get(name)
                if tool is None:
                    feedback.append(f"未知的功能：{name}")
                    continue
                try:
                    result = await tool.call(context, **call["args"])
                except AgentFinish as finish:
                    return self._result(finish.output, resp)
                except Exception as e:
                    log.warning(f"跑团功能 {name} 执行失败：{str_traceback(e)}")
                    feedback.append(f"功能 {name} 执行失败，请勿再次调用")
                    continue
                # 记下功能输出的文本摘要：调用方据此写复盘里的「裁定」一行。
                context.data.setdefault("tool_log", []).append(
                    {"function": name, "result": str(result)}
                )
                feedback.append(f"调用 {name} 返回：{result}")

            messages.add_message(
                LLMMessage("user", LLMContents().text("\n".join(feedback)))
            )

        raise AgentError(f"{self.title}超过最大思考轮数（{self.max_steps}）")

    def _result(self, content: str, resp: Any) -> AgentResult:
        return AgentResult(
            agent=self.name,
            title=self.title,
            content=content or "",
            model_type=self.model_type,
            usage=getattr(resp, "usage", None),
        )
