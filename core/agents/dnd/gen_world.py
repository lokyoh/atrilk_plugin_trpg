"""跑团 Agent：世界地图生成。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」中的「生成地图」环节，
提示词精简自 ``.agents/trpg/generate_world.md``。

产出三份内容：

- **世界档案**：世界名称与一句话简介（供开局选择列表展示）；
- **地区描述文档**：依次介绍各个地区，供后续「生成故事 / 生成区域」使用；
- **相邻数据文档**：地区之间陆地相邻关系的 JSON，置于描述文档底部。

除模型原文外，本 agent 还解析出结构化的地区列表、相邻关系与世界档案
（:class:`WorldMapResult`）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ATRI.log import log

from ...dnd.world_bible import Entity, entities_from_json
from ...dnd.world_store import clean_name
from ...dnd.world_topology import (
    MAX_REGIONS,
    MIN_REGIONS,
    Topology,
    build_topology,
    lint_world,
    prompt_text,
)
from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = [
    "WorldAgent",
    "WorldMapResult",
    "parse_region_names",
    "parse_world_map",
    "parse_world_profile",
    "world_agent",
]


_SYSTEM_PROMPT = """# 任务
为跑团生成一张世界地图。**地理由系统给定**：地区数量、顺序、方位与相邻关系都在下面的「地区布局」里，你不许改动，只负责给每个地区起名、写设定。

# 写作顺序
按「地区布局」列出的顺序，一个地区写一节：`## 地区一：<你起的名字>`，依次到最后一个。
- 名字要贴合世界基调、彼此风格统一（2~4 字或音译词组），不要重名，不要照抄「地区一」这类占位名。
- 每节固定八个小节：大致形状 / 主要地形 / 标志地标 / 国都 / 特殊地区 / 人文特点 / 政治局势 / 与周边区域的分隔方式。
- 「与周边区域的分隔方式」**只能**写布局里给它的那几个邻居，逐一说明边界地形或海域，不许新增邻接、也不许漏掉。
- 各地区之间的历史、贸易与矛盾要互相呼应（谁跟谁有旧账、谁卡着谁的路），不要写成互不相干的独立小国。

# 世界档案
文档最前面写世界名称与一句话简介（不超过 30 字，概括风格与最大看点）。

# 末尾结构化索引（必须）
正文之后再给一段 JSON，内容必须与正文一致：

```json
{"world_name": "<世界名称>", "intro": "<一句话简介>", "regions": ["<地区一的名字>", "<地区二的名字>"], "entities": [{"name": "<专有名词>", "kind": "person|faction|item", "region": "<所属地区>", "note": "<不超过20字>"}]}
```

- `regions` 的顺序与长度必须和「地区布局」完全一致——系统只认这段 JSON 里的名字，写错会让整张地图错位。
- `entities` 是**实体登记**：只收人物 / 势力 / 物品这类跨地区也成立的专有名词（地区与地标系统会自动收录，不要重复列），每条带所属地区与一句定位；没有就空数组 `[]`。
  这些名字会进世界圣经，之后所有生成与主持都以它为准——写错一个名字，整局都会跟着错。

# 输出模板

```markdown
# 世界档案
- 世界名称：<名称>
- 一句话简介：<一句话>

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

## 地区二：<地区名称>

（同上，按布局顺序写完所有地区）

```json
{"world_name": "...", "intro": "...", "regions": ["...", "..."], "entities": [{"name": "...", "kind": "person", "region": "...", "note": "..."}]}
```
```
"""

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
    """世界地图生成结果：模型原文 + 解析出的地区、相邻关系与世界档案。"""

    #: 地区名列表（按文档顺序）
    regions: list[str] = None  # type: ignore[assignment]
    #: 陆地相邻关系：地区名 -> 相邻地区名列表
    adjacency: dict[str, list[str]] = None  # type: ignore[assignment]
    #: 世界名称（世界档案；解析失败为空串）
    world_title: str = ""
    #: 一句话简介（世界档案；解析失败为空串）
    world_intro: str = ""
    #: 实体登记（人物 / 势力 / 物品；来自末尾 JSON，地区与地标由程序另行播种）
    entities: list[Entity] = field(default_factory=list)
    #: 系统生成的拓扑（地区连续、邻接对称、无孤立点）；老调用方可以忽略
    topology: Topology | None = None
    #: 生成后自检发现的问题（名字数量不符 / 重名 / 邻接没写进正文……）
    warnings: list[str] = field(default_factory=list)

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


#: 世界档案里的名称 / 简介行
_WORLD_TITLE_RE = re.compile(
    r"^[>\-\s]*世界名称[：:]\s*(?P<value>.+?)\s*$", re.MULTILINE
)
_WORLD_INTRO_RE = re.compile(
    r"^[>\-\s]*一句话简介[：:]\s*(?P<value>.+?)\s*$", re.MULTILINE
)


def parse_world_profile(text: str) -> tuple[str, str]:
    """解析世界档案，返回 ``(世界名称, 一句话简介)``；缺失的部分为空串。

    名称 / 简介会剥掉首尾的包裹符号（如 ``**名称**``、``「名称」``），
    与世界库的 :func:`~...dnd.world_store.clean_name` 归一规则一致。
    """
    title_match = _WORLD_TITLE_RE.search(text or "")
    intro_match = _WORLD_INTRO_RE.search(text or "")
    return (
        clean_name(title_match.group("value")) if title_match else "",
        clean_name(intro_match.group("value")) if intro_match else "",
    )


#: 任意二级标题（地区小节的兜底解析用）
_ANY_HEADING_RE = re.compile(r"^##\s+(?P<text>[^\n#].+?)\s*$", re.MULTILINE)

#: 世界档案里的名称 / 简介也可能放在末尾 JSON 里
_PROFILE_JSON_KEYS = ("world_name", "intro")


def _fit_names(names: list[str], count: int) -> list[str]:
    """把名字补齐 / 截断到拓扑给的数量，并保证不重名。"""
    fixed: list[str] = []
    for index in range(count):
        candidate = names[index] if index < len(names) else f"未命名之地{index + 1}"
        if candidate in fixed:
            candidate = f"{candidate}·{index + 1}"
        fixed.append(candidate)
    return fixed


def parse_region_names(text: str, count: int) -> tuple[list[str], bool]:
    """按顺序取地区名：**优先末尾 JSON 的 ``regions``**，没有再退回小标题。

    :return: ``(地区名列表, 是否来自 JSON)``
    """
    count = max(1, int(count))
    for block in _JSON_BLOCK_RE.findall(text or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("regions"), list):
            continue
        names = [clean_name(str(item)) for item in data["regions"]]
        names = [name for name in names if name]
        if names:
            return _fit_names(names, count), True

    names = []
    for match in _ANY_HEADING_RE.finditer(text or ""):
        raw = match.group("text").strip()
        if any(word in raw for word in ("世界档案", "地图", "相邻", "索引")):
            continue
        if re.search(r"[：:]", raw):
            raw = re.split(r"[：:]", raw, maxsplit=1)[-1]
        name = clean_name(raw)
        if name and name not in names:
            names.append(name)
    return _fit_names(names, count), False


def _profile_from_json(text: str) -> tuple[str, str]:
    """从末尾 JSON 里取 ``(世界名称, 一句话简介)``；没有返回两个空串。"""
    for block in _JSON_BLOCK_RE.findall(text or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and any(key in data for key in _PROFILE_JSON_KEYS):
            return (
                clean_name(str(data.get("world_name", ""))),
                clean_name(str(data.get("intro", ""))),
            )
    return "", ""


def _region_count(value: object, default: int = 6) -> int:
    """把「6」「六个」这类输入转成地区数量，限制在拓扑允许的范围内。"""
    match = re.search(r"\d+", str(value or ""))
    count = int(match.group()) if match else default
    return max(MIN_REGIONS, min(MAX_REGIONS, count))


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
            description="期望生成的地区数量（3~12），可留空",
        ),
        AgentInput(
            "layout",
            "地区布局",
            required=False,
            description="系统给定的地区数量、顺序、方位与相邻关系；模型照它写，不得改动",
        ),
    )

    def build_prompt(self, values) -> str:
        prompt = super().build_prompt(values)
        if prompt:
            return prompt
        return "请为跑团生成一个完整的世界地图。"

    async def run(self, **values: object) -> WorldMapResult:
        # 先由程序定下地理：地区数量、连续地块、对称邻接（模型不该也不擅长做这件事）
        count = _region_count(values.get("region_count"))
        topology = build_topology(count)
        values = {**values, "layout": prompt_text(topology)}

        result = await super().run(**values)

        names, from_json = parse_region_names(result.content, topology.count)
        if not from_json:
            log.info("世界地图没有可用的 JSON 索引，已按小节标题回退取地区名")
        adjacency = {
            names[index]: [names[i] for i in topology.neighbors(index)]
            for index in range(topology.count)
        }

        world_title, world_intro = _profile_from_json(result.content)
        if not world_title and not world_intro:
            world_title, world_intro = parse_world_profile(result.content)

        warnings = lint_world(topology, names, result.content)
        for item in warnings:
            log.warning(f"世界地图自检：{item}")

        return WorldMapResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            regions=names,
            adjacency=adjacency,
            world_title=world_title,
            world_intro=world_intro,
            entities=entities_from_json(result.content),
            topology=topology,
            warnings=warnings,
        )


#: 全局默认实例
world_agent = WorldAgent()
