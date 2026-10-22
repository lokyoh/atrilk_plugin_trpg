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
  **额度按距离花**：调用方给一个 ``near``（玩家 / 这次生成对象所在的地方），
  只把同一个省、同一个王国、分不出地区的登记摆进来，别处的压成末行一句
  「另有 N 条未列出」。额度固定，装谁进来就是让 DM 看得见谁。

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


# --------------------------------------------------------------------------- #
# 距离：这个实体离「玩家现在在哪儿」有多远
# --------------------------------------------------------------------------- #

#: 距离档，越小越靠前。同一个省 → 同一个王国 → 分不出地区（多半是世界级设定）
#: → 邻国 → 别处。邻国排在前面的「分不出地区」之后，是因为这些登记本来往往
#: 就是邻国的人（名字里带「泊银湾收账人」这种），只是没填 region。
_DIST_SAME_PROVINCE = 0
_DIST_SAME_REALM = 1
_DIST_UNKNOWN = 2
_DIST_NEIGHBOUR = 3
_DIST_ELSEWHERE = 4

#: 截断时给「另有 N 条在别处」那行预留的字数（放不下就少放一条实体）
_OMITTED_RESERVE = 40


class _Atlas:
    """一次 :func:`bible_text` 调用里的地理索引（零模型成本，只读世界库的表）。

    三张表：省归属（``province_index``：场景 / 地点 → 省）、王国归属
    （``get_provinces``：省 → 王国）、邻接（``get_world_map``：省与省相邻）。

    实体落在哪儿，按**可靠性从高到低**依次试：

    1. ``region`` 字段（模型登记时会写，可能是王国也可能是省）；
    2. ``source`` 里带的那个名字（``scene:主甲板`` / ``province:剑湾``）——
       场景与地点能经 ``province_index`` 上溯到王国，比 region 更常见；
    3. 实体名本身（``凡达林`` 这种地名，名字就是它所在的地方）。

    三条都问不出来才当「分不出地区」——不猜。
    """

    def __init__(self, world_key: str, near: str = "") -> None:
        from .world_store import get_provinces, province_index

        self.world_key = world_key
        self.table = get_provinces(world_key)
        self.owner = {
            province: realm
            for realm, provinces in self.table.items()
            for province in provinces
        }
        self.places = province_index(world_key)
        self.province, self.realm = self._locate(clean_name(str(near or "")))
        self.neighbours = self._neighbours()

    # ---- 建表 ----

    def _locate(self, name: str) -> tuple[str, str]:
        """一个名字 → ``(省, 王国)``；认不出返回空串（不猜）。"""
        if not name:
            return "", ""
        if name in self.owner:
            return name, self.owner[name]
        if name in self.table:
            return "", name
        province = self.places.get(name, "")
        return province, self.owner.get(province, "")

    def _neighbours(self) -> frozenset[str]:
        """和当前王国相邻的王国（从**省邻接**推：我的省邻着谁的省就是邻着谁）。"""
        if not self.realm:
            return frozenset()
        adjacency = get_world_map(self.world_key)[1]
        found: set[str] = set()
        for province in self.table.get(self.realm, []):
            for other in adjacency.get(province, []):
                other_realm = self.owner.get(other, "")
                if other_realm and other_realm != self.realm:
                    found.add(other_realm)
        return frozenset(found)

    # ---- 查询 ----

    def place_of(self, entity: Entity) -> tuple[str, str]:
        """实体所在的 ``(省, 王国)``；三条线索都问不出来返回两个空串。"""
        source_key = str(entity.source or "").partition(":")[2]
        for name in (entity.region, source_key, entity.name):
            province, realm = self._locate(name)
            if province or realm:
                return province, realm
        return "", ""

    def distance(self, entity: Entity) -> int:
        province, realm = self.place_of(entity)
        if self.province and province == self.province:
            return _DIST_SAME_PROVINCE
        if self.realm and realm == self.realm:
            return _DIST_SAME_REALM
        if not province and not realm:
            return _DIST_UNKNOWN
        if realm and realm in self.neighbours:
            return _DIST_NEIGHBOUR
        return _DIST_ELSEWHERE

    def rank(self, entities: list[Entity]) -> list[Entity]:
        """排序：**种类**仍然是第一关键字（人物最容易写错名字，得排前面），
        同一个种类里按距离——眼前的在前，别的地区垫底。"""
        order = {kind: index for index, kind in enumerate(KIND_ORDER)}
        return sorted(
            entities,
            key=lambda entity: (
                order.get(entity.kind, len(KIND_ORDER)),
                self.distance(entity),
            ),
        )

    def split(self, entities: list[Entity]) -> tuple[list[Entity], list[Entity]]:
        """切成 ``(这一带的, 别处的)``。

        **位置本身不明时不切**（还没进场景、或这个世界还没分过省）：分不出远近
        就照单全收——保底宁可多给，不能把整本登记簿都当成「别处」丢掉。
        """
        if not (self.province or self.realm):
            return list(entities), []
        near: list[Entity] = []
        far: list[Entity] = []
        for entity in entities:
            if self.distance(entity) <= _DIST_UNKNOWN and not self._stray_place(entity):
                near.append(entity)
            else:
                far.append(entity)
        return near, far

    def _stray_place(self, entity: Entity) -> bool:
        """**定位不到的地名不是地名。**

        世界库里每一个真地名（王国 / 省 / 据点 / 地点 / 场景）都能经
        ``province_index`` 或王国表查到归属；查不到的多半是播种时从世界文档里
        把「」引住的短语当成了地标——「赎罪券」「一斗铁换一车石」「铁」都是
        这么进来的。它们每轮都占一行，还会让 DM 以为那里真有个叫「铁」的地方。
        """
        if entity.kind != "place" or not self.places:
            return False
        province, realm = self.place_of(entity)
        return not province and not realm

    def omitted_line(self, entities: list[Entity]) -> str:
        """没装下的那几条，汇总成一行：让 DM 知道登记簿比它看见的大，
        而不是以为「没列 = 不存在」，回头再编一个同名的。"""
        counts: dict[str, int] = {}
        for entity in entities:
            province, realm = self.place_of(entity)
            label = realm or province or "未标地区"
            counts[label] = counts.get(label, 0) + 1
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]
        labels = "、".join(f"{name} {count}" for name, count in top)
        return f"· 另有 {len(entities)} 条未列出（{labels}）"


def bible_text(world_key: str, *, near: str = "", max_chars: int = 0) -> str:
    """世界圣经的紧凑提示词块；没有内容返回空串。

    :param near: 玩家（或这次生成的对象）现在在哪儿——场景名、地点名、省名、
        王国名都认。给了它就**按距离裁**：同一个省的、同一个王国的、分不出
        地区的都留着，别处的整批不进提示词（只在末行留一句「另有 N 条」）。
        没给就沿用原来的「人物 > 势力 > 地名 > 物品」顺序、一条不删。
    :param max_chars: 字数上限（0 = 不限制）。超限时从尾部丢实体——
        圣经是每轮 / 每次生成都要重发的，宁缺毋滥。

    **为什么按距离裁**：额度固定，装谁进来就是让 DM 看得见谁；而 DM 看见什么
    就会写什么。一局在剑湾跑，把泊银湾的账房、炉心的炉主摆在最前面，
    等同于请它把这些名字带进卡兰提尔的叙述里（实际发生过：剑湾的海商船上
    出现了泊银湾的铁票与收账人）。远处的登记不是不许存在，只是不该占眼前
    这几百字——人走到那边，``near`` 一变它们自然回来。
    """
    bible = load_bible(world_key)
    if not bible.entities and not bible.facts:
        return ""

    lines = ["# 世界圣经（专有名词与事实以这里为准，不要另起名字、不要矛盾）"]
    lines.extend(f"- {item}" for item in bible.facts)
    if bible.entities:
        lines.append("- 实体登记（名字｜种类｜地区｜备注）：")
        atlas = _Atlas(world_key, near)
        kept, faraway = atlas.split(atlas.rank(bible.entities))
        used = sum(len(line) for line in lines) + len(lines)
        # 有省略时才留出末行的位置：默默截断会让人以为登记簿就这么大
        reserve = _OMITTED_RESERVE if faraway else 0
        shown = 0
        for entity in kept:
            row = f"· {entity.line()}"
            if max_chars and used + len(row) + 1 > max_chars - reserve:
                break
            lines.append(row)
            used += len(row) + 1
            shown += 1
        omitted = [*faraway, *kept[shown:]]
        if omitted:
            lines.append(atlas.omitted_line(omitted))
    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text
