"""跑团 Agent：事件生成。

事件取代了「事件推力文档」：**章 = 一个事件**（见 :mod:`...dnd.events`）。
这一层与 :mod:`~.gen_event` 的分工是刻意对称的：

- 事件卡生成在**玩家选定走向之后**才跑，输入是分支（程序织好的）；
- 事件生成在**玩家连续多跳没走向当前事件**之后才跑（:func:`~.event_flow.weave_event`），
  输入是修正表账本（玩家改过什么）与当下挂着的事。

两者共用的纪律不变：**程序算得出来的绝不交给模型**。规模档（战斗节点数下限）、
场景候选（``Node.at`` 只能从世界里已有的场景里挑）、id 分配都由系统给定，
模型只写「什么地方、什么人、什么场面、什么代价」。输出用 ``event-graph``
围栏包着的 JSON，解析复用 :func:`~...dnd.events.text_to_event`，
校验用 :func:`~...dnd.module_graph.validate`——**拒绝重织，不做静默修补**。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...dnd.events import EventSlot, text_to_event
from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = [
    "EventAgent",
    "EventResult",
    "event_agent",
]


_SYSTEM_PROMPT = """# 任务
写一件**触发式事件**：一件**正落在玩家头上**的事。
不是「世界里出了件可以追的事」，而是**它已经碰到玩家了**——被扣下、过不去、
撞见、或者非它不可。玩家可以追也可以不追，但**开局那一步他躲不开**。
它包着一张**网状节点图**——不是一条线。同一个事件，不同的玩家该走出不同的局面。

# 输入
- **世界档案**：专有名词（人名、势力名、地名）以它为准，已有名字可以直接用。
- **场景候选**：节点的所在地 ``at`` **只能**从这些里挑，一个都不能是候选之外的名字。
  候选里既可能有区域（「白港外滩」），也可能有场所里的某个房间
  （「罗盘玫瑰号的主甲板」）——两者都能用，选哪个由事情发生在哪里决定。
- **规模档**：light / medium / heavy，决定**至少要几场战斗**。
- **上下文**：世界当前时间、玩家改过什么（修正表）、还挂着的事件、队伍概况。

# 输出
一个 ```event-graph 围栏，里面是一段 JSON：

```json
{
  "title": "事件标题（12 字以内，有具体画面）",
  "duration_days": 3,
  "scale": "medium",
  "stage": "大概在哪一带（如「白港一带」，不要写成硬绑地点）",
  "default_outcome": "没人管它时这件事怎么收场（一句话，可能好也可能坏）",
  "rumors": ["最模糊的一条", "中间的一条", "最具体的一条"],
  "graph": {
    "conflict": "一句话：谁要什么、谁挡着谁",
    "scale": "medium",
    "facts": {
      "fact_id": {"text": "玩家会知道的说法", "kind": "secret|proof|debt|access", "excludes": ["另一个fact_id"]}
    },
    "nodes": {
      "node_id": {
        "title": "局面的名字",
        "at": "场景候选里的一个",
        "kind": "clue|encounter|battle|decision|clock|showdown",
        "requires": ["fact_id"],
        "grants": ["fact_id"],
        "on_fail": ["fact_id"],
        "cost": "做这件事要付的代价（一句话）",
        "depth": 1,
        "hint": "给 DM 的一句话：这里会发生什么（不写台词）"
      }
    },
    "convergences": [
      {"id": "c1", "title": "结局名", "requires": [["fact_id"], ["fact_id", "fact_id"]], "kind": "ending|turn", "rank": 0, "outcome": "达成后世界怎么变"}
    ],
    "clocks": [{"name": "时钟名", "every": 4, "fires": "node_id"}]
  }
}
```

# 硬规则（违反任何一条都会被退回重织）
1. **节点 5 到 10 个**，每个都要有 ``title`` / ``at`` / ``hint``。
2. ``at`` 只能填场景候选里的名字。候选之外的地名一律不许出现。
3. **每个节点至少 ``grants`` 一个事实**——不产出事实的节点是死胡同。
4. 战斗节点数：light 至少 1 场、medium 至少 2 场、heavy 至少 3 场。战斗是 DND 的主轴。
5. 每个事实都要**有节点能产出**（拿不到的事实 = 永久死锁）；每个节点都要**从入口可达**
   （入口 = 没有 ``requires`` 的节点，至少一个）。
6. **至少 2 个收束**；至少一个收束有**两套不同的成立条件组**（``requires`` 是两行）。
7. 至少一个收束的某一组条件**不依赖战斗产出的事实**——打不过的玩家要有
   「撤出去、把消息带回来」这类退路。
8. **至少一对 ``excludes``**：拿到 A 就永久失去 B。没有互斥就没有抉择。
9. ``encounter`` / ``decision`` 节点必须写 ``cost``——白送的东西玩家不需要判断。
10. **至少一个 ``depth: 2`` 的节点**：同一地点要值得来第二次。
11. ``decision`` 节点的两边（``grants`` 与 ``on_fail``）给的事实必须不同。
12. **入口必须是硬钩子**：入口节点（没有 ``requires`` 的那个）里发生的事
    **必须作用在玩家自己身上**——被扣下 / 过不去 / 撞见 / 非要不可，四选一。
    「听说某地不太平」「有人请你去看看」这种**玩家可以转身走开**的入口一律退回。

# 硬钩子（四选一，写进入口节点的 title 与 hint）
- **被扣下**：你成了嫌疑人、被认错成另一个人、或者你的东西被扣了——走不掉。
- **过不去**：路封了 / 门关了 / 船不发，而你有非走不可的理由（船期、等的人、追的货）。
- **撞见**：事在你眼前发生——塌方、死人砸在脚边、当面动手。不是听说，是你在场。
- **非要不可**：你的家伙坏了只有这儿能修、你要找的人或货就在这儿。
- **不用「受雇」**：雇佣玩家可以拒绝，那是软钩子。真要写委托，理由必须是
  「正因为你是生面孔」——且仍要让玩家此刻走不掉。

# 内容要求
- **流言只有真与偏**：2 到 4 条，从模糊到具体。不许写无关的（「最近不太平」这种空话不算）。
- ``default_outcome`` 不是惩罚：玩家不追，事情就按它自己的逻辑走完。
- ``cost`` 写具体的损失（暴露自己、欠人情、错过另一处），不要写「消耗了一些体力」。
- 场面要具体：写「盐仓码头夜班交接时」，不要写「某个港口」。
- 只用世界档案里已有的专有名词；确有必要只新加**一个**人名。
- 承接上下文：修正表里玩家做过的事，要有回响（旧账上门、对手升级、被烧过的地方再出事）。
- 语言简洁，每字段一到两句。
"""


@dataclass(slots=True)
class EventResult(AgentResult):
    """事件生成结果：模型原文 + 解析出的槽位。"""

    #: 解析出来的事件；解析失败时为空
    slot: EventSlot | None = None
    #: 解析失败的原因（供上层决定重试还是放弃）
    problems: list[str] = field(default_factory=list)


class EventAgent(TrpgAgent):
    """事件生成：把「世界里正在发生什么」织成一张可玩的网状节点图。"""

    name = "event"
    title = "事件生成"
    description = "按上下文织一件触发式事件（节点图 / 收束 / 流言）"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput("world", "世界档案", description="专有名词的来源"),
        AgentInput("scenes", "场景候选", description="节点所在地只能从这些里挑"),
        AgentInput(
            "scale", "规模档", description="light / medium / heavy，决定战斗场数下限"
        ),
        AgentInput(
            "context",
            "上下文",
            required=False,
            description="世界时间、修正表账本、挂着的事件、队伍概况",
        ),
    )

    async def run(self, **values: object) -> EventResult:
        result = await super().run(**values)
        slot = text_to_event(result.content)
        problems: list[str] = []
        if slot is None:
            problems.append("模型没有给出可解析的 event-graph 围栏")
        return EventResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            slot=slot,
            problems=problems,
        )


#: 全局默认实例
event_agent = EventAgent()
