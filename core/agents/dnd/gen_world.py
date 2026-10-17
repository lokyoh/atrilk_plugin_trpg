"""跑团 Agent：世界地图生成。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」中的「生成地图」环节，
提示词精简自 ``.agents/trpg/generate_world.md``。

产出两份内容：

- **地区描述文档**：依次介绍各个地区，供后续「生成故事 / 生成区域」使用；
- **相邻数据文档**：地区之间陆地相邻关系的 JSON，置于描述文档底部。

除模型原文外，本 agent 还解析出结构化的地区列表与相邻关系（:class:`WorldMapResult`）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ATRI.log import log

from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = ["WorldAgent", "WorldMapResult", "parse_world_map", "world_agent"]


_SYSTEM_PROMPT = """# 任务
为跑团生成一张世界地图，输出两个部分：
1. 地区描述文档：依次描述每个地区，供后续生成国家 / 区域数据使用。
2. 相邻数据文档：以 JSON 记录地区之间的陆地相邻关系，置于描述文档底部。

# 文档一：地区描述文档
对每个地区，按以下顺序依次描述：
1. 地区名称。
2. 大致形状：在地图上的轮廓形状（如狭长半岛、不规则多边形、环形、楔形等）。
3. 主要地形：平原、丘陵、山地、森林、沙漠、沼泽、高原、盆地、苔原等，并说明大致分布。
4. 标志地标：该区域最著名的自然或建筑地标（山脉、河流、湖泊、瀑布、峡谷、森林、火山、海湾；长城、巨塔、神殿、要塞、运河、大桥等），每个简要说明位置与特征。
5. 国都：名称、地理位置与基本特征。
6. 特殊地区：禁区、圣地、魔法荒原、古战场、贸易自由市等，说明其特殊之处。
7. 人文特点：主要民族、语言、宗教、文化习俗、经济形态等。
8. 政治局势：当前政治体制、权力结构、内外矛盾、盟友与敌人等。
9. 与周边区域的分隔方式：逐一说明与每个周边区域如何分隔——陆地相邻说明边界地形，海洋分隔说明海域 / 海峡 / 群岛，其他方式说明具体形式。

# 文档二：相邻数据文档
- 一个数组，数组中每个元素是一个字典。
- 字典的键为区域名称，值为该区域**陆地相邻**区域的名称列表。
- 只记录陆地相邻，海洋分隔不记入；无陆地相邻区域则值为空列表。
- JSON 置于描述文档底部，用 Markdown 代码块包裹，必须合法、可直接解析。
- 地区描述顺序与 JSON 中键名顺序一致。

# 输出格式
- 两个文档合在一起输出：描述文档在前，相邻数据 JSON 在后。
- JSON 使用 Markdown 代码块包裹。

# 输出模板
```markdown
# 地图地区描述文档

## 地区一：<地区名称>

### 大致形状
...

### 主要地形
...

### 标志地标
...

### 国都
...

### 特殊地区
...

### 人文特点
...

### 政治局势
...

### 与周边区域的分隔方式
...

---

## 地区二：<地区名称>

（同上，依次列出所有地区）

---

# 相邻数据

```json
[
  {
    "<地区一>": ["<陆地相邻地区A>", "<陆地相邻地区B>"]
  },
  {
    "<地区二>": ["<陆地相邻地区C>"]
  }
]
```
```"""

#: JSON 代码块（优先 json 标注，兼容无标注）
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

#: 地区描述文档里的小标题，如 ``## 地区一：北境``
_REGION_HEADING_RE = re.compile(
    r"^#{1,4}\s*地区[^\n：:]{0,8}[：:]\s*(?P<name>.+?)\s*$", re.MULTILINE
)


def _parse_adjacency(text: str) -> dict[str, list[str]]:
    """从文本里找出第一个合法的相邻关系 JSON。"""
    for block in _JSON_BLOCK_RE.findall(text or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        items: list[dict] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            items = [data]

        merged: dict[str, list[str]] = {}
        for item in items:
            for key, value in item.items():
                if isinstance(value, list):
                    merged[str(key)] = [str(v) for v in value]
                elif value is None:
                    merged[str(key)] = []
        if merged:
            return merged
    return {}


def _parse_regions(text: str, adjacency: dict[str, list[str]]) -> list[str]:
    """按出现顺序取地区名：优先文档小标题，缺省回退到相邻关系键名。"""
    names = [m.group("name").strip() for m in _REGION_HEADING_RE.finditer(text or "")]
    regions = [name for name in names if name]
    if not regions:
        regions = list(adjacency)
    return list(dict.fromkeys(regions))


@dataclass(slots=True)
class WorldMapResult(AgentResult):
    """世界地图生成结果：模型原文 + 解析出的地区与相邻关系。"""

    #: 地区名列表（按文档顺序）
    regions: list[str] = None  # type: ignore[assignment]
    #: 陆地相邻关系：地区名 -> 相邻地区名列表
    adjacency: dict[str, list[str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.regions is None:
            self.regions = []
        if self.adjacency is None:
            self.adjacency = {}


def parse_world_map(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """解析世界地图原文，返回 ``(地区名列表, 相邻关系)``。"""
    adjacency = _parse_adjacency(text)
    regions = _parse_regions(text, adjacency)
    return regions, adjacency


class WorldAgent(TrpgAgent):
    """世界地图生成：产出地区描述文档与相邻关系。"""

    name = "world"
    title = "世界地图生成"
    description = "生成各地区描述文档与陆地相邻关系数据"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput(
            "theme",
            "世界主题/基调",
            required=False,
            description="希望的世界风格与基调，可留空",
        ),
        AgentInput(
            "region_count",
            "地区数量",
            required=False,
            description="期望生成的地区数量，可留空",
        ),
    )

    def build_prompt(self, values) -> str:
        prompt = super().build_prompt(values)
        if prompt:
            return prompt
        return "请为跑团生成一个完整的世界地图。"

    async def run(self, **values: object) -> WorldMapResult:
        result = await super().run(**values)
        regions, adjacency = parse_world_map(result.content)
        if not regions and not adjacency:
            log.warning("世界地图生成结果未能解析出地区与相邻关系")
        return WorldMapResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            regions=regions,
            adjacency=adjacency,
        )


#: 全局默认实例
world_agent = WorldAgent()
