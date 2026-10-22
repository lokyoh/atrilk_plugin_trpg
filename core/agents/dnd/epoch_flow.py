"""纪元编排：把「一章结束了」变成「一个纪元结算了」。

这一层在 :mod:`...agents.dnd` 而不是 :mod:`...dnd.epoch`，因为它要**读存档**
（事件状态、事件日志、世界圣经）、**调模型**（给称号命名）、**写世界库**
（称号进圣经）。纯判定与代价施加都在 :mod:`...dnd.epoch` 里，那边一概不碰存档。

一次结算走五步：

1. 读事件状态与演化轨，算出这个纪元的事实（:func:`~...dnd.epoch.epoch_facts`）；
2. :func:`~...dnd.epoch.verdict` 收束判定——**纯函数**，同样的事实给同样的结论；
3. 达成的结局 → 记进存档并把 ``wrapping_up`` 立起来（本局收）；
   未达成 → :func:`~...dnd.epoch.apply_epoch_cost` 给代价（落在演化轨上）；
4. :class:`~.gen_epoch.EpithetAgent` 按纪元内的实际行为命名一次，失败用程序兜底；
5. 称号同时进存档与**世界圣经**（``kind="person"``），所以它会出现在
   玩家向的世界背景手册里，也会被后面的事件生成器当成既有专有名词复用。

**触发时机是「一章结束」而不是「织下一章」**：织下一章时结算会让第 4 章的
结算晚到第 5 章才发生，那样「第 4 章触发一次结算」就不成立了。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ...dnd.encounter_budget import (
    Performance,
    performance_factor,
    summarize_performance,
)
from ...dnd.epoch import (
    EPOCH_COSTS,
    EpithetGrant,
    EpochFacts,
    apply_epoch_cost,
    epoch_facts,
    epoch_index,
    epoch_span,
    fallback_epithet,
    is_epoch_end,
    load_epoch,
    record_epithet,
    store_epoch,
    verdict,
)
from ...dnd.event_progress import EventProgress, Recipe, mark_done
from ...dnd.evolution import load_evolution, pressure_bonus, store_evolution
from ...dnd.save_store import Save, log_event, recent_events, save_save
from ...dnd.world_bible import Entity, register_entities
from ...dnd.world_store import MAIN_DOC_KEY, load_doc
from ..base import UsageEntry
from .gen_epoch import EpithetAgent, epithet_agent

__all__ = [
    "EpochOutcome",
    "advance_event",
    "epoch_deeds",
    "settle_epoch",
]

#: 进度回调
ProgressCallback = Callable[[str], "Awaitable[None] | None"]

#: 参与难度自适应的事件条数上限。取最近 200 条：够覆盖两三次战斗的
#: 回合与倒地记录，又不会把整局日志都读进来（存档跑久了事件文件会很长）。
_PERF_EVENT_LIMIT = 200

#: 列进行为事实时读多少条事件日志。与上面同一档，两处刻意取同一个数。
_DEED_EVENT_LIMIT = 200

#: 事实清单里各类各取几条。刻意都很少——称号的输入是「几个抓手」，
#: 不是纪元流水账；给 40 条行为模型只会复述其中一条。
_DEED_TITLES = 6
_DEED_CLUES = 6
_DEED_LOG = 4
_DEED_MARKS = 3


@dataclass(slots=True)
class EpochOutcome:
    """一次纪元结算的结果。"""

    #: 结算的纪元序号（1 起）；没触发结算时为 0
    index: int = 0
    facts: EpochFacts = field(default_factory=EpochFacts)
    #: 达成的结局名（空 = 未达成，代价已施加）
    ending: str = ""
    #: 施加的代价（给 DM 看的一行行说明）
    costs: list[str] = field(default_factory=list)
    #: 授予的称号
    epithet: str = ""
    #: 生成称号时用到的问题（空 = 一次成功）
    problems: list[str] = field(default_factory=list)
    #: 本局是否就此收（达成结局时为真）
    finished: bool = False
    usages: list[UsageEntry] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        return self.index > 0

    def summary(self) -> str:
        """给 DM 看的结算摘要（也会拼进玩家消息）。"""
        if not self.settled:
            return ""
        head = f"第{self.index}纪元结算（截至第 {self.facts.last_event} 章）"
        if self.ending:
            return f"{head}：达成结局【{self.ending}】。称号「{self.epithet}」。"
        tail = (
            f"代价：{'；'.join(self.costs)}" if self.costs else "这一纪元没有留下代价"
        )
        return f"{head}：未达成收束。称号「{self.epithet}」。{tail}。"


# --------------------------------------------------------------------------- #
# 结束一个事件（生产路径上「completed 推进」的那一步）
# --------------------------------------------------------------------------- #


def _perf_of(
    save: Save, *, enabled: bool = True, difficulty: str = "medium"
) -> tuple[Performance, float]:
    """从事件日志算表现统计与系数。

    第一次读世界时事件文件可能还不存在，``recent_events`` 会返回空列表——
    那是正常的（新档没有任何历史），系数为 0 表示「按基准难度来」。
    """
    try:
        events = recent_events(save, limit=_PERF_EVENT_LIMIT)
    except OSError as e:  # 事件文件读不到不该拦住开局
        log.warning(f"读事件日志失败，表现系数按 0 处理：{e}")
        events = []
    perf = summarize_performance(events)
    return perf, performance_factor(perf, difficulty=difficulty, enabled=enabled)


def advance_event(
    save: Save,
    *,
    difficulty: str = "medium",
    note: str = "",
    done: int = 0,
    total: int = 0,
) -> str:
    """记下「一件事了结了」，推进「已完成事件数」，返回进手册的那一句。

    唯一的生产写入点在 :func:`~...dnd.event_progress.mark_done`，这里只是带上
    **表现系数**——难度序号轨、威胁层级、世界演化轨、纪元结算全靠这个数往前走；
    没人推它，整局就会一直停在第 1 个事件。

    表现系数要在记录**之前**算：那一步读最近的事件日志，顺序上刚好还是
    刚打完的这件事。是否启用自适应从存档的织法里读。

    :param done: 这件事了结了几个局面（与 ``total`` 一起算本件完成率）
    """
    rec = Recipe.from_dict((save.progress or {}).get("_recipe") or {})
    _perf, factor = _perf_of(save, enabled=rec.adapt, difficulty=difficulty)
    return mark_done(save, factor=factor, note=note, done=done, total=total)


# --------------------------------------------------------------------------- #
# 事实清单（给称号生成器当输入）
# --------------------------------------------------------------------------- #


def epoch_deeds(save: Save, state: EventProgress, facts: EpochFacts) -> list[str]:
    """列出一个纪元里实际发生的事——**全是程序提取的，不调模型**。

    这些是称号的「抓手」：战斗与倒地读事件日志，事件与线索读事件状态，
    痕迹与账期读演化轨。刻意不给原始流水，模型看到的是结论而不是素材。
    """
    first = facts.first_event
    last = facts.last_event
    deeds: list[str] = []

    if facts.events:
        window = state.history[first - 1 : last]
        if window:
            deeds.append("打过的事件：" + "、".join(window[-_DEED_TITLES:]))

    try:
        events = recent_events(save, limit=_DEED_EVENT_LIMIT)
    except OSError as e:
        log.warning(f"读事件日志失败，称号事实缺战斗统计：{e}")
        events = []
    if events:
        from ...dnd.encounter_budget import summarize_performance

        perf = summarize_performance(events)
        bits: list[str] = []
        if perf.combats:
            bits.append(
                f"打了 {perf.combats} 场硬仗（平均 {perf.rounds_avg:.1f} 回合）"
            )
        if perf.knockdowns:
            bits.append(f"有 {perf.knockdowns} 人次被打倒过")
        if perf.long_rests:
            bits.append(f"靠 {perf.long_rests} 次长休才续上前")
        if bits:
            deeds.append("、".join(bits) + "。")

    if state.clues:
        deeds.append("查清的事：" + "、".join(state.clues[-_DEED_CLUES:]))

    evo = load_evolution(save)
    if evo.factions:
        top = evo.dominant_faction()
        if top is not None:
            deeds.append(
                f"{top.name}（{top.region or '本地'}）现在的位置：{top.stage_label}"
            )
    marked = [p for p in evo.places if p.marks][-_DEED_MARKS:]
    if marked:
        deeds.append(
            "留下的痕迹："
            + "；".join(f"{p.name}（{'、'.join(p.marks)}）" for p in marked)
        )
    if evo.open_deadlines():
        deeds.append(
            "还压着的账："
            + "；".join(d.line() for d in evo.open_deadlines()[:_DEED_LOG])
        )
    if evo.log:
        deeds.append("最近：" + "；".join(evo.log[-_DEED_LOG:]))
    return deeds


# --------------------------------------------------------------------------- #
# 结算
# --------------------------------------------------------------------------- #


async def settle_epoch(
    save: Save,
    *,
    difficulty: str = "medium",
    agent: EpithetAgent = epithet_agent,
    on_progress: ProgressCallback | None = None,
) -> EpochOutcome:
    """章数跨过纪元边界时做一次收束判定并发称号。

    **不跨边界就直接返回空的 :class:`EpochOutcome`**（``settled`` 为假），
    所以调用方可以在每章结束时无条件调它，不必自己算边界——边界判定
    （:func:`~...dnd.epoch.is_epoch_end`）刻意要求 ``completed > 0``，
    这样开局的第一章不会误触发。
    """
    from ...dnd.event_progress import _load

    outcome = EpochOutcome()
    event_state = _load(save)
    ep_state = load_epoch(save)
    completed = event_state.completed
    if not is_epoch_end(completed, ep_state.length):
        return outcome

    evo = load_evolution(save)
    index = epoch_index(completed, ep_state.length)
    first, last = epoch_span(index, ep_state.length)
    facts = epoch_facts(
        event_state,
        evo,
        index=index,
        length=ep_state.length,
        pressure=pressure_bonus(evo),
    )
    outcome.index = index
    outcome.facts = facts

    decision = verdict(facts)
    ep_state.settled_epochs = max(ep_state.settled_epochs, index)
    ep_state.last_costs = []

    if decision.ending:
        outcome.ending = decision.ending
        ep_state.ending = decision.ending
        ep_state.endings.append(
            {"index": index, "ending": decision.ending, "event": completed}
        )
    else:
        # 未达成**不硬拦**：把代价落在演化轨上，然后照常进下一纪元
        lines = apply_epoch_cost(evo, decision.costs, event_no=completed)
        outcome.costs = lines
        ep_state.last_costs = list(lines)
        store_evolution(save, evo)
        log_event(
            save, "note", f"第{index}纪元未达成收束：" + "；".join(lines), actor="dm"
        )

    # -- 称号：一次模型调用，失败就用程序兜底 -------------------------------- #
    name = ""
    deeds = epoch_deeds(save, event_state, facts)
    if agent is not None:
        try:
            result = await agent.run(
                world=load_doc(save.world_key, "world", MAIN_DOC_KEY) or "",
                deeds="\n".join(deeds) or "（这个纪元没有留下什么记录）",
                verdict=(
                    f"结局【{decision.ending}】"
                    if decision.ending
                    else f"未达成（{decision.reason}）"
                ),
            )
            outcome.usages.extend(getattr(result, "usages", None) or [])
            name = getattr(result, "epithet", "") or ""
            outcome.problems.extend(getattr(result, "problems", None) or [])
        except Exception as e:  # 称号是叙事装饰，生成失败不该拦住结算
            log.warning(f"称号生成失败，改用兜底：{str_traceback(e)}")
            outcome.problems.append(f"称号生成失败：{e}")
    if not name:
        name = fallback_epithet(facts)
    outcome.epithet = name

    record_epithet(
        ep_state,
        EpithetGrant(
            index=index,
            name=name,
            event_no=completed,
            verdict=decision.ending or f"未达成（{decision.reason}）",
        ),
    )
    store_epoch(save, ep_state)

    # -- 称号进世界圣经（只登记、失败不影响结算） ---------------------------- #
    try:
        register_entities(
            save.world_key,
            [
                Entity(
                    name=name,
                    kind="person",
                    region=facts.top_faction or "",
                    note=f"第{index}纪元（截至第 {completed} 章）留下的称号",
                    source=f"epoch:{index}",
                )
            ],
        )
    except Exception as e:
        log.warning(f"称号写入世界圣经失败：{str_traceback(e)}")
        outcome.problems.append(f"称号未入圣经：{e}")

    # 达成结局 → 本局收。走的是玩家 ``/跑团.结档`` 立起来的同一条出口
    # （见 ``game._ending_text``），不另造一条，所以 DM 那边的规矩不用改。
    if decision.ending:
        save.progress["ending"] = decision.ending
        outcome.finished = True
    save_save(save)
    log_event(save, "note", f"第{index}纪元结算：{outcome.summary()}", actor="dm")
    await _notify(on_progress, outcome.summary())
    return outcome


async def _notify(on_progress: ProgressCallback | None, message: str) -> None:
    """调用进度回调；回调出错只记日志。"""
    if on_progress is None or not message:
        return
    try:
        result = on_progress(message)
        if hasattr(result, "__await__"):
            await result
    except Exception as e:
        log.warning(f"纪元结算进度回调失败：{str_traceback(e)}")


#: 供 :mod:`...dnd.epoch` 的文档与测试引用：代价种类必须与纯层一致
COST_KINDS = EPOCH_COSTS
