"""跑团 Agent：DM 入场流程编排器。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 入场流程」，但按「用着什么生成什么」
收敛为**只生成世界地图与故事**：

1. 生成地图（:mod:`.gen_world`）
2. 生成故事（:mod:`.gen_story`）

区域 / 城镇 / 自然地点 / 地下城 / 场景**不在开局预生成**，而是在 DM 决定玩家去往某地时
由 :mod:`.on_demand` 逐级补齐。

各生成 agent 只负责产出文档，**落库由本编排器负责**：结果写进全局共享世界库
（:mod:`...dnd.world_store`），相邻关系写进世界库索引。耗时的模型调用在这里串行执行，
并通过 ``on_progress`` 回调逐步汇报进度。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ...dnd.world_store import save_doc, set_world_map
from ..base import AgentResult, TrpgAgent, UsageEntry
from .gen_story import StoryAgent, story_agent
from .gen_world import WorldAgent, world_agent

__all__ = [
    "DEFAULT_OPTIONS",
    "EntryOptions",
    "EntryReport",
    "ProgressCallback",
    "run_entry_flow",
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


#: 默认参数
DEFAULT_OPTIONS = EntryOptions()


@dataclass(slots=True)
class EntryReport:
    """入场流程的执行结果：生成了什么、哪里失败了。"""

    options: EntryOptions
    regions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: 本流程里每一次模型调用的用量（供计费统计）
    usages: list[UsageEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        """给玩家 / 日志看的一行统计。"""
        lines = [
            "世界已生成" if self.ok else f"世界已生成（{len(self.errors)} 处失败）",
            f"地区 {len(self.regions)} 个",
        ]
        if self.errors:
            lines.append("失败：" + "；".join(self.errors))
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
    story: StoryAgent = story_agent,
) -> EntryReport:
    """生成世界地图与故事，并写入全局世界库。

    失败只记录到 :attr:`EntryReport.errors`，不会抛出；世界生成失败时直接返回。
    """
    options = options or DEFAULT_OPTIONS
    report = EntryReport(options=options)

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
    save_doc("world", "main", world_result.content, title="世界地图")
    regions = list(world_result.regions)
    set_world_map(regions, world_result.adjacency)
    report.regions = regions
    await _notify(on_progress, f"① 世界地图完成：地区 {len(regions)} 个")

    # ② 世界起点与推力
    await _notify(on_progress, "② 正在生成世界起点与推力……")
    story_result = await _safe(
        story,
        report,
        "世界起点与推力",
        world=world_result.content,
        theme=options.theme,
    )
    if story_result is not None:
        save_doc("story", "main", story_result.content, title="世界起点与推力设定")
        await _notify(on_progress, "② 世界起点与推力完成")

    return report
