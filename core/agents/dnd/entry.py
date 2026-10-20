"""跑团 Agent：DM 入场流程编排器（多世界）。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」，但按「用着什么生成什么」
收敛为**只生成世界地图与故事**：

1. 生成地图（:mod:`.gen_world`）
2. 生成故事（:mod:`.gen_story`）

一个世界可以容纳**多个故事**：

- :func:`run_entry_flow` 生成一个新世界与它的第一个故事；
- :func:`run_story_flow` 往已有世界里追加一个新故事（不重新生成世界地图）。

区域 / 城镇 / 自然地点 / 地下城 / 场景**不在开局预生成**，而是在 DM 决定玩家去往某地时
由 :mod:`.on_demand` 逐级补齐。

各生成 agent 只负责产出文档，**落库由本编排器负责**：世界写入注册表
（名称 / 一句话简介 / 主题）与该世界的文档库，故事带着名称与一句话简介写入该世界
的 ``story`` 索引。耗时的模型调用在这里串行执行，并通过 ``on_progress`` 回调
逐步汇报进度。
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
    list_stories,
    list_worlds,
    load_doc,
    new_story_id,
    new_world_id,
    save_doc,
    set_world_map,
    upsert_world,
)
from ..base import AgentResult, TrpgAgent, UsageEntry
from .gen_story import StoryAgent, story_agent
from .gen_world import WorldAgent, world_agent

__all__ = [
    "DEFAULT_OPTIONS",
    "EntryOptions",
    "EntryReport",
    "ProgressCallback",
    "run_entry_flow",
    "run_story_flow",
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
    #: 目标故事键；留空时自动生成新键（生成新故事）
    story_key: str = ""


#: 默认参数
DEFAULT_OPTIONS = EntryOptions()


@dataclass(slots=True)
class EntryReport:
    """入场流程的执行结果：生成了什么、哪里失败了。"""

    options: EntryOptions
    #: 实际使用的世界键 / 故事键
    world_key: str = ""
    story_key: str = ""
    regions: list[str] = field(default_factory=list)
    #: 世界是否成功落库
    world_saved: bool = False
    #: 世界档案（名称 / 一句话简介）
    world_title: str = ""
    world_intro: str = ""
    #: 故事是否成功落库
    story_saved: bool = False
    story_title: str = ""
    story_intro: str = ""
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
        if self.story_saved:
            lines.append(f"故事「{self.story_title}」已生成")
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


def _save_story(
    report: EntryReport, world_key: str, story_key: str, result: object, *, label: str
) -> None:
    """把故事落库（名称 / 简介缺省时补一个可用的，落库前剥掉首尾包裹符）。"""
    title = clean_name(str(getattr(result, "story_title", "") or "")) or (
        f"{label} {len(list_stories(world_key)) + 1}"
    )
    intro = clean_name(str(getattr(result, "story_intro", "") or ""))
    meta = {"intro": intro} if intro else None
    save_doc(world_key, "story", story_key, result.content, title=title, meta=meta)
    # 故事里的势力 / 关键节点登记进世界圣经（之后生成与主持都以它为准）
    register_entities(
        world_key, getattr(result, "entities", None) or [], source=story_key
    )
    report.story_key = story_key
    report.story_saved = True
    report.story_title = title
    report.story_intro = intro


async def run_entry_flow(
    *,
    options: EntryOptions | None = None,
    on_progress: ProgressCallback | None = None,
    world: WorldAgent = world_agent,
    story: StoryAgent = story_agent,
) -> EntryReport:
    """生成一个新世界与它的第一个故事，写入全局世界库。

    失败只记录到 :attr:`EntryReport.errors`，不会抛出；世界生成失败时直接返回
    （不建存档，但已生成的世界会保留在世界列表里，可再次选中后补故事）。
    """
    options = options or DEFAULT_OPTIONS
    world_key = options.world_key or new_world_id()
    report = EntryReport(options=options, world_key=world_key)

    # ① 世界地图
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
    report.world_intro = world_result.world_intro
    await _notify(
        on_progress, f"① 世界「{world_title}」已就绪（地区 {len(regions)} 个）"
    )

    # ② 世界起点与推力（第一个故事）
    story_key = options.story_key or new_story_id(world_key)
    await _notify(on_progress, "② 正在生成世界起点与推力……")
    story_result = await _safe(
        story,
        report,
        "世界起点与推力",
        world=world_result.content,
        theme=options.theme,
    )
    if story_result is not None:
        _save_story(report, world_key, story_key, story_result, label="故事")
        await _notify(on_progress, f"② 故事「{report.story_title}」已就绪")

    return report


async def run_story_flow(
    *,
    world_key: str,
    story_key: str = "",
    theme: str = "",
    on_progress: ProgressCallback | None = None,
    story: StoryAgent = story_agent,
) -> EntryReport:
    """往**已有世界**里追加一个新故事；世界文档缺失时直接失败。

    只生成故事，不重新生成 / 覆盖世界地图。世界键会原样记入报告，方便调用方
    建立（或继续使用）引用这个世界与这个故事的存档。
    """
    options = EntryOptions(world_key=world_key, story_key=story_key, theme=theme)
    report = EntryReport(options=options, world_key=world_key)
    report.world_saved = True  # 世界已存在，不算本次失败

    world_text = load_doc(world_key, "world", MAIN_DOC_KEY) or ""
    if not world_text:
        report.errors.append(f"世界 {world_key} 不存在或还没有世界地图")
        return report

    key = story_key or new_story_id(world_key)
    await _notify(on_progress, "正在生成新的故事设定……")
    story_result = await _safe(story, report, "故事", world=world_text, theme=theme)
    if story_result is not None:
        _save_story(report, world_key, key, story_result, label="故事")
        await _notify(on_progress, f"故事「{report.story_title}」已就绪")

    return report
