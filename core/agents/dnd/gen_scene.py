"""跑团 Agent：场景详细生成（scene）。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」，
提示词精简自 ``.agents/trpg/generate_sence.md``（文件名沿用源文件的 sence 拼写）。

根据输入的三种地点之一（地形子区域 / 城镇据点 / 地下城），生成该地点的具体场景文档，
为实际跑团提供直接可用的描述：场景画面、可感知信息、可交互元素、可能发生的事。
落库由调用方负责（世界库 ``scene`` / 场景名）。

**场景可以是区域，也可以是场所里的一个房间**（2026-10-22 改）。原先只有
「一个区域」一种，房间一律由 agent 临场描绘；但官方模组（风骸岛之龙）那种
「一间舱房一份文档、每间都有写死的遭遇与宝藏清单」，压进一个区域就全丢了。
所以这一层现在两种都收：粒度由输入的地点类型与 ``kind`` 决定。
"""

from __future__ import annotations

import re

from ...dnd.world_bible import Entity
from ...dnd.world_store import clean_name
from ..base import AgentInput, TrpgAgent

__all__ = ["SceneAgent", "parse_scene_npcs", "scene_agent"]


#: 小标题（``### 在场者：<名称或标识>`` / ``### 元素：<名称>`` / ``### 事件：<名称>``）
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s*(?P<title>.+?)\s*$")

#: 在场者小节里的「- 类型：X」行
_KIND_FIELD_RE = re.compile(r"^[-*·]\s*类型\s*[：:]\s*(?P<value>.+?)\s*$")

#: 在场者小标题：``在场者：<名称或标识>``（冒号可省）
_PRESENTER_RE = re.compile(r"^在场者[^\n：:]{0,8}[：:]\s*(?P<name>.+?)$")


_SYSTEM_PROMPT = """# 任务
根据输入的三种地点之一（地形子区域、城镇据点、地下城），生成该地点的具体场景详细信息，为后续实际跑团场景提供直接可用的描述，包括：场景画面、可感知信息、可交互元素、可能发生的事。

**粒度：区域或房间都行。**
- 输入是**一整片区域**（地形子区域、城镇据点、地下城）→ 写这一片地方的样子，
  里面的具体房间留给 DM 临场描绘。
- 输入是**场所里的某个房间**（如「罗盘玫瑰号的主甲板」「修道院的图书馆」）
  → 就写这一间，写具体：布局、出口、藏东西的地方、谁在这儿。
  玩家站在这里时看到的就是这一间，不需要再往里细化。

# 输入
以下三种地点之一：
1. 地形子区域：位置范围、地形类型、海拔与地势、气候特征、主要地貌、水源分布、交通难度等。
2. 城镇据点：据点类型、地理位置、面积、人口、势力归属、势力结构、经济基础、军事力量、周边关系、特殊之处等。
3. 地下城：位置、类型、简介、结构、魔物分布、陷阱与机关、宝藏与资源、势力介入、危险等级等。

# 输出
一份 Markdown 文档，含十一节：场景概览、第一印象、可感知信息、场景布局、可交互元素、
在场者、可能发生的事、**遭遇设计**、隐藏信息、场景变化、**宝藏清单**。

后三节是为「别人的模组转进来」加的（2026-10-22）：

- **遭遇设计**：这场遭遇长什么样（对手、规模、难点、退路），而不是只写「可能发生的事」；
- **宝藏清单**：这一场有哪些东西可拿、值多少、藏在哪儿；
- **在场者**里带**台词**：官方模组给 NPC 写了整段台词（读给玩家听的引文），
  那是模组最贵的资产，压缩成「态度：友善」就全丢了。

# 输出模板
```markdown
# 场景详细文档：<场景名称>

## 一、场景概览
- 场景名称：
- 地点类型：
- 所在位置：
- 一句话定位：
- 场景功能：

## 二、第一印象
- 视觉：
- 听觉：
- 嗅觉：
- 触觉：
- 整体氛围：
- 直觉：

## 三、可感知信息
- 可见物：
- 可听声：
- 可闻味：
- 可触物：
- 异常之处：
- 动态信息：

## 四、场景布局
- 整体形状：
- 边界与出口：
- 主要区域：
- 通道与路径：
- 高低差：
- 遮蔽物：
- 光源分布：
- 地形特征：
- 关键位置：

## 五、可交互元素
### 元素：<名称>
- 类型：
- 位置：
- 外观：
- 交互方式：
- 可能结果：
- 关联信息：
- 隐藏内容：
（可重复多个元素）

## 六、在场者
### 在场者：<名称或标识>
- 类型：
- 数量：
- 位置：
- 外观：
- 当前行为：
- 态度：
- 可交流性：
- 台词：（读给玩家听的原话，一两句就够；没有就写「无」）
- 关联信息：
（可重复多个在场者）

## 七、可能发生的事
### 事件：<名称>
- 触发条件：
- 事件内容：
- 涉及元素：
- 涉及在场者：
- 可能走向：
- 持续时间：
- 后续影响：
（可重复多个事件）

## 八、遭遇设计
### 遭遇：<名称>
- 对手：
- 数量与规模：
- 战斗环境：
- 难点：
- 可选打法：
- 撤退路线：
- 战利品：
- 与其他遭遇的关系：
（可重复多个遭遇；没有遭遇就写「无」）

## 九、隐藏信息
- 隐藏内容：
- 隐藏方式：
- 发现方式：
- 发现难度：
- 关联信息：
- 可能影响：

## 十、场景变化
- 时间变化：
- 事件变化：
- 角色行为变化：
- 在场者变化：
- 环境变化：
- 状态变化：

## 十一、宝藏清单
### 宝物：<名称>
- 位置：
- 外观：
- 取走方式：
- 价值：
- 谁在看着它：
- 关联信息：
（可重复多个宝物；没有就写「无」）
```

# 生成要求
- 所有内容必须基于输入的地点信息；不新增未提及的国家、地区或势力。
- 场景描述需具体、可直接使用。
- 可交互元素、在场者、事件、隐藏信息之间需逻辑自洽。
- 场景变化需有逻辑、与已有设定一致，不预设角色必须触发某变化。
- 语言简洁、结构化，可直接用于实际跑团场景。"""


class SceneAgent(TrpgAgent):
    """场景详细生成：产出可直接使用的场景文档。"""

    name = "scene"
    title = "场景详细生成"
    description = "根据地形 / 据点 / 地下城，生成场景画面、可交互元素与可能发生的事"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput(
            "location",
            "地点信息",
            description="地形子区域 / 城镇据点 / 地下城 之一的基础数据",
        ),
        AgentInput(
            "kind",
            "地点类型",
            required=False,
            description="地形子区域 / 城镇据点 / 地下城，可留空由模型推断",
        ),
        AgentInput(
            "context",
            "上下文",
            required=False,
            description="世界推力 / 所在地区等信息，供保持一致，可留空",
        ),
        AgentInput(
            "bible",
            "世界圣经",
            required=False,
            description="世界专有名词与事实的登记；命名与事实以它为准，可留空",
        ),
    )


#: 全局默认实例
scene_agent = SceneAgent()


#: 占位式的「在场者」写法，不是名字：转模组时没数据的那几节写的就是
#: ``### 在场者：见正文``。登记进去等于教 DM 有个叫「见正文」的 NPC。
_PLACEHOLDER_NAMES = frozenset(
    {"见正文", "见下", "见上", "同上", "略", "无", "暂无", "待定", "同前"}
)


def parse_scene_npcs(text: str) -> list[Entity]:
    """从场景文档提取实体登记：在场者（「六、在场者」节）。

    小标题写法是提示词规定的固定格式（``### 在场者：<名称或标识>``），
    程序提取零成本、不会认错；备注取小节内的「- 类型：」行。
    标识类在场者（「盐仓看守」这类没名字的）同样登记——DM 之后说到他们时
    也得用同一个叫法。事件 / 可交互元素是一次性内容，不登记。
    占位写法（``见正文`` / ``暂无``）不是名字，跳过。
    """
    entities: list[Entity] = []
    in_section = False
    current: Entity | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and current.name:
            entities.append(current)
        current = None

    for line in (text or "").splitlines():
        head = _HEADING_RE.match(line)
        if head is None:
            if current is not None and not current.note:
                match = _KIND_FIELD_RE.match(line)
                if match is not None:
                    current.note = match.group("value")[:60]
            continue
        level = len(head.group("hashes"))
        title = head.group("title").strip()
        if level == 2:
            flush()
            in_section = "在场者" in title
            continue
        if level != 3 or not in_section:
            continue
        flush()
        match = _PRESENTER_RE.match(title)
        if match is None:
            continue
        name = clean_name(match.group("name"))
        if not name or len(name) > 20 or name in _PLACEHOLDER_NAMES:
            continue
        current = Entity(name=name, kind="person", source="scene")
    flush()
    return entities
