"""世界圣经与实体登记（跨文档、跨轮次的一致性锚点）。

世界地图、故事、区域、场景由**不同的模型调用**生成，DM 叙述又是另一些调用——
各写各的，同一个人很容易在这份文档里叫「商承弼」、那份里叫「沈万三」，
或者 DM 说着说着给盐务公会换了个总部。世界圣经就是为此存在的**唯一事实源**：

- **实体登记**：世界里的专有名词（人物 / 势力 / 地名 / 物品）各记一条——
   canonical 名、别名、所属地区、一句话备注、出处。按 ``(name, kind)`` 去重合并。
- **世界圣经要点**：世界名称、简介、地区清单等不易变的事实。

落盘为 ``worlds/<world_key>/bible.json``（与 ``index.json`` 并列，同属世界目录）：

- **播种**（零模型成本）：:func:`seed_from_world` 从世界库里程序提取——
  地区表（索引）、「」地标、国都、特殊地区；
- **补充**（搭生成的便车）：世界地图 agent 的末尾 JSON 带 ``entities``
  （人物 / 势力这些自由文本里提不出来的），故事文档按固定小标题程序提取；
- **消费**：:func:`bible_text` 生成一块有字数上限的紧凑文本，注入 DM 提示词与
  按需生成（区域 / 城镇 / 地点 / 地下城 / 场景）的输入——命名与事实以它为准。

设计取向：**能不调用模型就不调用**。程序能可靠提取的（索引、小标题、「」引文）
绝不麻烦模型；模型只在顺手输出结构化 JSON 时补上程序提不出来的人物与势力。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ATRI.log import log

from .world_store import (
    clean_name,
    get_world_map,
    load_doc,
    world_dir,
)

__all__ = [
    "BIBLE_FILENAME",
    "KIND_LABELS",
    "KIND_ORDER",
    "Entity",
    "WorldBible",
    "bible_path",
    "bible_text",
    "entities_from_json",
    "load_bible",
    "normalize_kind",
    "register_entities",
    "save_bible",
    "seed_from_world",
]

#: 圣经文件名（世界目录下，与 index.json 并列）
BIBLE_FILENAME = "bible.json"

#: 实体种类 -> 展示名
KIND_LABELS: dict[str, str] = {
    "person": "人物",
    "faction": "势力",
    "place": "地名",
    "item": "物品",
}

#: 提示词里的排列顺序（重要的在前）
KIND_ORDER: tuple[str, ...] = ("person", "faction", "place", "item")

#: 模型 / 文档里可能出现的种类写法 -> 规范种类
_KIND_ALIASES: dict[str, str] = {
    "person": "person",
    "人": "person",
    "人物": "person",
    "npc": "person",
    "faction": "faction",
    "势力": "faction",
    "组织": "faction",
    "派系": "faction",
    "org": "faction",
    "place": "place",
    "地点": "place",
    "地名": "place",
    "区域": "place",
    "地区": "place",
    "region": "place",
    "item": "item",
    "物品": "item",
    "道具": "item",
}

#: JSON 代码块（优先 json 标注，兼容无标注）
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

#: 「」引住的地标名，如 「千炉塔」
_QUOTED_RE = re.compile(r"「([^」]{1,20})」")

#: 世界地图文档里的小节名 -> 实体种类
_SECTION_KINDS: dict[str, str] = {"国都": "place", "特殊地区": "place"}

#: 国都小节里「这里没有城」的写法（跳过，别把「无城」登记成地名）
_NO_CAPITAL_RE = re.compile(r"^(无|没有|不曾)")


@dataclass(slots=True)
class Entity:
    """一个登记在册的专有名词。"""

    #: 规范名（展示与匹配都用这个）
    name: str
    #: 种类：person / faction / place / item
    kind: str = "place"
    #: 所属地区（世界地区名；跨地区或不明时为空）
    region: str = ""
    #: 一句话备注（身份 / 定位 / 与谁有关）
    note: str = ""
    #: 别名（模型或文档里的其他叫法）
    aliases: list[str] = field(default_factory=list)
    #: 出处（world / story-1 / scene:盐仓码头 ……）
    source: str = ""
    #: 是否可以给玩家看。剧情秘密（故事的「关键节点」等）登记为 ``False``，
    #: 不进玩家向的世界背景手册（见 :mod:`.world_brief`），只留在 DM 的提示词里。
    public: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "region": self.region,
            "note": self.note,
            "aliases": list(self.aliases),
            "source": self.source,
            "public": self.public,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entity:
        return cls(
            name=clean_name(str(data.get("name", ""))),
            kind=normalize_kind(data.get("kind")),
            region=clean_name(str(data.get("region", ""))),
            note=str(data.get("note", "") or "").strip()[:60],
            aliases=[
                clean_name(str(alias))
                for alias in (data.get("aliases") or [])
                if clean_name(str(alias))
            ],
            source=str(data.get("source", "") or ""),
            # 老存档没有这个字段，一律当公开（保持既有行为不变）
            public=bool(data.get("public", True)),
        )

    def line(self) -> str:
        """提示词里的一行：``名字｜种类｜地区｜备注``。"""
        parts = [self.name, KIND_LABELS.get(self.kind, self.kind)]
        if self.region:
            parts.append(self.region)
        if self.note:
            parts.append(self.note)
        return "｜".join(parts)


@dataclass(slots=True)
class WorldBible:
    """一个世界的圣经：要点 + 实体登记。"""

    world_key: str = ""
    #: 世界名称（解析自世界地图文档）
    world_name: str = ""
    #: 一句话简介
    intro: str = ""
    #: 不易变的事实（每条一行）
    facts: list[str] = field(default_factory=list)
    #: 实体登记（按 kind 优先级 + 登记顺序排列）
    entities: list[Entity] = field(default_factory=list)
    updated_at: float = 0.0

    def sorted_entities(self) -> list[Entity]:
        return sorted(
            self.entities,
            key=lambda e: (
                KIND_ORDER.index(e.kind) if e.kind in KIND_ORDER else len(KIND_ORDER),
            ),
        )


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #


def bible_path(world_key: str) -> Path:
    """某个世界的圣经文件路径（``worlds/<key>/bible.json``）。"""
    return world_dir(world_key) / BIBLE_FILENAME


def load_bible(world_key: str) -> WorldBible:
    """读某个世界的圣经；不存在或损坏时返回空圣经（不抛错）。"""
    path = bible_path(world_key)
    if not path.is_file():
        return WorldBible(world_key=world_key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取世界圣经失败：{path}（{e}）")
        return WorldBible(world_key=world_key)
    if not isinstance(data, dict):
        return WorldBible(world_key=world_key)
    entities = [
        Entity.from_dict(item)
        for item in (data.get("entities") or [])
        if isinstance(item, dict) and clean_name(str(item.get("name", "")))
    ]
    return WorldBible(
        world_key=world_key,
        world_name=clean_name(str(data.get("world_name", ""))),
        intro=clean_name(str(data.get("intro", ""))),
        facts=[str(item) for item in (data.get("facts") or []) if str(item).strip()],
        entities=entities,
        updated_at=float(data.get("updated_at", 0.0) or 0.0),
    )


def save_bible(world_key: str, bible: WorldBible) -> None:
    """写某个世界的圣经（原子替换，避免写一半）。"""
    path = bible_path(world_key)
    bible.world_key = world_key
    bible.updated_at = time.time()
    payload = {
        "world_name": bible.world_name,
        "intro": bible.intro,
        "facts": list(bible.facts),
        "entities": [entity.to_dict() for entity in bible.sorted_entities()],
        "updated_at": bible.updated_at,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# 登记与合并
# --------------------------------------------------------------------------- #


def normalize_kind(value: object) -> str:
    """把「人物 / 势力 / 组织」等写法归一成规范种类；认不出按地名。"""
    return _KIND_ALIASES.get(str(value or "").strip().lower(), "place")


def _merge(bible: WorldBible, entities: list[Entity], source: str) -> int:
    """把实体合并进圣经（按 name+kind 去重）；返回新增条数。"""
    added = 0
    index = {(entity.name, entity.kind): entity for entity in bible.entities}
    for item in entities:
        if not item.name:
            continue
        # 调用方给的出处更具体（如 story-1 / scene:盐仓码头），优先用它
        if source:
            item.source = source
        found = index.get((item.name, item.kind))
        if found is None:
            bible.entities.append(item)
            index[(item.name, item.kind)] = item
            added += 1
            continue
        # 已登记：补空字段、合并别名，不覆盖已有备注（先来者是正史）
        found.region = found.region or item.region
        found.note = found.note or item.note
        # 公开性取「更保守」的一侧：任一来源登记为秘密，就不再进玩家手册
        found.public = found.public and item.public
        for alias in item.aliases:
            if alias not in found.aliases:
                found.aliases.append(alias)
    return added


def register_entities(
    world_key: str,
    entities: list[Entity] | list[dict[str, Any]],
    *,
    source: str = "",
) -> int:
    """把一批实体登记进某个世界的圣经；返回新增条数（0 = 都在册或无有效实体）。

    ``entities`` 可以是 :class:`Entity`，也可以是字典（如模型 JSON 里拆出来的）。
    """
    items: list[Entity] = []
    for item in entities or []:
        if isinstance(item, Entity):
            items.append(item)
        elif isinstance(item, dict) and clean_name(str(item.get("name", ""))):
            items.append(Entity.from_dict(item))
    if not items:
        return 0
    bible = load_bible(world_key)
    added = _merge(bible, items, source)
    if added or not bible.entities:
        save_bible(world_key, bible)
    return added


def entities_from_json(text: str) -> list[Entity]:
    """从文本里的 JSON 代码块提取 ``entities`` 数组（模型顺手输出的实体登记）。"""
    for block in _JSON_BLOCK_RE.findall(text or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        raw = data.get("entities")
        if not isinstance(raw, list):
            continue
        entities: list[Entity] = []
        for item in raw:
            if isinstance(item, dict) and clean_name(str(item.get("name", ""))):
                entities.append(Entity.from_dict(item))
            elif isinstance(item, str) and clean_name(item):
                entities.append(Entity(name=clean_name(item)))
        if entities:
            return entities
    return []


# --------------------------------------------------------------------------- #
# 程序播种：从世界库里提取（零模型成本）
# --------------------------------------------------------------------------- #


def _section_name(section_text: str) -> str:
    """取小节正文的第一个名称（到第一个句读为止）；没有返回空串。"""
    text = section_text.strip()
    if not text:
        return ""
    cut = re.split(r"[。，！？；\n]", text, maxsplit=1)[0].strip()
    return clean_name(cut)


def seed_from_world(world_key: str, *, world_text: str = "") -> WorldBible:
    """从世界库程序化播种圣经：地区、地标、国都、特殊地区 + 世界档案要点。

    与已有圣经**合并**（模型登记的人物 / 势力不会被冲掉），可重复调用。
    """
    text = world_text or load_doc(world_key, "world", "main") or ""
    regions, _adjacency = get_world_map(world_key)

    # 延迟导入：presets / gen_world 都会用到本模块，不能在这里形成模块级环
    from ..agents.dnd.gen_world import parse_world_profile

    world_name, intro = parse_world_profile(text)

    bible = load_bible(world_key)
    bible.world_name = bible.world_name or world_name
    bible.intro = bible.intro or intro
    facts: list[str] = []
    if bible.world_name:
        facts.append(
            f"世界：{bible.world_name}" + (f"——{bible.intro}" if bible.intro else "")
        )
    if regions:
        facts.append(f"地区（{len(regions)}）：{'、'.join(regions)}")
    for item in facts:
        if item not in bible.facts:
            bible.facts.append(item)

    seeded: list[Entity] = []
    for name in regions:
        seeded.append(Entity(name=name, kind="place", note="地区", source="world"))
    for landmark in dict.fromkeys(_QUOTED_RE.findall(text)):
        if clean_name(landmark) not in {entity.name for entity in seeded}:
            seeded.append(
                Entity(name=clean_name(landmark), kind="place", source="world")
            )

    # 国都 / 特殊地区：逐地区小节找（小节名固定，取正文第一个名称）
    for region in regions:
        region_body = _slice_region(text, region)
        if not region_body:
            continue
        for section_title, kind in _SECTION_KINDS.items():
            body = _slice_section(region_body, section_title)
            name = _section_name(body)
            if not name:
                continue
            if section_title == "国都" and _NO_CAPITAL_RE.match(name):
                continue
            seeded.append(Entity(name=name, kind=kind, region=region, source="world"))

    _merge(bible, seeded, source="world")
    save_bible(world_key, bible)
    return bible


def _slice_region(world_text: str, region: str) -> str:
    """截出世界地图文档里某个地区的那一节（``## 地区N：<名>`` 到下一个地区为止）。"""
    pattern = re.compile(rf"^##\s*地区[^\n：:]{{0,8}}[：:]\s*{re.escape(region)}\s*$")
    lines = (world_text or "").splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if pattern.match(line):
            start = index
            break
    if start is None:
        return ""
    for index in range(start + 1, len(lines)):
        if re.match(r"^##\s", lines[index]):
            return "\n".join(lines[start + 1 : index]).strip()
    return "\n".join(lines[start + 1 :]).strip()


def _slice_section(region_body: str, title: str) -> str:
    """截出地区正文里某个小节（``### <title>``）到下一个 ``###`` 为止。"""
    lines = region_body.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(rf"^###\s*{re.escape(title)}\s*$", line):
            start = index
            break
    if start is None:
        return ""
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("###"):
            return "\n".join(lines[start + 1 : index]).strip()
    return "\n".join(lines[start + 1 :]).strip()


# --------------------------------------------------------------------------- #
# 提示词块
# --------------------------------------------------------------------------- #


def bible_text(world_key: str, *, max_chars: int = 0) -> str:
    """世界圣经的紧凑提示词块；没有内容返回空串。

    :param max_chars: 字数上限（0 = 不限制）。超限时从尾部丢实体——
        圣经是每轮 / 每次生成都要重发的，宁缺毋滥。
    """
    bible = load_bible(world_key)
    if not bible.entities and not bible.facts:
        return ""

    lines = ["# 世界圣经（专有名词与事实以这里为准，不要另起名字、不要矛盾）"]
    lines.extend(f"- {item}" for item in bible.facts)
    if bible.entities:
        lines.append("- 实体登记（名字｜种类｜地区｜备注）：")
        used = sum(len(line) for line in lines) + len(lines)
        for entity in bible.sorted_entities():
            row = f"· {entity.line()}"
            if max_chars and used + len(row) + 1 > max_chars:
                break
            lines.append(row)
            used += len(row) + 1
    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text
