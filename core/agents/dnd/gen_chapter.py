"""跑团 Agent：章节卡内容生成。

章节卡取代了原来的「故事推力」文档。生成器（story）产出的是**一个世界的长期压力**，
而章节卡产出的是**一章的任务**：这一章玩家要干什么、做完算完成、没做完会怎样。

与旧生成器最重要的差别在**输入**：章节卡是在玩家选定了走向之后才生成的，
所以生成器看得到「玩家已经做了什么、现在队伍什么水平、上一章的余波」，
而不只是世界地图。

纪律不变：**程序算得出来的绝不交给模型**。骨架（这一章是什么类型）、
钩子地点、目标 CR、经验预算、元素配额、去重窗口都由 :mod:`...dnd.chapter` 与
:mod:`...dnd.encounter_budget` 算好塞进来；模型只写「什么地方、什么人、
什么场面」这些不可计算的部分。输出用 ``chapter-card`` 围栏包着的 JSON，
解析复用 :func:`...dnd.chapter.text_to_card`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...dnd.chapter import ChapterCard, text_to_card
from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = [
    "ChapterAgent",
    "ChapterResult",
    "chapter_agent",
]


_SYSTEM_PROMPT = """# 任务
根据给定的章节骨架、钩子地点与上下文，写出一张 5e 跑团的**章节卡**。
章节卡只写「什么地方、什么人、什么场面」——难度数值、地点候选、章号都由系统给定，不要改动也不要复述。

# 输入
- **世界档案与圣经**：世界的专有名词（人名、势力名、地名）以它为准。已有名字可以直接用。
- **骨架**：这一章的类型（调查 / 潜入 / 守点 / 仪式 / 对峙 / 交涉）与大致走向。
- **钩子地点**：这一章的地点**只能**从给定的候选里挑，不得新增地名。
- **难度**：目标 CR 与本章经验预算（只读，不要写进正文，也不要暗示具体数字）。
- **上下文**：已完成的章节与已知线索——用来承接余波、避免重复。

# 输出
一个 ````chapter-card` 围栏，里面是一段 JSON，字段如下：

```json
{
  "title": "章节标题（12 字以内，有具体画面，不要写「第 N 章」）",
  "goal": "这一章玩家要干成什么（一句话，不预设解法）",
  "entry": "玩家是怎么被卷进来的（一句话）",
  "locations": ["从钩子地点里挑的 1-2 个"],
  "npcs": ["在场人物的名字，0 或 1 个"],
  "objectives": [
    {"text": "一条可勾选的完成条件", "kind": "clue", "ref": "判定依据"},
    {"text": "另一条完成条件", "kind": "narrative", "ref": ""}
  ],
  "fail_forward": "这一章没做完会怎样（一句话，写清后果落在谁身上）"
}
```

# 字段规则
- `locations`：**只能**从「钩子地点」给定的候选里选，1 到 2 个，一个都不能是候选之外的名字。
- `npcs`：0 或 1 个人名。新名字要与已有专有名词不重名；没有合适的人就写空数组，不要硬造。
- `objectives`：2 到 4 条。`kind` 只能是 `clue` / `item` / `defeat` / `narrative`：
  - `clue`：拿到某条线索就算达成，`ref` 写线索关键词；
  - `item`：拿到某件东西就算达成，`ref` 写物品名；
  - `defeat`：击败某个目标就算达成，`ref` 写目标名；
  - `narrative`：需要 DM 判定的描述性目标，`ref` 留空字符串。
  每条 `text` 要能被玩家一眼看懂「做成什么样算完成」。
- `fail_forward`：必须有。写清这一章没做完时**事情会怎样继续走**（谁会接着动作、后果是什么），
  不要写成「故事继续」这种空话。

# 内容要求
- **不预设解法**：`goal` 与每条 `objectives` 只说目标，不规定路径；同一个目标至少留三种做法。
- **不强制剧情**：不要求玩家必须去某个地方、杀某个人、救某个人；不写「唯一结局」。
- **承接上文**：上下文里已完成的章节与已知线索要在这一章里有回响（对手升级、欠账上门、旧线索兑现）。
- **具体**：写「盐仓码头夜班交接时」，不要写「某个港口」。
- **只用已有专有名词**：人名、地名、势力名一律取自世界档案与圣经；实在需要新人物时只新加这一个名字。
- 不写难度等级、不写「本应是」「按照剧情」「为了推动发展」这类作者口吻。
- 语言简洁，每字段一到两句。
"""


@dataclass(slots=True)
class ChapterResult(AgentResult):
    """章节卡生成结果：模型原文 + 解析出的卡。"""

    #: 解析出来的章节卡；解析失败或校验不通过时为空
    card: ChapterCard | None = None
    #: 解析失败的原因（供上层决定重试还是放弃）
    problems: list[str] = field(default_factory=list)


class ChapterAgent(TrpgAgent):
    """章节卡生成：把一段走向写成一张可勾选完成条件的任务卡。"""

    name = "chapter"
    title = "章节卡生成"
    description = "根据章节骨架与钩子地点写出一章的任务卡（目标 / 完成条件 / 失败前进）"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput("world", "世界档案与圣经", description="专有名词的来源"),
        AgentInput("arc", "章节骨架", description="这一章的类型与走向"),
        AgentInput("locations", "钩子地点", description="可用的地点候选（只能用这些）"),
        AgentInput(
            "difficulty",
            "难度",
            required=False,
            description="目标 CR 与本章预算（只读，不要写进正文）",
        ),
        AgentInput(
            "context",
            "上下文",
            required=False,
            description="已完成的章节、已知线索、队伍概况",
        ),
    )

    async def run(self, **values: object) -> ChapterResult:
        result = await super().run(**values)
        card = text_to_card(result.content)
        problems: list[str] = []
        if card is None:
            problems.append("模型没有给出可解析的 chapter-card 围栏")
            card = ChapterCard()
        else:
            if not card.objectives:
                problems.append("章节卡没有任何完成条件")
            if not card.fail_forward.strip():
                problems.append("章节卡缺 fail_forward")
        return ChapterResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            card=card,
            problems=problems,
        )


#: 全局默认实例
chapter_agent = ChapterAgent()
