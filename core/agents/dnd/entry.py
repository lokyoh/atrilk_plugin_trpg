"""跑团 Agent：DM 入场流程编排器（多世界）。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」，但按「用着什么生成什么」
收敛为**只生成世界地图**：

1. 生成地图（:mod:`.gen_world`）

**故事推力文档已经下线**。原先这里会接着生成一篇「世界起点与推力」，
把一个世界的长期压力写死成静态文档；现在那一层由事件卡接手——
玩家玩到哪一章，就由 :mod:`.event_flow` 按当时的世界与队伍状态织那一章的卡。
换句话说：**世界层保持纯净，剧情是可替换的剧本**（见 :mod:`...dnd.event_progress`）。

区域 / 城镇 / 自然地点 / 地下城 / 场景**不在开局预生成**，而是在 DM 决定玩家去往某地时
由 :mod:`.on_demand` 逐级补齐；事件卡的钩子地点就从这些逐步长出来的地名里挑。

生成 agent 只负责产出文档，**落库由本编排器负责**：世界写入注册表
（名称 / 一句话简介 / 主题）与该世界的文档库。耗时的模型调用在这里串行执行，
并通过 ``on_progress`` 回调逐步汇报进度。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ...dnd.world_bible import register_entities, seed_from_world
from ...dnd.world_store import (
    MAIN_DOC_KEY,
    clean_name,
    list_worlds,
    load_doc,
    new_world_id,
    save_doc,
    set_world_map,
    upsert_world,
)
from ..base import AgentResult, TrpgAgent, UsageEntry
from .gen_world import WorldAgent, world_agent

__all__ = [
    "DEFAULT_OPTIONS",
    "EntryOptions",
    "EntryReport",
    "ProgressCallback",
    "run_entry_flow",
    "run_world_refresh",
]

#: 进度回调：接收一行进度文案，可以是普通函数或协程函数
ProgressCallback = Callable[[str], "Awaitable[None] | None"]


@dataclass(slots=True)
class EntryOptions:
    """入场流程的可调参数。"""

    #: 世界主题 / 基调
    theme: str = ""
    #: 期望的地区数量（传给世界地图 agent）
    region_count: str = ""
    #: 目标世界键；留空时自动生成新键（生成新世界）
    world_key: str = ""


#: 默认参数
DEFAULT_OPTIONS = EntryOptions()


@dataclass(slots=True)
class EntryReport:
    """入场流程的执行结果：生成了什么、哪里失败了。"""

    options: EntryOptions
    #: 实际使用的世界键
    world_key: str = ""
    regions: list[str] = field(default_factory=list)
    #: 世界是否成功落库
    world_saved: bool = False
    #: 世界档案（名称 / 一句话简介）
    world_title: str = ""
    world_intro: str = ""
    errors: list[str] = field(default_factory=list)
    #: 本流程里每一次模型调用的用量（供计费统计）
    usages: list[UsageEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        """给玩家 / 日志看的一行统计。"""
        lines: list[str] = []
        if self.world_saved:
            name = f"「{self.world_title}」" if self.world_title else ""
            lines.append(f"世界{name}已生成（地区 {len(self.regions)} 个）")
        if self.errors:
            lines.append("失败：" + "；".join(self.errors))
        if not lines:
            lines.append("没有生成任何内容")
        return "\n".join(lines)


async def _notify(on_progress: ProgressCallback | None, message: str) -> None:
    """调用进度回调；回调出错只记日志，不影响流程。"""
    if on_progress is None:
        return
    try:
        result = on_progress(message)
        if inspect.isawaitable(result):
            await result
    except Exception as e:
        log.warning(f"入场流程进度回调失败：{str_traceback(e)}")


async def _safe(
    agent: TrpgAgent, report: EntryReport, label: str, **values: object
) -> AgentResult | None:
    """调用一个生成 agent；失败时记入 ``report.errors`` 并返回 ``None``。"""
    try:
        result = await agent.run(**values)
    except Exception as e:
        log.warning(f"入场流程「{label}」生成失败：{str_traceback(e)}")
        report.errors.append(f"{label}：{e}")
        return None
    report.usages.extend(getattr(result, "usages", None) or [])
    return result


async def run_entry_flow(
    *,
    options: EntryOptions | None = None,
    on_progress: ProgressCallback | None = None,
    world: WorldAgent = world_agent,
) -> EntryReport:
    """生成一个新世界，写入全局世界库。

    生成完就收工：事件不进这里。第一个事件由 :func:`~.game.run_opening`
    装**预写事件**（预设世界里每省一件），织不上的才走生成器。
    那时才有玩家、角色卡与难度可言。

    失败只记录到 :attr:`EntryReport.errors`，不会抛出；世界生成失败时直接返回
    （已生成的世界会保留在世界列表里，可再次选中）。
    """
    options = options or DEFAULT_OPTIONS
    world_key = options.world_key or new_world_id()
    report = EntryReport(options=options, world_key=world_key)

    # ① 世界地图（唯一一步）
    await _notify(on_progress, "① 正在生成世界地图……")
    world_result = await _safe(
        world,
        report,
        "世界地图",
        theme=options.theme,
        region_count=options.region_count,
    )
    if world_result is None:
        return report
    world_title = (
        world_result.world_title or options.theme or (f"世界 {len(list_worlds()) + 1}")
    )
    save_doc(world_key, "world", MAIN_DOC_KEY, world_result.content, title="世界地图")
    regions = list(world_result.regions)
    set_world_map(world_key, regions, world_result.adjacency)
    # 世界圣经：程序播种（地区 / 地标 / 国都 / 特殊地区）+ 模型给的人物势力登记
    seed_from_world(world_key, world_text=world_result.content)
    register_entities(
        world_key, getattr(world_result, "entities", None) or [], source="world"
    )
    upsert_world(
        world_key,
        title=world_title,
        intro=world_result.world_intro,
        theme=options.theme,
    )
    report.regions = regions
    report.world_saved = True
    report.world_title = world_title
    report.world_intro = world_result.intro
    await _notify(
        on_progress, f"① 世界「{world_title}」已就绪（地区 {len(regions)} 个）"
    )
    return report


async def run_world_refresh(
    *,
    world_key: str,
    on_progress: ProgressCallback | None = None,
    world: WorldAgent = world_agent,
) -> EntryReport:
    """补齐一个**已有世界**里缺失的世界地图文档；世界不存在时直接失败。

    只补世界地图，不碰事件。世界文档缺失通常是上一次生成中途失败留下的
    半成品，补一次就能重新建档。
    """
    options = EntryOptions(world_key=world_key)
    report = EntryReport(options=options, world_key=world_key)

    if load_doc(world_key, "world", MAIN_DOC_KEY):
        report.world_saved = True  # 已有，不算本次失败
        report.errors.append("这个世界已经有世界地图了")
        return report

    await _notify(on_progress, "正在补齐世界地图……")
    world_result = await _safe(world, report, "世界地图")
    if world_result is None:
        return report
    world_title = clean_name(world_result.world_title or "") or world_key
    save_doc(world_key, "world", MAIN_DOC_KEY, world_result.content, title="世界地图")
    set_world_map(world_key, list(world_result.regions), world_result.adjacency)
    seed_from_world(world_key, world_text=world_result.content)
    register_entities(
        world_key, getattr(world_result, "entities", None) or [], source="world"
    )
    upsert_world(
        world_key,
        title=world_title,
        intro=world_result.world_intro,
        theme=report.options.theme,
    )
    report.regions = list(world_result.regions)
    report.world_saved = True
    report.world_title = world_title
    report.world_intro = world_result.world_intro
    await _notify(on_progress, f"世界「{world_title}」已就绪")
    return report
