"""DND 专属 Agent。

生成类（入场流程，走工作模型 ``WORK``）：

- :mod:`.gen_world`：世界地图生成（``world``）
- :mod:`.gen_story`：世界起点与推力设定（``story``）
- :mod:`.gen_area`：地区详细生成（``area``）
- :mod:`.gen_place`：地形地貌详细生成（``place``）
- :mod:`.gen_city`：城镇据点详细生成（``city``）
- :mod:`.gen_dungeon`：地下城详细生成（``dungeon``）
- :mod:`.gen_scene`：场景详细生成（``scene``，产出**通用场景**）

构建类（把通用内容改造成本局专属，走工作模型 ``WORK``）：

- :mod:`.build_scene`：场景构建（``scene_builder``，把通用场景改成本局场景）

流程类：

- :mod:`.entry`：入场编排（只生成世界地图 + 故事）
- :mod:`.on_demand`：按需补齐区域 / 地点 / 场景（缺什么生成什么）
- :mod:`.game`：本轮行动编排（``submit_action`` / ``run_dm_round``）与场景切换（``open_scene``）

主持类（走聊天模型 ``CHAT``）：

- :mod:`.dm`：DM 主持（带思考循环与功能调用）
- :mod:`.tools`：DM 可调用的 DND 功能
- :mod:`.rules`：内置规则条目（插件资源 ``res/rule.md`` 的查询封装）

:data:`AGENTS` 汇总全部 agent 实例，供调用方按名取用。
"""

from __future__ import annotations

from .build_scene import SceneBuilderAgent, scene_builder_agent
from .dm import DMAgent, DungeonMasterAgent, dm_agent
from .entry import DEFAULT_OPTIONS, EntryOptions, EntryReport, run_entry_flow
from .game import (
    TurnResult,
    build_scene_for_save,
    open_scene,
    run_dm_round,
    scene_text,
    submit_action,
)
from .gen_area import AreaAgent, AreaResult, area_agent, parse_area
from .gen_city import CityAgent, city_agent
from .gen_dungeon import DungeonAgent, dungeon_agent
from .gen_place import PlaceAgent, place_agent
from .gen_scene import SceneAgent, scene_agent
from .gen_story import StoryAgent, story_agent
from .gen_world import WorldAgent, WorldMapResult, parse_world_map, world_agent
from .on_demand import ensure_scene, normalize_kind, slice_block
from .tools import DM_TOOLS

__all__ = [
    "AGENTS",
    "DEFAULT_OPTIONS",
    "DM_TOOLS",
    "AreaAgent",
    "AreaResult",
    "CityAgent",
    "DMAgent",
    "DungeonAgent",
    "DungeonMasterAgent",
    "EntryOptions",
    "EntryReport",
    "PlaceAgent",
    "SceneAgent",
    "SceneBuilderAgent",
    "StoryAgent",
    "TurnResult",
    "WorldAgent",
    "WorldMapResult",
    "area_agent",
    "build_scene_for_save",
    "city_agent",
    "dm_agent",
    "dungeon_agent",
    "ensure_scene",
    "get_agent",
    "normalize_kind",
    "open_scene",
    "parse_area",
    "parse_world_map",
    "place_agent",
    "run_dm_round",
    "run_entry_flow",
    "scene_agent",
    "scene_builder_agent",
    "scene_text",
    "slice_block",
    "story_agent",
    "submit_action",
    "world_agent",
]

#: 全部 agent 实例，按 :attr:`~..base.TrpgAgent.name` 索引
AGENTS = {
    world_agent.name: world_agent,
    story_agent.name: story_agent,
    area_agent.name: area_agent,
    place_agent.name: place_agent,
    city_agent.name: city_agent,
    dungeon_agent.name: dungeon_agent,
    scene_agent.name: scene_agent,
    scene_builder_agent.name: scene_builder_agent,
    dm_agent.name: dm_agent,
}


def get_agent(name: str):
    """按名取 agent 实例，未注册时抛出 :class:`KeyError`。"""
    return AGENTS[name]
