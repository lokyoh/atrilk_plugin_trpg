"""事件进度：存档里那把刻度尺。

**这一模块只记刻度，不记内容。** 事件本身（局面、事实、收束、时钟）长在
:class:`~.events.EventSlot` 与 :class:`~.module_graph.ModuleGraph` 里，存在事件池中；
这里存的是**跨事件才用得上的几个数**——难度序号轨、威胁层级、世界演化轨、纪元结算
全拿这些数当刻度，所以它们必须跟着事件一起往前走。

存什么、不存什么：

===============  ==================================================
字段              谁在读
===============  ==================================================
``completed``     :func:`~.epoch.epoch_index`（走到第几纪元）、
                 :func:`~.encounter_budget.event_cr`（难度序号轨）
``progresses``   :func:`~.epoch.epoch_facts`（这一纪元的目标完成率）
``history``      :func:`~.epoch.epoch_deeds`（称号的抓手）、玩家手册
``clues``        玩家手册「查清的事」
``last_factor``  DM 侧难度展示
``current``      玩家手册「眼下这件事」
``open_nodes``   玩家手册「还开着的局面」
===============  ==================================================

**为什么镜像一份事件摘要到这里**（``current`` / ``open_nodes``）：
玩家手册由 :mod:`.world_brief` 生成，那是**纯函数层**，只能读存档，
不该反过来去摸编排层的事件池。所以编排层在织成、推进、了结时把一句摘要写进
``progress["_event"]``，手册只读这一处——分层靠这个镜像维持，
代价是两处要同步（写入点只有三个，见 :func:`set_current`）。

数据与展示的分工：本模块负责**存与读**，
「第几个事件」「完成率」这些算法在 :mod:`.epoch` 与 :mod:`.encounter_budget` 里。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ARC_LABELS",
    "EventProgress",
    "Recipe",
    "event_summary",
    "mark_done",
    "note_clue",
    "set_current",
]


#: 事件骨架。键是内部用的短名，值是给 DM 看的中文标签。
#:
#: 生成器（:mod:`...agents.dnd.gen_event`）按骨架给事件定调子，
#: 织出来之后这个值只用于展示，不参与任何判定——
#: 一件「调查」的事里照样可以打一场，那是节点层的事。
ARC_LABELS: dict[str, str] = {
    "investigation": "调查",
    "surveillance": "盯梢",
    "audit": "审计",
    "heist": "潜入",
    "infiltration": "渗透",
    "defense": "守点",
    "holdout": "据守",
    "ritual": "仪式",
    "decoding": "解读",
    "confrontation": "对峙",
    "ambush": "伏击",
    "social": "交涉",
    "bargain": "交易",
}


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EventProgress:
    """存档里的事件进度，落在 ``progress["_event"]``。

    以下划线开头，所以 :meth:`~.save_store.Save.to_dict` 之外的玩家侧文本
    过滤会挡住它，不会把内部状态原样发给玩家。
    """

    #: 已了结的事件数（当前事件 = 这个数 + 1）
    completed: int = 0
    #: 当前事件的一句话（标题 + 规模档，由编排层写）
    current: str = ""
    #: 当前事件还开着的局面名（玩家视角：还能做什么）
    open_nodes: list[str] = field(default_factory=list)
    #: 当前事件已经了结的局面名
    done_nodes: list[str] = field(default_factory=list)
    #: 已经查清的事（线索本）
    clues: list[str] = field(default_factory=list)
    #: 上一件事的表现系数，供 DM 与手册展示
    last_factor: float = 0.0
    #: 每件事收尾时记下的 ``[已了结局面数, 总局面数]``（顺序记，第 N 件在下标 N-1）。
    #: 纪元收束判定要「这个纪元的完成率」，跨事件累加就得有历史，
    #: 否则只能看到最后一件的完成情况。
    progresses: list[list[int]] = field(default_factory=list)
    #: 累计了结的事件标题（一行一个，进手册）
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "current": self.current,
            "open_nodes": list(self.open_nodes),
            "done_nodes": list(self.done_nodes),
            "clues": list(self.clues),
            "last_factor": self.last_factor,
            "progresses": [list(x) for x in self.progresses],
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: Any) -> EventProgress:
        d = data if isinstance(data, dict) else {}
        return cls(
            completed=int(d.get("completed") or 0),
            current=str(d.get("current") or ""),
            open_nodes=[str(x) for x in (d.get("open_nodes") or [])],
            done_nodes=[str(x) for x in (d.get("done_nodes") or [])],
            clues=[str(x) for x in (d.get("clues") or [])],
            last_factor=float(d.get("last_factor") or 0.0),
            progresses=[
                [int(x[0]), int(x[1])]
                for x in (d.get("progresses") or [])
                if isinstance(x, list | tuple) and len(x) >= 2
            ],
            history=[str(x) for x in (d.get("history") or [])],
        )


@dataclass(slots=True)
class Recipe:
    """一局怎么跑：开局那一刻定下来、整局不变的参数。

    只有**真被读到**的字段才留在这里（见 :func:`~...agents.dnd.epoch_flow.advance_event`
    与 :func:`~.gamemode.start_mode`）。曾经还有骨架权重、去重窗口、元素配额
    这一堆「织法」参数，它们唯一的消费者是早已下线的织卡路径——
    没人读的参数等于没有参数，留着只会让人以为调了有用。
    """

    #: 是否启用表现系数自适应难度
    adapt: bool = True
    #: 一件事几个推进点
    story_points: int = 3

    def to_dict(self) -> dict[str, Any]:
        """序列化：写回 ``save.progress["_recipe"]`` 用。

        **写全部字段**，包括取默认值的那些——``from_dict`` 的缺省与 dataclass
        的缺省并不完全一致（见那里的说明），少写一个字段就会让那一项静默变成另一个值。
        """
        return {"adapt": self.adapt, "story_points": self.story_points}

    @classmethod
    def from_dict(cls, data: Any) -> Recipe:
        d = data if isinstance(data, dict) else {}
        return cls(
            adapt=bool(d.get("adapt", True)),
            # 缺省必须与 dataclass 的 ``story_points = 3`` 一致。
            # 早先 ``parallel_every`` 写成 ``or 0``，于是「存档里没有这个键」
            # 被读成「不要并行支线」——而那时 ``_recipe`` 一个写入点都没有，
            # 等于那条支线从来没触发过。缺省值必须等于想让它跑起来的那个值。
            story_points=max(1, min(int(d.get("story_points", 3)), 12)),
        )


# --------------------------------------------------------------------------- #
# 存档读写
# --------------------------------------------------------------------------- #


def _load(save: Any) -> EventProgress:
    raw = (getattr(save, "progress", None) or {}).get("_event")
    return EventProgress.from_dict(raw or {})


def _store(save: Any, state: EventProgress) -> None:
    progress = getattr(save, "progress", None)
    if progress is None:
        return
    progress["_event"] = state.to_dict()


def set_current(
    save: Any,
    title: str = "",
    *,
    open_nodes: Iterable[str] = (),
    done_nodes: Iterable[str] = (),
) -> None:
    """把「眼下这件事」的摘要写进存档（玩家手册的镜像）。

    三个写入点：织成新事件、装上预写事件、结算/放弃一个局面之后。
    **传空标题表示清掉**（事件了结或放弃时），手册那一节就会整块消失——
    玩家该看到的是「这一带眼下没有追着不放的事」。
    """
    state = _load(save)
    state.current = str(title or "").strip()
    if not state.current:
        state.open_nodes = []
        state.done_nodes = []
    else:
        state.open_nodes = [str(x).strip() for x in open_nodes if str(x).strip()]
        state.done_nodes = [str(x).strip() for x in done_nodes if str(x).strip()]
    _store(save, state)


def note_clue(save: Any, clue: str) -> None:
    """记一条已经查清的事（线索本，进玩家手册）。"""
    state = _load(save)
    clue = str(clue or "").strip()
    if clue and clue not in state.clues:
        state.clues.append(clue)
    _store(save, state)


def mark_done(
    save: Any,
    *,
    factor: float = 0.0,
    note: str = "",
    done: int = 0,
    total: int = 0,
) -> str:
    """记下一件事了结了，推进「已完成事件数」。返回进 ``history`` 的那一句。

    **这是 :attr:`EventProgress.completed` 唯一的生产写入点**——
    难度序号轨、威胁层级、世界演化轨、纪元结算全靠这个数往前走；
    没人推它，整局就会一直停在第 1 个事件。

    :param factor: 这件事的表现系数，留作下一件的难度输入。
    :param note: 一句话摘要，进 ``history`` 与玩家手册；留空则按序号兜底。
    :param done: 了结了几个局面（与 ``total`` 一起算本件完成率）
    :param total: 这件事一共有几个局面
    """
    state = _load(save)
    state.completed += 1
    state.last_factor = float(factor)
    state.progresses.append([max(0, int(done)), max(0, int(total))])
    line = str(note).strip() or f"第 {state.completed} 件事"
    state.history.append(line)
    state.current = ""
    state.open_nodes = []
    state.done_nodes = []
    _store(save, state)
    return line


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #


def event_summary(save: Any, *, for_player: bool = True) -> str:
    """当前事件的摘要文本。

    :param for_player: 玩家侧只给「眼下这件事 / 还开着的局面 / 查清的事 /
        已经过去的事」，不泄露表现系数——那是 DM 侧的东西。
    """
    state = _load(save)
    lines: list[str] = []
    if state.current:
        lines.append(f"【第 {state.completed + 1} 件事】{state.current}")
        if state.open_nodes:
            lines.append("　还开着的局面：" + "、".join(state.open_nodes))
        if state.done_nodes:
            lines.append("　已经了结：" + "、".join(state.done_nodes))
    if state.clues:
        lines.append("查清的事：" + "、".join(state.clues))
    if state.history:
        lines.append("已经过去的事：" + "、".join(state.history))
    if not for_player and state.completed > 0:
        lines.append(
            f"【难度刻度】已了结 {state.completed} 件事，上一件表现系数 "
            f"{state.last_factor:+.2f}"
        )
    return "\n".join(lines)
