"""跑团 Agent：按需生成地点（用着什么生成什么）。

入场流程（:mod:`.entry`）只生成**世界地图**与**故事**。之后 DM 决定玩家去往某地时，
由 :func:`ensure_scene` 逐级补齐缺失的文档：

.. code-block:: text

    区域(area)  →  城镇据点 / 自然地点 / 地下城(city / place / dungeon)  →  场景(scene)

每一级都先在世界库里找；找不到才调用对应的生成 agent，生成后写回世界库。
这样「用到哪块生成哪块」，不必开局就把整张地图铺满。
"""

from __future__ import annotations

import re

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ... import settings
from ...dnd.billing import record_result
from ...dnd.world_bible import (
    Entity,
    bible_text,
    register_entities,
)
from ...dnd.world_store import (
    get_world_map,
    list_docs,
    load_doc,
    read_doc,
    save_doc,
)
from .gen_area import area_agent, parse_area
from .gen_city import city_agent
from .gen_dungeon import dungeon_agent
from .gen_place import place_agent
from .gen_scene import parse_scene_npcs, scene_agent

__all__ = ["ensure_scene", "normalize_kind", "slice_block"]

#: 文档种类 -> 中文标签
KIND_LABELS: dict[str, str] = {
    "place": "自然地点",
    "city": "城镇据点",
    "dungeon": "地下城",
}

#: 文档种类 -> 地区文档里的小标题（如 ``### 据点：白港``）
BLOCK_LABELS: dict[str, str] = {
    "place": "子区域",
    "city": "据点",
    "dungeon": "地下城",
}

#: 文档种类 -> 生成 agent 的输入参数名
_BASE_ARG: dict[str, str] = {
    "place": "terrain",
    "city": "settlement",
    "dungeon": "dungeon",
}

_LABEL_TO_KIND: dict[str, str] = {
    "place": "place",
    "自然地点": "place",
    "地形子区域": "place",
    "地形": "place",
    "city": "city",
    "城镇据点": "city",
    "城镇": "city",
    "据点": "city",
    "城市": "city",
    "dungeon": "dungeon",
    "地下城": "dungeon",
}

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s*(?P<title>.+?)\s*$")


def normalize_kind(value: str) -> str:
    """把「城镇据点 / 自然地点 / 地下城」等写法归一成文档种类；识别不了返回空串。"""
    return _LABEL_TO_KIND.get(str(value or "").strip().lower(), "")


def slice_block(text: str, label: str, name: str) -> str:
    """截出标题为 ``<label>：<name>`` 的那一块（到下一个同级或更高级标题为止）。

    找不到回退为空串，由调用方决定退回整篇文档。
    """
    target_re = re.compile(
        rf"^{re.escape(label)}[^\n：:]{{0,8}}[：:]\s*{re.escape(name)}\s*$"
    )
    lines = text.splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        if start is None:
            if target_re.match(match.group("title")):
                start = index
                level = len(match.group("hashes"))
        elif len(match.group("hashes")) <= level:
            return "\n".join(lines[start:index]).strip()
    if start is not None:
        return "\n".join(lines[start:]).strip()
    return ""


def _area_keys(world_key: str) -> list[str]:
    return list_docs(world_key, "area").get("area", [])


def _find_in_areas(world_key: str, name: str, kind: str) -> tuple[str, str, str] | None:
    """在已生成的区域文档里找某地点的基础数据。

    :return: ``(种类, 基础数据, 所在区域正文)``；找不到返回 ``None``。
    """
    for area_key in _area_keys(world_key):
        doc = read_doc(world_key, "area", area_key)
        if doc is None:
            continue
        for kind_key in ("city", "place", "dungeon"):
            if kind and kind_key != kind:
                continue
            block = slice_block(doc.content, BLOCK_LABELS[kind_key], name)
            if block:
                return kind_key, block, doc.content
    return None


def _first_location(area_text: str, kind: str) -> tuple[str, str, str] | None:
    """取区域文档里的第一个地点：``(种类, 名字, 基础数据)``。"""
    sub_areas, settlements, dungeons = parse_area(area_text)
    for kind_key, names in (
        ("city", settlements),
        ("place", sub_areas),
        ("dungeon", dungeons),
    ):
        if kind and kind_key != kind:
            continue
        for name in names:
            block = slice_block(area_text, BLOCK_LABELS[kind_key], name)
            if block:
                return kind_key, name, block
    return None


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


def _register_area(world_key: str, region: str, content: str) -> None:
    """区域文档落库后，把其中的地点名登记进世界圣经（程序提取，零成本）。"""
    sub_areas, settlements, dungeons = parse_area(content)
    seeded: list[Entity] = []
    for name in sub_areas:
        seeded.append(Entity(name=name, kind="place", region=region, note="子区域"))
    for name in settlements:
        seeded.append(Entity(name=name, kind="place", region=region, note="据点"))
    for name in dungeons:
        seeded.append(Entity(name=name, kind="place", region=region, note="地下城"))
    register_entities(world_key, seeded, source=region)


async def ensure_area(world_key: str, region: str, world_text: str = "") -> str | None:
    """确保世界里存在某区域的区域文档，返回其正文；失败返回 ``None``。

    区域属于**世界**，不属于某一局的故事：生成时只喂世界档案与邻居地区的设定，
    绝不把故事文档塞进来（否则生成的区域会带上这一局的剧情，换条故事线就矛盾）。
    """
    region = str(region or "").strip()
    if not region:
        return None
    existing = read_doc(world_key, "area", region)
    if existing is not None:
        return existing.content

    world = world_text or load_doc(world_key, "world", "main") or ""
    region_text = slice_block(world, "地区", region) or world
    neighbor_names = get_world_map(world_key)[1].get(region, [])
    neighbors = "、".join(neighbor_names)
    content = await _run_agent(
        area_agent,
        f"区域「{region}」",
        world_key,
        region=region_text,
        neighbors=neighbors,
        world=_world_context(world, neighbor_names),
    )
    if content is None:
        return None
    save_doc(world_key, "area", region, content, title=region)
    _register_area(world_key, region, content)
    return content


def _world_context(world_text: str, neighbors: list[str]) -> str:
    """给区域生成器的「世界上下文」：世界档案头 + 相邻地区的设定（不含剧情）。

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


async def _ensure_detailed(
    world_key: str, kind: str, name: str, base_text: str, context: str
) -> str | None:
    """确保世界里存在某地点的详细文档（城镇 / 自然地点 / 地下城）。"""
    existing = read_doc(world_key, kind, name)
    if existing is not None:
        return existing.content

    agent = {"place": place_agent, "city": city_agent, "dungeon": dungeon_agent}[kind]
    base = base_text or (
        f"### {BLOCK_LABELS[kind]}：{name}\n（暂无基础数据，请结合所在区域信息合理设定）"
    )
    content = await _run_agent(
        agent,
        f"{KIND_LABELS[kind]}「{name}」",
        world_key,
        context=context,
        **{_BASE_ARG[kind]: base},
    )
    if content is None:
        return None
    save_doc(world_key, kind, name, content, title=name)
    return content


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
        kind=KIND_LABELS.get(kind, "地点"),
        context=context,
    )
    if content is None:
        return None
    key = save_doc(world_key, "scene", name, content, title=name).stem
    # 场景里的在场者登记进世界圣经（之后 DM 说到他们时用同一个叫法）
    register_entities(world_key, parse_scene_npcs(content), source=f"scene:{name}")
    return kind, key, content


async def ensure_scene(
    world_key: str,
    location: str,
    *,
    kind: str = "",
    region: str = "",
) -> tuple[str, str, str] | None:
    """确保世界里存在某地点的场景，返回 ``(种类, 场景键, 场景正文)``。

    逐级补齐：场景 → 详细文档（城镇 / 自然地点 / 地下城）→ 区域。
    无法定位时返回 ``None``。

    写回世界库的这几级都**不带故事**：世界库里的区域 / 地点 / 场景是世界的骨架，
    哪条故事线来跑都该是同一副骨架；本局的专属化由 :func:`~.game.build_scene_for_save`
    在存档副本里做，不回头污染世界库。
    """
    name = str(location or "").strip()
    if not name:
        return None
    kind_key = normalize_kind(kind)

    # 1. 已经是场景
    scene_doc = read_doc(world_key, "scene", name)
    if scene_doc is not None:
        return "scene", scene_doc.key, scene_doc.content

    # 2. 已经是详细文档（城镇 / 自然地点 / 地下城）
    for candidate in ("city", "place", "dungeon"):
        if kind_key and candidate != kind_key:
            continue
        detailed = read_doc(world_key, candidate, name)
        if detailed is not None:
            return await _make_scene(
                world_key, name, candidate, detailed.content, detailed.content
            )

    # 3. 在已生成的区域文档里找到该地点的基础数据
    found = _find_in_areas(world_key, name, kind_key)
    if found is not None:
        found_kind, base_text, area_text = found
        detailed = await _ensure_detailed(
            world_key, found_kind, name, base_text, area_text
        )
        if detailed is None:
            return None
        return await _make_scene(world_key, name, found_kind, detailed, area_text)

    # 4. location 本身是某个区域 → 生成该区域，取其中一个地点
    regions, _adjacency = get_world_map(world_key)
    world_text = load_doc(world_key, "world", "main") or ""
    region_name = str(region or "").strip()
    if name in regions or slice_block(world_text, "地区", name):
        region_name = name
    if not region_name and regions:
        # 谁都不知道玩家要去哪：先把第一个区域铺开，从中挑一个地点
        region_name = regions[0]
    if region_name:
        area_text = await ensure_area(world_key, region_name, world_text)
        if area_text:
            picked = _first_location(area_text, kind_key)
            if picked is not None:
                picked_kind, picked_name, base_text = picked
                # 该地点已经有场景（预设世界常见）：直接用，别为了「补详细文档」白花一次调用
                existing_scene = read_doc(world_key, "scene", picked_name)
                if existing_scene is not None:
                    return "scene", existing_scene.key, existing_scene.content
                detailed = await _ensure_detailed(
                    world_key, picked_kind, picked_name, base_text, area_text
                )
                if detailed is not None:
                    return await _make_scene(
                        world_key, picked_name, picked_kind, detailed, area_text
                    )

    # 5. 兜底：给了种类，就用所在区域上下文直接把这个名字当新地点生成
    if kind_key and region_name:
        area_doc = read_doc(world_key, "area", region_name)
        if area_doc is not None:
            detailed = await _ensure_detailed(
                world_key, kind_key, name, "", area_doc.content
            )
            if detailed is not None:
                return await _make_scene(
                    world_key, name, kind_key, detailed, area_doc.content
                )
    return None
