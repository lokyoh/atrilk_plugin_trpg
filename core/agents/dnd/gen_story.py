"""跑团 Agent：世界起点与推力设定（story）。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」中的「生成故事」环节，
提示词精简自 ``.agents/trpg/generate_story.md``。

产出「故事档案 + 世界起点与推力设定」文档：档案给出故事名称与一句话简介
（供开局选择列表展示），正文明确世界的初始状态与持续推动故事的压力来源，
但不预设玩家选择、剧情顺序与唯一结局。一个世界里可以有多个故事（各自一份
``story`` 文档）；落库由调用方负责（世界库 ``story`` / 自动生成的故事键）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...dnd.world_store import clean_name
from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = ["StoryAgent", "StoryResult", "parse_story_profile", "story_agent"]


_SYSTEM_PROMPT = """# 任务
根据已有的世界地区描述文档，生成一份「世界起点与推力设定」文档，用于指导 DM 在跑团中明确世界的初始状态与持续推动故事发展的压力来源，但不预设玩家选择、剧情顺序与唯一结局。

# 输入
世界地区描述文档：各地区的大致形状、主要地形、标志地标、国都、特殊地区、人文特点、政治局势、与周边区域的分隔方式。

# 输出
一份 Markdown 文档：先是一节「故事档案」，再是七节设定——世界起点、世界推力、势力动机、事件触发条件、关键节点、结局倾向、Agent 使用原则。

# 输出模板
```markdown
# 故事档案
- 故事名称：<给这个故事起一个名字>
- 一句话简介：<不超过 30 字，点出核心冲突或悬念>

# 世界起点与推力设定

## 一、世界起点
### 时间背景
### 整体格局
### 近期大事
### 未解悬念
### 玩家切入点

## 二、世界推力
### 主要威胁
### 威胁来源
### 威胁进度
### 影响范围
### 紧迫程度

## 三、势力动机
### 势力一：<名称>
- 所在地：
- 核心目标：
- 当前行动：
- 对玩家的态度：
- 与其他势力的关系：
- 不干预后果：
（可重复多个势力）

## 四、事件触发条件
### 事件一：<名称>
- 触发条件：
- 前置条件：
- 涉及势力：
- 可能后果：
- 玩家影响方式：
（可重复多个事件）

## 五、关键节点
### 节点一：<名称>
- 所在地区：
- 节点内容：
- 与推力的关系：
- 可能作用：
- 多种通过方式：
（可重复多个节点）

## 六、结局倾向
### 倾向一：<名称>
- 形成条件：
- 主要特征：
- 受益方：
- 受损方：
- 玩家角色可能的位置：
（可重复多个倾向）

## 七、Agent 使用原则
- 准备局势，不准备剧本：写清谁想要什么、谁在做什么、不干预会怎样。
- 使用「如果……就……」结构，让世界对玩家行为做出反应。
- 让 NPC 独立行动，NPC 按自身计划推进，不等待玩家。
- 接受玩家改写故事：可以扶持新势力、推翻旧秩序、创造新结局。
- 用掷骰决定不确定结果，不因「剧情需要」篡改骰子。
- 保留多条出路，每个问题至少三种可能解法。
- 不强制剧情保护，不为让某 NPC 存活或某事件发生而否定玩家行动。
- 不预设玩家选择，不规定玩家必须去某地、杀某人、救某人。
- 不设定唯一结局，允许谈判、逃跑、牺牲、妥协、意外结局。
- 保持公平、一致、连贯。
```

# 生成要求
- 故事档案放最前面：故事名称与一句话简介各一行，简介点出这一局的核心冲突或悬念。
- 只描述「现在是什么样」，不描述「接下来必须怎样」；起点要足够具体、又留白供玩家行动。
- 推力是「压力」不是「剧本」；写清威胁进度、影响范围与紧迫程度。
- 所有内容必须基于已有世界信息，不新增未提及的地区或势力；缺少的细节可合理推断，但须保持一致。
- 不预设玩家选择、剧情顺序与唯一结局。
- 语言简洁、结构化，可直接用于 DM 主持。"""


#: 故事档案里的名称 / 简介行
_STORY_TITLE_RE = re.compile(
    r"^[>\-\s]*故事名称[：:]\s*(?P<value>.+?)\s*$", re.MULTILINE
)
_STORY_INTRO_RE = re.compile(
    r"^[>\-\s]*一句话简介[：:]\s*(?P<value>.+?)\s*$", re.MULTILINE
)


def parse_story_profile(text: str) -> tuple[str, str]:
    """解析故事档案，返回 ``(故事名称, 一句话简介)``；缺失的部分为空串。

    名称 / 简介会剥掉首尾的包裹符号（如 ``**名称**``、``「名称」``），
    与世界库的 :func:`~...dnd.world_store.clean_name` 归一规则一致。
    """
    title_match = _STORY_TITLE_RE.search(text or "")
    intro_match = _STORY_INTRO_RE.search(text or "")
    return (
        clean_name(title_match.group("value")) if title_match else "",
        clean_name(intro_match.group("value")) if intro_match else "",
    )


@dataclass(slots=True)
class StoryResult(AgentResult):
    """故事生成结果：模型原文 + 故事档案解析出的名称与简介。"""

    #: 故事名称（故事档案；解析失败为空串）
    story_title: str = ""
    #: 一句话简介（故事档案；解析失败为空串）
    story_intro: str = ""


class StoryAgent(TrpgAgent):
    """世界起点与推力设定：产出故事的初始状态与持续压力。"""

    name = "story"
    title = "世界起点与推力设定"
    description = "根据世界地区文档，生成世界起点、推力、势力动机与结局倾向"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput(
            "world", "世界地区描述文档", description="世界地图生成产出的地区描述文档"
        ),
        AgentInput(
            "theme",
            "额外要求",
            required=False,
            description="希望强调的基调 / 风格，可留空",
        ),
    )

    async def run(self, **values: object) -> StoryResult:
        result = await super().run(**values)
        story_title, story_intro = parse_story_profile(result.content)
        return StoryResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            story_title=story_title,
            story_intro=story_intro,
        )


#: 全局默认实例
story_agent = StoryAgent()
