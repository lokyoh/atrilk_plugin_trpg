"""DND 专属 Agent。

生成类（入场流程，走工作模型 ``WORK``）：

- :mod:`.gen_world`：世界地图生成（``world``）
- :mod:`.gen_chapter`：章节卡生成（``chapter``）——取代原先的「世界起点与推力」
- :mod:`.gen_realm`：王国/地区生成（``realm``）——政体 / 法度 / 军力 / 外交 + 区域划分
- :mod:`.gen_province`：区域（省）生成（``province``）——地形气候 / 人文产出 / 风险 + 地点索引
- :mod:`.gen_epoch`：纪元称号命名（``epithet``）——按纪元内的实际行为起名
- :mod:`.gen_area`：地区详细生成（``area``）
- :mod:`.gen_place`：地形地貌详细生成（``place``）
- :mod:`.gen_city`：城镇据点详细生成（``city``）
- :mod:`.gen_dungeon`：地下城详细生成（``dungeon``）
- :mod:`.gen_scene`：场景详细生成（``scene``，产出**通用场景**）

构建类（把通用内容改造成本局专属，走工作模型 ``WORK``）：

- :mod:`.build_scene`：场景构建（``scene_builder``，把通用场景改成本局场景）

流程类：

- :mod:`.entry`：入场编排（``run_entry_flow`` 生成新世界；``run_world_refresh`` 补齐残缺世界）
- :mod:`.chapter_flow`：章节线编排（算难度 → 织候选走向 → 选定后织正文 → 推进下一章）
- :mod:`.epoch_flow`：纪元编排（收束判定 → 代价 → 称号 → 称号进圣经）
- :mod:`.on_demand`：按需补齐区域 / 地点 / 场景（缺什么生成什么，按存档的世界作用域）
- :mod:`.game`：本轮行动编排（``submit_action`` / ``run_dm_round``）与场景切换（``open_scene``）

主持类（走聊天模型 ``CHAT``）：

- :mod:`.dm`：DM 主持（带思考循环与功能调用）
- :mod:`.tools`：DM 可调用的 DND 功能
- :mod:`.rules`：内置规则条目（插件资源 ``res/rule.md`` 的查询封装）

:data:`AGENTS` 汇总全部 **DM 可用** 的 agent 实例，供调用方按名取用；
内部 agent（称号命名）另放 :data:`_INTERNAL_AGENTS`，不进 DM 工具清单。
"""

from __future__ import annotations

from .build_scene import SceneBuilderAgent, scene_builder_agent
from .dm import DMAgent, DungeonMasterAgent, dm_agent
from .chapter_flow import (
    ChapterPlan,
    advance_chapter,
    branch_choices_text,
    chapter_plan,
    ensure_opening_chapter,
    offer_branches,
    weave_chapter,
)
from .entry import (
    DEFAULT_OPTIONS,
    EntryOptions,
    EntryReport,
    run_entry_flow,
    run_world_refresh,
)
from .epoch_flow import EpochOutcome, close_chapter, epoch_deeds, settle_epoch
from .game import (
    TurnResult,
    build_scene_for_save,
    is_started,
    open_scene,
    run_dm_round,
    run_opening,
    scene_text,
    submit_action,
)
from .gen_area import AreaAgent, AreaResult, area_agent, parse_area
from .gen_city import CityAgent, city_agent
from .gen_dungeon import DungeonAgent, dungeon_agent
from .gen_epoch import EpithetAgent, EpithetResult, epithet_agent
from .gen_place import PlaceAgent, place_agent
from .gen_scene import SceneAgent, scene_agent
from .gen_chapter import ChapterAgent, ChapterResult, chapter_agent
from .gen_world import (
    WorldAgent,
    WorldMapResult,
    parse_world_map,
    parse_world_profile,
    world_agent,
)
from .gen_province import ProvinceAgent, ProvinceResult, parse_province, province_agent
from .gen_realm import RealmAgent, RealmResult, parse_realm, realm_agent
from .on_demand import (
    ensure_area,
    ensure_province,
    ensure_realm,
    ensure_scene,
    ensure_site,
    normalize_kind,
    slice_block,
)
from .tools import DM_TOOLS

__all__ = [
    "AGENTS",
    "DEFAULT_OPTIONS",
    "DM_TOOLS",
    "AreaAgent",
    "AreaResult",
    "ChapterAgent",
    "CityAgent",
    "DMAgent",
    "DungeonAgent",
    "DungeonMasterAgent",
    "EntryOptions",
    "EntryReport",
    "EpithetAgent",
    "EpithetResult",
    "EpochOutcome",
    "PlaceAgent",
    "ProvinceAgent",
    "ProvinceResult",
    "RealmAgent",
    "RealmResult",
    "SceneAgent",
    "SceneBuilderAgent",
    "ChapterPlan",
    "ChapterResult",
    "TurnResult",
    "WorldAgent",
    "WorldMapResult",
    "advance_chapter",
    "area_agent",
    "branch_choices_text",
    "build_scene_for_save",
    "chapter_agent",
    "chapter_plan",
    "city_agent",
    "close_chapter",
    "dm_agent",
    "dungeon_agent",
    "ensure_area",
    "ensure_opening_chapter",
    "ensure_province",
    "ensure_realm",
    "ensure_scene",
    "ensure_site",
    "epoch_deeds",
    "epithet_agent",
    "get_agent",
    "is_started",
    "normalize_kind",
    "open_scene",
    "offer_branches",
    "parse_area",
    "parse_province",
    "parse_realm",
    "parse_world_map",
    "parse_world_profile",
    "place_agent",
    "province_agent",
    "realm_agent",
    "run_dm_round",
    "run_entry_flow",
    "run_opening",
    "run_world_refresh",
    "scene_agent",
    "scene_builder_agent",
    "scene_text",
    "settle_epoch",
    "slice_block",
    "submit_action",
    "weave_chapter",
    "world_agent",
]

#: 全部 agent 实例，按 :attr:`~..base.TrpgAgent.name` 索引
AGENTS = {
    world_agent.name: world_agent,
    realm_agent.name: realm_agent,
    province_agent.name: province_agent,
    chapter_agent.name: chapter_agent,
    area_agent.name: area_agent,
    place_agent.name: place_agent,
    city_agent.name: city_agent,
    dungeon_agent.name: dungeon_agent,
    scene_agent.name: scene_agent,
    scene_builder_agent.name: scene_builder_agent,
    dm_agent.name: dm_agent,
}

#: 只在内部使用的 agent（不进 :data:`AGENTS`，也不由 DM 直接调用）。
#: 称号生成走 :func:`~.epoch_flow.settle_epoch`，不在 DM 的工具清单里——
#: DM 工具清单有 4300 字符硬上限，而称号是每几章才发生一次的编排动作。
_INTERNAL_AGENTS = {
    epithet_agent.name: epithet_agent,
}


def get_agent(name: str):
    """按名取 agent 实例，未注册时抛出 :class:`KeyError`。

    内部 agent（:data:`_INTERNAL_AGENTS`）也能按名取到——它们只是不进
    DM 的工具清单，并不是不存在。
    """
    if name in AGENTS:
        return AGENTS[name]
    return _INTERNAL_AGENTS[name]
