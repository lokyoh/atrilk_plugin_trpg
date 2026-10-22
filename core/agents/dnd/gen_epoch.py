"""跑团 Agent：纪元称号命名。

称号的**内容**（这个纪元发生了什么）是程序统计出来的，
但**措辞**不是：「让霜牙岭欠了三次人情的人」这句话没法算出来。
所以这里只做一件事——把一份事实清单变成一个称号。

三条纪律：

1. **这是内部 agent，不是 DM 工具**。DM 工具清单有 4300 字符的硬上限
   （当前 4293，只剩 7 字符余量），而称号是**每四章才发生一次**的编排动作，
   不该占 DM 每轮都要读的工具预算。
2. **必须有程序兜底**（:func:`~...dnd.epoch.fallback_epithet`）。
   验收标准是「称号进圣经」——那是存档层面的承诺，不该因为一次生成失败就落空。
3. **一次调用，不重试**。称号是叙事装饰，错了只是不好听；为它重试
   是把预算花在最不值的地方。解析不出来就直接用兜底。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = [
    "EpithetAgent",
    "EpithetResult",
    "epithet_agent",
]


_SYSTEM_PROMPT = """# 任务
根据给定的「一个纪元里实际发生了什么」，给这队玩家起一个**称号**。

# 规则
- 称号是一句话，**12 到 20 个字**，中文，可以带引号以外的标点。
- 必须**从这个纪元真实发生过的事**里长出来：打过的仗、办成的差、欠下的账、
  某个势力对他们的态度。不要写与事实无关的褒贬。
- 听起来要像**别人私下叫他们的**，不是官方表彰，不是成语。
  差（「把盐仓的账翻了个底朝天」）、带刺（「白港欠了三次人情的那位」）都可以。
- 不要出现「勇者」「传奇」「英雄」「史诗」这类空词。
- 不要复述数字（「打了 12 场仗」），也不要写难度、章号、CR。
- **只输出称号本身**，不要解释、不要给备选、不要加书名号以外的包装。
"""

#: 围栏里的键。留着围栏是为了跟事件卡生成器一个形状（便于统一解析），
#: 也让模型清楚「这是一个结构化输出，不是正文」。
_FENCE_RE = re.compile(r"```epithet\s*(.+?)\s*```", re.S)

#: 称号长度上限。超了说明模型在写句子而不是写称号。
_MAX_LEN = 24


@dataclass(slots=True)
class EpithetResult(AgentResult):
    """称号生成结果。"""

    #: 称号正文；解析失败或校验不过时为空
    epithet: str = ""
    #: 解析失败的原因（上层据此决定用兜底）
    problems: list[str] = field(default_factory=list)


def parse_epithet(text: str) -> tuple[str, list[str]]:
    """从模型原文里取出称号。返回 ``(称号, 问题列表)``。

    校验只有一条：**非空且不太长**。刻意不做「必须含某词」这类内容校验——
    称号是自由文本，硬性内容规则只会逼出模板句。

    没有围栏时按纯文本取，但**带反引号的一律判失败**：那说明模型想输出
    代码块却没闭合，把 ```` ```epithet ```` 本身当成称号只会让垃圾进圣经。
    """
    problems: list[str] = []
    match = _FENCE_RE.search(text or "")
    body = (match.group(1) if match else (text or "")).strip()
    # 只取第一行：模型爱在后面附解释，解释不是称号的一部分
    lines = body.splitlines()
    name = lines[0].strip().strip("「」\"'“” ") if lines else ""
    name = name.rstrip("。.!！?？")
    if "`" in name:
        problems.append("模型给出的是未闭合的代码块，没能取出称号")
        return "", problems
    if not name:
        problems.append("模型没有给出称号")
        return "", problems
    if len(name) > _MAX_LEN:
        problems.append(f"称号过长（{len(name)} 字）")
        return "", problems
    return name, problems


class EpithetAgent(TrpgAgent):
    """纪元称号命名：把事实清单变成一个别人会叫出口的名字。"""

    name = "epithet"
    title = "纪元称号"
    description = "按一个纪元里实际发生的事，为玩家起一个称号"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput("world", "世界档案与圣经", description="专有名词的来源"),
        AgentInput("deeds", "本纪元事实", description="程序统计出的行为清单"),
        AgentInput(
            "verdict",
            "收束判定",
            required=False,
            description="这个纪元的结局或未达成原因",
        ),
    )

    async def run(self, **values: object) -> EpithetResult:
        result = await super().run(**values)
        epithet, problems = parse_epithet(result.content)
        return EpithetResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            epithet=epithet,
            problems=problems,
        )


#: 全局默认实例
epithet_agent = EpithetAgent()
