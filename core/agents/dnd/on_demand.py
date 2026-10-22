"""按需补齐世界内容：缺什么生成什么，一层一层往上补。

世界是**五层**，由深到浅：

```
世界（world）
└─ 王国/地区（realm）      谁说了算、法度、军力、外交
   └─ 区域/省（province）  地形气候、人文产出、风险 + 辖区地点索引
      └─ 地点（city / place / dungeon）
         └─ 场景（scene）  真正演出的那一处地方
```

玩家说「我要去旧盐井」，程序只知道一个名字。:func:`ensure_scene` 要做的是从这个名字
**倒着补齐**：先看它是不是已有场景，再看是不是已有地点（三个种类都试），都没有就去省文档
的地点索引里找，连省都没有就先落省、再落地点、再织场景。每一步都只在**真的需要**时才调模型。

三条贯穿全文件的纪律：

1. **写回世界库的东西不带故事**。世界库是世界本身的骨架，换一条剧情线来跑该是同一副骨架；
   本局专属化由 :func:`~.game.build_scene_for_save` 在存档副本里做，不回头污染世界库。
2. **第 4 层是三个种类，不是一个种类加子类型**。据点 / 野外地点 / 地下城的生成模板本来就不同，
   靠子类型分派只是把区别藏起来。所以这里按种类分派生成器，落库也各进各的目录。
3. **地点索引只给名字与种类**。省文档列出辖区地点，但每个只要一行——细节等玩家真去了再展开。
   旧「地区文档」把辖区里十个地点全展开，是它又长又难改的根因。
"""

from __future__ import annotations

import re

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ... import settings
from ...dnd.billing import record_result
from ...dnd.world_bible import Entity, bible_text, register_entities
from ...dnd.world_store import (
    DOC_KIND_LABELS,
    add_provinces,
    clean_name,
    get_provinces,
    get_world_map,
    load_doc,
    load_index,
    names_at_level,
    read_at_level,
    read_doc,
    save_doc,
)
from .gen_city import city_agent
from .gen_dungeon import dungeon_agent
from .gen_place import place_agent
from .gen_province import parse_crossing, parse_province, province_agent
from .gen_realm import parse_inhabited, parse_realm, realm_agent
from .gen_scene import parse_scene_npcs, scene_agent

__all__ = [
    "ensure_area",
    "ensure_province",
    "ensure_realm",
    "ensure_scene",
    "ensure_site",
    "normalize_kind",
    "slice_block",
]

#: 地点种类 -> (生成 agent, 该 agent 的基础数据参数名)
_SITE_AGENTS = {
    "city": (city_agent, "settlement"),
    "place": (place_agent, "terrain"),
    "dungeon": (dungeon_agent, "dungeon"),
}

#: 中文写法 -> 地点种类。DM 现在仍然会说「城镇」「野外」「地下城」。
_KIND_ALIASES: dict[str, str] = {
    "city": "city",
    "城镇": "city",
    "城镇据点": "city",
    "据点": "city",
    "城市": "city",
    "place": "place",
    "野外地点": "place",
    "自然地点": "place",
    "地形": "place",
    "野外": "place",
    "dungeon": "dungeon",
    "地下城": "dungeon",
    "遗迹": "dungeon",
}

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s*(?P<title>.+?)\s*$")


def normalize_kind(value: str) -> str:
    """把 DM 写的「种类」归一成 ``city`` / ``place`` / ``dungeon``。

    认不出返回空串（= 不限定种类，让程序三个都试）。
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in _KIND_ALIASES:
        return _KIND_ALIASES[text]
    for word, kind in _KIND_ALIASES.items():
        if word and word in text:
            return kind
    return ""


def slice_block(text: str, label: str, name: str) -> str:
    """截出 ``### <label>：<name>`` 这一小节（到下一个小标题为止）。

    世界文档、王国文档、省文档里的地点小节都靠它取——**不依赖具体是哪一层**，
    只要小标题写成「标签：名字」就能截。名字后面允许带 ``（地下城）`` 这类类型注。
    """
    body = str(text or "")
    name = str(name or "").strip()
    if not name:
        return ""
    pattern = re.compile(
        rf"^#{{2,4}}\s*[^\n：:]*{re.escape(label)}[^\n：:]{{0,8}}[：:]\s*"
        rf"{re.escape(name)}\s*(?:[（(][^）)\n]{{1,10}}[）)])?\s*$"
    )
    lines = body.splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match and pattern.match(line):
            start = index
            level = len(match.group("hashes"))
            break
    if start is None:
        return ""
    for index in range(start + 1, len(lines)):
        match = _HEADING_RE.match(lines[index])
        if match and len(match.group("hashes")) <= level:
            return "\n".join(lines[start:index]).strip()
    return "\n".join(lines[start:]).strip()


async def _run_agent(agent, label: str, world_key: str = "", **values) -> str | None:
    """调用一个生成 agent，失败只记日志并返回 ``None``。

    ``world_key`` 非空时自动注入世界圣经——专有名词与事实以它为准。
    """
    try:
        if world_key:
            values.setdefault(
                "bible",
                bible_text(world_key, max_chars=settings.world_bible_max_chars()),
            )
        result = await agent.run(**values)
    except Exception as e:
        log.warning(f"按需生成「{label}」失败：{str_traceback(e)}")
        return None
    record_result(result)
    return result.content


# --------------------------------------------------------------------------- #
# 第 2 层：王国/地区
# --------------------------------------------------------------------------- #


def _register_realm(world_key: str, realm: str, content: str) -> list[str]:
    """王国文档落库后登记辖下的**区域**名（零模型成本），返回省名列表。

    省名一律登记成圣经里的 ``place``——圣经是**名字登记簿**，不是层级树；
    层级在 ``index.json`` 的 ``provinces`` 与文档 ``meta.parent`` 里。
    往圣经里加 ``realm`` / ``province`` 两种新种类只会让提示词变长。
    """
    provinces = parse_realm(content)
    register_entities(
        world_key,
        [
            Entity(
                name=name,
                kind="place",
                region=realm,
                note="区域",
                source=f"realm:{realm}",
            )
            for name, _terrain, _sites in provinces
        ],
        source=realm,
    )
    return [name for name, _t, _s in provinces]


async def ensure_realm(world_key: str, realm: str, world_text: str = "") -> str | None:
    """确保世界里存在某个**王国/地区**的文档，返回其正文；失败返回 ``None``。

    王国属于**世界**，不属于某一局：生成时只喂世界档案与相邻地区的设定，
    绝不把这一局的故事塞进来（否则这个王国会带上本局剧情，换条线就矛盾）。
    """
    realm = clean_name(str(realm or ""))
    if not realm:
        return None
    existing = read_at_level(world_key, 2, realm)
    if existing is not None:
        return existing.content

    world = world_text or load_doc(world_key, "world", "main") or ""
    info = slice_block(world, "地区", realm) or world
    neighbor_names = _neighbor_realms(world_key, realm)
    content = await _run_agent(
        realm_agent,
        f"王国/地区「{realm}」",
        world_key,
        realm=info,
        neighbors="、".join(neighbor_names),
        world=_world_context(world, neighbor_names),
    )
    if content is None:
        return None
    save_doc(
        world_key,
        "realm",
        realm,
        content,
        title=realm,
        meta={"level": 2, "parent": "", "inhabited": parse_inhabited(content)},
    )
    provinces = _register_realm(world_key, realm, content)
    if provinces:
        add_provinces(world_key, realm, provinces)
    return content


def _neighbor_realms(world_key: str, realm: str) -> list[str]:
    """和某个王国相邻的王国（**从省邻接推**：我的省邻着谁的省，就是邻着谁）。

    邻接表是省级的（相邻两省 = 走一天），所以王国之间没有直接的边。
    推不出来就返回空——生成器那边这一项本就可留空。
    """
    table = get_provinces(world_key)
    adjacency = get_world_map(world_key)[1]
    owner: dict[str, str] = {}
    for name, provinces in table.items():
        for province in provinces:
            owner.setdefault(province, name)
    mine = set(table.get(realm, []))
    found: list[str] = []
    for province in mine:
        for other in adjacency.get(province, []):
            other_realm = owner.get(other, "")
            if other_realm and other_realm != realm and other_realm not in found:
                found.append(other_realm)
    return found


# --------------------------------------------------------------------------- #
# 第 3 层：区域（省）
# --------------------------------------------------------------------------- #


def _province_hint(
    world_key: str, province: str, realm: str = ""
) -> tuple[str, str, str]:
    """找某个省的基础数据，返回 ``(省名, 基础正文, 所属王国)``。

    来源是王国文档的「区域划分」一节（``### 区域：<名>``）。
    「所属王国」取不到时返回空串，调用方退回「世界里第一个王国」。
    """
    province = clean_name(str(province or ""))
    if not province:
        return "", "", str(realm or "")

    # 1. 先查归属表（王国文档落库时就写进去了，最省事也最准）
    catalog = get_provinces(world_key)
    if realm and province in (catalog.get(realm) or []):
        owner = realm
    else:
        owner = next(
            (name for name, names in catalog.items() if province in (names or [])), ""
        )
    if owner:
        doc = read_at_level(world_key, 2, owner)
        block = slice_block(doc.content, "区域", province) if doc is not None else ""
        return province, block or "", owner

    # 2. 表里没有：可能是个还没登记进表的省，逐个王国文档解析一遍
    realms = (
        [realm]
        if realm
        else [*get_world_map(world_key)[0], *names_at_level(world_key, 2)]
    )
    for owner in dict.fromkeys(r for r in realms if r):
        doc = read_at_level(world_key, 2, owner)
        if doc is None:
            continue
        for name, _terrain, _sites in parse_realm(doc.content):
            if name == province:
                block = slice_block(doc.content, "区域", province)
                return name, block or "", owner
    return province, "", str(realm or "")


async def ensure_province(
    world_key: str, province: str, *, realm: str = "", hint: str = ""
) -> str | None:
    """确保世界里存在某个**区域（省）**的文档，返回其正文；失败返回 ``None``。

    :param realm: 已经知道它属于哪个王国时传进来，省掉一次反查。
    :param hint: 已经取到的基础数据（王国文档里那一节），省掉一次解析。
    """
    province = clean_name(str(province or ""))
    if not province:
        return None
    existing = read_at_level(world_key, 3, province)
    if existing is not None:
        return existing.content

    name, base, owner = (
        (province, hint, str(realm or ""))
        if hint
        else _province_hint(world_key, province, realm)
    )
    if not owner:
        # 谁都不知道它属于哪儿：先铺开世界里第一个王国，让省有个归属
        regions, _adjacency = get_world_map(world_key)
        owner = regions[0] if regions else ""
    realm_text = ""
    uninhabited = False
    if owner:
        realm_text = await ensure_realm(world_key, owner) or ""
        realm_meta = (load_index(world_key)["docs"].get("realm") or {}).get(owner) or {}
        uninhabited = not bool(realm_meta.get("inhabited", True))
    if not base and realm_text:
        base = slice_block(realm_text, "区域", province)
    world_text = load_doc(world_key, "world", "main") or ""

    content = await _run_agent(
        province_agent,
        f"区域「{province}」",
        world_key,
        province=base
        or f"### 区域：{province}\n（暂无设定，请结合所属王国与地形合理设定）",
        realm=realm_text,
        world=_world_context(world_text, [owner] if owner else []),
        # 无人区不占 prompt（空值会被 build_prompt 跳过）
        inhabited=""
        if not uninhabited
        else "无人区：这片地方没人管事，没有政体、没有驿站、没有常驻人口。"
        "只出野外地点与地下城，**不要生成据点**。",
    )
    if content is None:
        return None
    save_doc(
        world_key,
        "province",
        province,
        content,
        title=province,
        meta={"level": 3, "parent": owner, "crossing": parse_crossing(content)},
    )
    if owner:
        add_provinces(world_key, owner, [province])
    _register_sites(world_key, province, content)
    return content


def _register_sites(
    world_key: str, province: str, content: str
) -> list[tuple[str, str]]:
    """把省文档「地点」一节的地点名登记进圣经（零模型成本）。"""
    sites = parse_province(content)
    register_entities(
        world_key,
        [
            Entity(
                name=name,
                kind="place",
                region=province,
                note=DOC_KIND_LABELS.get(kind, "地点"),
                source=f"province:{province}",
            )
            for name, kind, _note in sites
        ],
        source=province,
    )
    return [(name, kind) for name, kind, _note in sites]


# --------------------------------------------------------------------------- #
# 第 4 层：地点（据点 / 野外地点 / 地下城）
# --------------------------------------------------------------------------- #


async def ensure_site(
    world_key: str,
    name: str,
    *,
    kind: str = "",
    base_text: str = "",
    context: str = "",
    province: str = "",
) -> tuple[str, str] | None:
    """确保世界里存在某个地点的详细文档，返回 ``(种类, 正文)``；失败返回 ``None``。

    三个种类各有自己的生成器与目录：``city`` 据点、``place`` 野外地点、``dungeon`` 地下城。
    种类不确定时按「野外地点」算——宁可当成一处野外，也不要凭空造一个有人住的据点。
    """
    name = clean_name(str(name or ""))
    if not name:
        return None
    found = read_at_level(world_key, 4, name)
    if found is not None:
        return found.kind, found.content

    picked = normalize_kind(kind) or "place"
    agent, arg_name = _SITE_AGENTS[picked]
    base = base_text or (
        f"### 地点：{name}（{DOC_KIND_LABELS[picked]}）\n"
        "（暂无基础数据，请结合所在区域信息合理设定）"
    )
    content = await _run_agent(
        agent,
        f"{DOC_KIND_LABELS[picked]}「{name}」",
        world_key,
        context=context,
        **{arg_name: base},
    )
    if content is None:
        return None
    save_doc(
        world_key,
        picked,
        name,
        content,
        title=name,
        meta={"level": 4, "parent": province},
    )
    return picked, content


def _find_site_hint(
    world_key: str, name: str, kind: str = ""
) -> tuple[str, str, str, str] | None:
    """在省文档里找某个地点的基础数据。

    :return: ``(种类, 基础正文, 所在省, 上下文正文)``；找不到返回 ``None``。
    """
    name = clean_name(str(name or ""))
    if not name:
        return None
    want = normalize_kind(kind)
    for province in names_at_level(world_key, 3):
        doc = read_at_level(world_key, 3, province)
        if doc is None:
            continue
        for site_name, found_kind, _note in parse_province(doc.content):
            if site_name != name or (want and found_kind != want):
                continue
            block = slice_block(doc.content, "地点", name) or slice_block(
                doc.content, DOC_KIND_LABELS[found_kind], name
            )
            return found_kind, block or "", province, doc.content
    return None


# --------------------------------------------------------------------------- #
# 第 5 层：场景
# --------------------------------------------------------------------------- #


async def _make_scene(
    world_key: str, name: str, kind: str, detailed: str, context: str
) -> tuple[str, str, str] | None:
    """用某地点的详细文档生成场景，返回 ``(种类, 场景键, 场景正文)``。"""
    existing = read_doc(world_key, "scene", name)
    if existing is not None:
        return kind, existing.key, existing.content

    content = await _run_agent(
        scene_agent,
        f"场景「{name}」",
        world_key,
        location=detailed,
        kind=DOC_KIND_LABELS.get(kind, "地点"),
        context=context,
    )
    if content is None:
        return None
    key = save_doc(
        world_key, "scene", name, content, title=name, meta={"site": name}
    ).stem
    register_entities(world_key, parse_scene_npcs(content), source=f"scene:{name}")
    return kind, key, content


def _first_site(province_text: str, want: str = "") -> tuple[str, str, str] | None:
    """取省文档地点索引里的第一个（可指定种类），返回 ``(名字, 种类, 基础正文)``。"""
    for name, kind, _note in parse_province(province_text):
        if want and kind != want:
            continue
        block = slice_block(province_text, "地点", name) or slice_block(
            province_text, DOC_KIND_LABELS[kind], name
        )
        return name, kind, block or ""
    return None


def _first_province(realm_text: str) -> tuple[str, str, str] | None:
    """取王国文档「区域划分」里的第一个省，返回 ``(省名, 地形底色, 主要据点)``。"""
    provinces = parse_realm(realm_text)
    return provinces[0] if provinces else None


async def ensure_scene(
    world_key: str,
    location: str,
    *,
    kind: str = "",
    region: str = "",
) -> tuple[str, str, str] | None:
    """确保世界里存在某地点的场景，返回 ``(种类, 场景键, 场景正文)``。

    **逐级往上补**：场景 → 地点 → 区域（省）→ 王国。哪一级缺就补哪一级，
    已有的直接复用（所以重复调用不会重复花模型调用）。

    :param kind: 地点种类的提示（``城镇`` / ``野外地点`` / ``地下城``）。
    :param region: 玩家／DM 提到的所在地（王国或省），已知就传，省掉反查。
    """
    name = clean_name(str(location or ""))
    if not name:
        return None
    want = normalize_kind(kind)

    # 1. 已经是场景
    scene_doc = read_doc(world_key, "scene", name)
    if scene_doc is not None:
        return "scene", scene_doc.key, scene_doc.content

    # 2. 已经是地点（三个种类都试）
    site_doc = read_at_level(world_key, 4, name)
    if site_doc is not None:
        return await _make_scene(
            world_key, name, site_doc.kind, site_doc.content, site_doc.content
        )

    # 3. 省文档的地点索引里有它 → 落地点 → 织场景
    found = _find_site_hint(world_key, name, want)
    if found is not None:
        found_kind, base, province, province_text = found
        detailed = await ensure_site(
            world_key,
            name,
            kind=found_kind,
            base_text=base,
            context=province_text,
            province=province,
        )
        if detailed is None:
            return None
        return await _make_scene(
            world_key, name, detailed[0], detailed[1], province_text
        )

    region_name = clean_name(str(region or ""))
    world_text = load_doc(world_key, "world", "main") or ""
    regions, _adjacency = get_world_map(world_key)

    # 4. location 本身是某个**省** → 落省 → 取它辖下第一个地点
    _province, base_hint, owner = _province_hint(world_key, name, region_name)
    if base_hint or name in get_provinces(world_key).get(region_name, []):
        province_text = await ensure_province(
            world_key, name, realm=owner, hint=base_hint
        )
        if province_text:
            picked = _first_site(province_text, want)
            if picked is not None:
                picked_name, picked_kind, base = picked
                existing_scene = read_doc(world_key, "scene", picked_name)
                if existing_scene is not None:
                    return "scene", existing_scene.key, existing_scene.content
                detailed = await ensure_site(
                    world_key,
                    picked_name,
                    kind=picked_kind,
                    base_text=base,
                    context=province_text,
                    province=name,
                )
                if detailed is not None:
                    return await _make_scene(
                        world_key, picked_name, detailed[0], detailed[1], province_text
                    )

    # 5. location 本身是某个**王国** → 落王国 → 取第一个省 → 取第一个地点
    realm_name = region_name
    if name in regions or slice_block(world_text, "地区", name):
        realm_name = name
    if not realm_name and regions:
        # 谁都不知道玩家要去哪：先把第一个王国铺开，从中挑一个省、再挑一个地点
        realm_name = regions[0]
    if realm_name and realm_name == name:
        realm_text = await ensure_realm(world_key, realm_name, world_text)
        if realm_text:
            picked_province = _first_province(realm_text)
            if picked_province is not None:
                province_text = await ensure_province(
                    world_key, picked_province[0], realm=realm_name
                )
                if province_text:
                    picked = _first_site(province_text, want)
                    if picked is not None:
                        picked_name, picked_kind, base = picked
                        detailed = await ensure_site(
                            world_key,
                            picked_name,
                            kind=picked_kind,
                            base_text=base,
                            context=province_text,
                            province=picked_province[0],
                        )
                        if detailed is not None:
                            return await _make_scene(
                                world_key,
                                picked_name,
                                detailed[0],
                                detailed[1],
                                province_text,
                            )

    # 6. 兜底：给了种类，就用所在王国 / 省的上下文直接把这个名字当新地点生成
    fallback_kind = want or "place"
    context = ""
    parent = ""
    if region_name and region_name in names_at_level(world_key, 3):
        doc = read_at_level(world_key, 3, region_name)
        context = doc.content if doc is not None else ""
        parent = region_name
    elif realm_name:
        doc = read_at_level(world_key, 2, realm_name)
        context = doc.content if doc is not None else ""
    if context:
        detailed = await ensure_site(
            world_key,
            name,
            kind=fallback_kind,
            base_text="",
            context=context,
            province=parent,
        )
        if detailed is not None:
            return await _make_scene(world_key, name, detailed[0], detailed[1], context)
    return None


def _world_context(world_text: str, neighbors: list[str]) -> str:
    """给生成器的「世界上下文」：世界档案头 + 相邻地区的设定（不含剧情）。

    只取与世界本身有关的部分——名称和一句话简介定基调，相邻地区定衔接风格；
    故事文档一律不进这里。取不到就返回空串（该项输入本就可留空）。
    """
    lines: list[str] = []
    head: list[str] = []
    for line in (world_text or "").splitlines():
        if line.startswith("## ") or line.startswith("```"):
            break
        if line.strip():
            head.append(line.strip())
    if head:
        lines.extend(head)
    for name in neighbors or []:
        block = slice_block(world_text, "地区", name)
        if block:
            lines.append("")
            lines.append(block)
    return "\n".join(lines).strip()


async def ensure_area(world_key: str, region: str, world_text: str = "") -> str | None:
    """旧名兼容：``ensure_area`` = :func:`ensure_realm`。

    代码里还有几处按老名字调它；语义已经升格成「王国/地区」，行为不变。
    """
    return await ensure_realm(world_key, region, world_text)
