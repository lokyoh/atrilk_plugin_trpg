"""跑团 Agent：地区详细生成（area）。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」中的「生成区域」环节，
提示词精简自 ``.agents/trpg/generate_area.md``。

根据本地区信息与周边地区信息，生成一份地区文档，给出该地区的**基础数据**：
地形子区域、城镇据点、地下城与魔物分布、与周边地区的衔接。
这些基础数据是后续「地形（place）／据点（city）／地下城（dungeon）」文档的输入，
本 agent 只落实到基础数据，不展开深入细节。

除模型原文外，本 agent 还解析出子区域 / 据点 / 地下城名称（:class:`AreaResult`）。
落库由调用方负责（世界库 ``area`` / 地区名）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = ["AreaAgent", "AreaResult", "area_agent", "parse_area"]


_SYSTEM_PROMPT = """# 任务
根据本地区信息与周边地区信息，为指定地区生成一份地区文档，给出该地区的基础数据，供后续生成地形 / 据点 / 地下城与建立场景使用。

# 输入
- 本地区信息：该地区的大致形状、主要地形、标志地标、国都、特殊地区、人文特点、政治局势、与周边区域的分隔方式。
- 周边地区信息：与该地区相邻的陆地地区及海洋分隔地区的相关信息。

# 输出
一份 Markdown 文档，含六节：地区概览、详细地形分布、城镇据点、地形地貌生态、地下城与魔物分布、与周边地区的衔接。

# 输出模板
```markdown
# <地区名称>

## 一、地区概览
- 地区名称：
- 大致形状：
- 主要地形：
- 国都：
- 特殊地区：
- 当前政治局势：
- 在世界推力中的位置：

## 二、详细地形分布
### 子区域：<名称>
- 位置范围：
- 地形类型：
- 海拔与地势：
- 气候特征：
- 主要地貌：
- 水源分布：
- 与周边子区域的过渡：
- 交通难度：
（可重复多个子区域）

## 三、城镇据点
### 据点：<名称>
- 据点类型：
- 地理位置：
- 面积：
- 人口：
- 势力归属：
- 势力结构：
- 经济基础：
- 军事力量：
- 与周边的关系：
- 特殊之处：
（可重复多个据点）

## 四、地形地貌生态
### 子区域：<名称>
- 植被类型：
- 主要植物：
- 主要动物：
- 魔物分布：
- 生态链：
- 季节变化：
- 资源产出：
- 生态威胁：
- 与人文的关系：
（可重复多个子区域）

## 五、地下城与魔物分布
### 地下城：<名称>
- 位置：
- 类型：
- 简介：
- 结构：
- 魔物分布：
- 陷阱与机关：
- 宝藏与资源：
- 势力介入：
- 危险等级：
（可重复多个地下城）

## 六、与周边地区的衔接
### <相邻地区>
- 边界地形过渡：
- 生态变化：
- 交通线与关隘：
- 人文与政治互动：

### <海洋分隔地区>
- 海域名称与特征：
- 海峡、群岛、航线：
- 海上交通与威胁：

### 特殊分隔方式
- 类型：
- 对通行的影响：
（可重复多个周边地区）
```

# 生成要求
- 只落实到**基础数据**（子区域 / 据点 / 地下城的字段），深入细节留给后续「地形 / 据点 / 地下城」文档，不要展开。
- 子区域划分需覆盖该地区全境，子区域之间地形过渡合理。
- 与已有标志地标、国都、特殊地区的位置保持一致，与周边地区的地形衔接自然。
- 据点分布需与地形、水源、交通线、资源分布合理对应；人口与面积匹配类型；势力归属与地区政治局势一致。
- 地下城分布需与地形、历史、势力活动合理对应；魔物分布与地下城类型、层数、危险等级一致。
- 不新增未提及的国家、地区或势力；地形、生态、城镇、地下城之间需逻辑自洽。
- 语言简洁、结构化，可直接用于后续生成。"""


#: 文档里出现的子区域 / 据点 / 地下城小标题，如 ``### 子区域：北境``
_SUB_AREA_RE = re.compile(
    r"^#{2,4}\s*子区域[^\n：:]{0,8}[：:]\s*(?P<name>.+?)\s*$", re.MULTILINE
)
_SETTLEMENT_RE = re.compile(
    r"^#{2,4}\s*据点[^\n：:]{0,8}[：:]\s*(?P<name>.+?)\s*$", re.MULTILINE
)
_DUNGEON_RE = re.compile(
    r"^#{2,4}\s*地下城[^\n：:]{0,8}[：:]\s*(?P<name>.+?)\s*$", re.MULTILINE
)


def _names(pattern: re.Pattern[str], text: str) -> list[str]:
    return list(
        dict.fromkeys(m.group("name").strip() for m in pattern.finditer(text or ""))
    )


def parse_area(text: str) -> tuple[list[str], list[str], list[str]]:
    """解析地区文档，返回 ``(子区域列表, 据点列表, 地下城列表)``。"""
    return (
        _names(_SUB_AREA_RE, text),
        _names(_SETTLEMENT_RE, text),
        _names(_DUNGEON_RE, text),
    )


@dataclass(slots=True)
class AreaResult(AgentResult):
    """地区生成结果：模型原文 + 解析出的子区域 / 据点 / 地下城名称。"""

    #: 地形子区域名列表
    sub_areas: list[str] = None  # type: ignore[assignment]
    #: 城镇据点名列表
    settlements: list[str] = None  # type: ignore[assignment]
    #: 地下城名列表
    dungeons: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sub_areas is None:
            self.sub_areas = []
        if self.settlements is None:
            self.settlements = []
        if self.dungeons is None:
            self.dungeons = []


class AreaAgent(TrpgAgent):
    """地区详细生成：产出该地区的基础数据文档。"""

    name = "area"
    title = "地区详细生成"
    description = "根据地区与周边信息，生成子区域 / 据点 / 地下城的基础数据"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput("region", "本地区信息", description="该地区的大致情况"),
        AgentInput(
            "neighbors",
            "周边地区信息",
            required=False,
            description="相邻的陆地 / 海洋分隔地区信息，可留空",
        ),
        AgentInput(
            "world",
            "世界上下文",
            required=False,
            description="世界名称 / 一句话简介与相邻地区的设定，供保持一致，可留空",
        ),
        AgentInput(
            "bible",
            "世界圣经",
            required=False,
            description="世界专有名词与事实的登记；命名与事实以它为准，可留空",
        ),
    )

    async def run(self, **values: object) -> AreaResult:
        result = await super().run(**values)
        sub_areas, settlements, dungeons = parse_area(result.content)
        return AreaResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            sub_areas=sub_areas,
            settlements=settlements,
            dungeons=dungeons,
        )


#: 全局默认实例
area_agent = AreaAgent()
