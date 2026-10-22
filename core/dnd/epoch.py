"""纪元：每若干章一个纪元，纪元末做一次**收束判定**，然后发称号。

===============  ==================  ==============================================
本模块            负责                不负责
===============  ==================  ==============================================
纯逻辑层          纪元边界、收束判定  读存档、调模型、写圣经
:mod:`.event_progress`  事件刻度        （那些在 :mod:`...agents.dnd.epoch_flow`）
===============  ==================  ==============================================

**为什么收束判定必须是纯函数**：判定要回答「这个纪元算不算达成了」，而答案只应该
取决于可数的事实——目标勾了几条、期限逾期几条、有没有清偿过旧账。让它可枚举、
可在测试里断言，比让模型「读一遍历史然后感觉一下」可靠得多，也便宜得多。

**为什么结局条件长这样**：旧设计里的 ``endings`` 挂在 frame 文档上，而 frame 与 story
都已经下线（事件卡是唯一机制），所以条件必须换成**当前程序真有的事实**。
现在的三个出口覆盖了所有情况，且互斥：

============  ==========================================  ==============
出口          条件                                        之后
============  ==========================================  ==============
``凯旋``      目标全勾 + 没有逾期的账                本局收
``清偿``      目标全勾 + 清偿过至少一桩旧账            本局收
未达成        其余（目标没勾完 / 有逾期）      给代价，进下一纪元
============  ==========================================  ==============

注意「有逾期」即便目标全勾也**不算达成**：把事做完了但欠着账，是下一纪元的压力来源，
不是收束。这是有意的——代价机制得有事情可做。

代价（:func:`apply_epoch_cost`）刻意**全部落在世界演化轨上**
（:mod:`.evolution`），不另造一套加压系统：势力得势改位置、期限收紧改 ``due``、
地点损失加痕迹，于是玩家能反推「这几个纪元我们拖了几件事没办」，
而 :func:`~.evolution.pressure_bonus` 是它唯一的数值出口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evolution import FACTION_STAGES, PLACE_MARKS, WorldEvolution, mark_place

__all__ = [
    "DEFAULT_EPOCH_LENGTH",
    "ENDINGS",
    "EPOCH_COSTS",
    "EPOCH_KEY",
    "EpithetGrant",
    "EpochFacts",
    "EpochState",
    "apply_epoch_cost",
    "epoch_facts",
    "epoch_index",
    "epoch_length_of",
    "epoch_span",
    "epoch_text",
    "fallback_epithet",
    "is_epoch_end",
    "load_epoch",
    "record_epithet",
    "store_epoch",
    "verdict",
]

#: 纪元状态在 ``save.progress`` 里的键名。下划线开头 = 内部键。
EPOCH_KEY = "_epoch"

#: 默认几个章算一个纪元。做成可调（见 :func:`epoch_length_of`）是因为
#: 节奏是口味问题：四章一段打得很紧，十章一段则更像一部长篇的卷。
DEFAULT_EPOCH_LENGTH = 4


# --------------------------------------------------------------------------- #
# 边界
# --------------------------------------------------------------------------- #


def epoch_index(completed: int, length: int = DEFAULT_EPOCH_LENGTH) -> int:
    """已完成的 ``completed`` 章落在第几个纪元（1 起）。

    「第几章」是 1 起的，「第几纪元」也是——所以第 4 章（``completed=4``）
    是**第一个**纪元的最后一章，不是第二个纪元的第一章。
    """
    step = max(1, int(length))
    done = int(completed)
    return max(1, (done - 1) // step + 1) if done > 0 else 1


def epoch_span(index: int, length: int = DEFAULT_EPOCH_LENGTH) -> tuple[int, int]:
    """第 ``index`` 个纪元覆盖的事件区间（闭区间，1 起）。"""
    step = max(1, int(length))
    idx = max(1, int(index))
    return ((idx - 1) * step + 1, idx * step)


def is_epoch_end(completed: int, length: int = DEFAULT_EPOCH_LENGTH) -> bool:
    """``completed`` 章是不是恰好某个纪元的末章。

    只在**刚完成**一章的那一刻为真（``completed`` 从 0 涨上来的那一刻）；
    第 0 章不算，所以开局不会误触发结算。
    """
    step = max(1, int(length))
    done = int(completed)
    return done > 0 and done % step == 0


def epoch_length_of(data: Any) -> int:
    """从 ``progress["_epoch"]`` 里取纪元长度；没配就用默认。

    **刻意不放在 :class:`~.event_progress.Recipe` 里**：纪元长度是**玩法**的一部分，
    由 :func:`~.gamemode.start_mode` 一次性写进 ``progress["_epoch"]``，
    而织法只管难度怎么算。两件事分开，改玩法不用碰难度公式。
    """
    raw = data if isinstance(data, dict) else {}
    try:
        step = int(raw.get("length") or 0)
    except (TypeError, ValueError):
        step = 0
    return step if step > 0 else DEFAULT_EPOCH_LENGTH


# --------------------------------------------------------------------------- #
# 事实
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EpochFacts:
    """一个纪元里可数的事实。**收束判定只看这些，不看别的。**"""

    #: 纪元序号（1 起）
    index: int = 1
    #: 纪元长度（章）
    length: int = DEFAULT_EPOCH_LENGTH
    #: 本纪元覆盖的事件区间
    first_event: int = 1
    last_event: int = 0
    #: 本纪元实际完成的章数
    events: int = 0
    #: 完成条件：已勾 / 总数（跨本纪元所有章累加）
    done: int = 0
    total: int = 0
    #: 未结的期限数 / 其中已逾期的 / 已清偿的
    open_deadlines: int = 0
    overdue: int = 0
    settled: int = 0
    #: 位置最高的势力（名字与档位）
    top_faction: str = ""
    stage: int = 0
    #: 各地累积的痕迹条数
    marks: int = 0
    #: 世界压力（CR 档，来自演化轨）
    pressure: int = 0

    @property
    def rate(self) -> float:
        """目标完成率。没有完成条件时按「无可勾」算 1.0——那是刻意的：
        一章都没有硬性目标不该被读成「一章都没做成」。
        """
        return 1.0 if self.total <= 0 else self.done / self.total

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.done >= self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "length": self.length,
            "first_event": self.first_event,
            "last_event": self.last_event,
            "events": self.events,
            "done": self.done,
            "total": self.total,
            "open_deadlines": self.open_deadlines,
            "overdue": self.overdue,
            "settled": self.settled,
            "top_faction": self.top_faction,
            "stage": self.stage,
            "marks": self.marks,
            "pressure": self.pressure,
        }


def epoch_facts(
    event_state: Any,
    evolution: WorldEvolution | None = None,
    *,
    index: int = 1,
    length: int = DEFAULT_EPOCH_LENGTH,
    pressure: int = 0,
) -> EpochFacts:
    """从事件状态与演化轨算出这个纪元的事实（**不碰存档**）。

    :param event_state: :class:`~.event_progress.EventProgress`，只用它的
        ``completed`` 与 ``progresses``（每章收尾时记下的 ``[已勾, 总数]``）。
    :param evolution: 世界演化轨；缺省按空轨道算（自检 / 单测友好）。
    """
    step = max(1, int(length))
    idx = max(1, int(index))
    first, last = epoch_span(idx, step)
    completed = int(getattr(event_state, "completed", 0) or 0)

    # 只取本纪元覆盖到的那几章的进度记录（progresses 是从第 1 章起顺序记的）
    records = list(getattr(event_state, "progresses", None) or [])
    window = records[max(0, first - 1) : last]
    done = sum(int(r[0]) for r in window if len(r) >= 2)
    total = sum(int(r[1]) for r in window if len(r) >= 2)

    evo = evolution or WorldEvolution()
    top = evo.dominant_faction()
    facts = EpochFacts(
        index=idx,
        length=step,
        first_event=first,
        last_event=last,
        events=len(window),
        done=done,
        total=total,
        open_deadlines=len(evo.open_deadlines()),
        overdue=sum(1 for d in evo.deadlines if not d.settled and d.overdue > 0),
        settled=sum(1 for d in evo.deadlines if d.settled),
        top_faction=top.name if top else "",
        stage=top.stage if top else 0,
        marks=sum(len(p.marks) for p in evo.places),
        pressure=int(pressure),
    )
    # 纪元覆盖到的章不该超过实际完成的章数（局还没打到那里）
    facts.events = max(0, min(facts.events, completed - first + 1))
    return facts


# --------------------------------------------------------------------------- #
# 收束判定
# --------------------------------------------------------------------------- #

#: 结局表。键是出口名，值是 (标题, 达成后给玩家 / DM 看的一句话)。
ENDINGS: dict[str, tuple[str, str]] = {
    "凯旋": ("凯旋", "这一纪元该做的事都做完了，一笔烂账也没留下。"),
    "清偿": ("清偿", "做完了全部目标，还顺手了结了一桩压在前面的旧事。"),
}

#: 未达成时的代价种类，按施加顺序（先位置、后账期、最后现场）
EPOCH_COSTS: tuple[str, ...] = ("势力得势", "期限收紧", "地点损失")

#: 未达成原因的键 -> 施加哪些代价
_COST_BY_REASON: dict[str, tuple[str, ...]] = {
    "incomplete": ("势力得势", "地点损失"),
    "overdue": ("期限收紧",),
    "both": ("势力得势", "期限收紧", "地点损失"),
}


@dataclass(slots=True)
class Verdict:
    """一次收束判定的结果。"""

    #: 达成的结局名（``ENDINGS`` 的键）；空 = 未达成
    ending: str = ""
    title: str = ""
    summary: str = ""
    #: 未达成的原因键（``incomplete`` / ``overdue`` / ``both``）
    reason: str = ""
    #: 该施加的代价（:data:`EPOCH_COSTS` 的子集）
    costs: tuple[str, ...] = ()

    @property
    def achieved(self) -> bool:
        return bool(self.ending)


def verdict(facts: EpochFacts) -> Verdict:
    """收束判定。**纯函数：同样的事实永远给同样的结论。**

    判定顺序有讲究——先看「目标做完了吗」，再看「有没有烂账」：

    1. 目标没全勾 → 未达成（``incomplete``）；
    2. 全勾了但有逾期 → 未达成（``overdue``）：做完了但欠着账，
       那是下一纪元的压力，不是收束；
    3. 全勾、无逾期、清偿过旧账 → ``清偿``（比 ``凯旋`` 更具体，先判它）；
    4. 全勾、无逾期 → ``凯旋``。
    """
    if not facts.complete:
        reason = "incomplete"
    elif facts.overdue > 0:
        reason = "overdue"
    elif facts.settled > 0:
        reason = ""
    else:
        reason = ""

    if reason:
        return Verdict(reason=reason, costs=_COST_BY_REASON.get(reason, ()))

    key = "清偿" if facts.settled > 0 else "凯旋"
    title, summary = ENDINGS[key]
    return Verdict(ending=key, title=title, summary=summary)


def apply_epoch_cost(
    evolution: WorldEvolution,
    costs: tuple[str, ...] | list[str],
    *,
    event_no: int,
) -> list[str]:
    """把代价施加到演化轨上，返回给玩家 / DM 看的一行行说明。

    刻意**不加新数值**：三种代价各自改的是演化轨已有的字段，所以
    「世界压力」那条唯一的数值出口（:func:`~.evolution.pressure_bonus`）
    会自动跟着走，玩家反推得出来。

    施加不出来的代价（没势力 / 没期限 / 没地点）**静默跳过**——
    空口说「势力得势」而世界里根本没有势力，是往存档里塞假信息。
    """
    lines: list[str] = []
    evo = evolution
    for cost in costs or ():
        if cost == "势力得势":
            top = evo.dominant_faction()
            if top is None:
                continue
            if top.stage < len(FACTION_STAGES) - 1:
                top.stage += 1
                lines.append(f"{cost}：{top.line()}")
            else:
                # 档位走完了还有代价可加？那就记「盘得更深」——压力公式读的就是它
                top.mature += 1
                lines.append(f"{cost}：{top.name}已经不需要明着来了")
        elif cost == "期限收紧":
            hit = False
            for deadline in evo.open_deadlines():
                # 不收紧到「本章之内就到期」——那不是代价，是立刻引爆
                deadline.due = max(event_no + 1, deadline.due - 1)
                lines.append(f"{cost}：{deadline.line()}")
                hit = True
            if not hit:
                continue
        elif cost == "地点损失":
            marked = [p for p in evo.places if p.marks]
            if not marked:
                continue
            place = max(marked, key=lambda p: (len(p.marks), -p.since))
            mark = mark_place(
                evo,
                place.name,
                PLACE_MARKS[len(place.marks) % len(PLACE_MARKS)],
                event_no=event_no,
            )
            lines.append(f"{cost}：{mark.name}：{mark.marks[-1]}")
        else:
            continue
    for line in lines:
        evo.note(line)
    return lines


# --------------------------------------------------------------------------- #
# 称号
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EpithetGrant:
    """一次称号授予。"""

    #: 纪元序号
    index: int = 1
    #: 称号正文
    name: str = ""
    #: 授予时的章号
    event_no: int = 0
    #: 判定出口（结局名或代价摘要）
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "event": self.event_no,
            "verdict": self.verdict,
        }

    @classmethod
    def from_dict(cls, data: Any) -> EpithetGrant | None:
        d = data if isinstance(data, dict) else {}
        name = str(d.get("name") or "").strip()
        if not name:
            return None
        return cls(
            index=int(d.get("index", 1) or 1),
            name=name,
            event_no=int(d.get("event", 0) or 0),
            verdict=str(d.get("verdict") or ""),
        )


def fallback_epithet(facts: EpochFacts) -> str:
    """程序兜底的称号。

    **必须有兜底**：验收标准是「称号进圣经」，那是存档与圣经层面的承诺，
    不该因为一次模型调用失败就落空。兜底句式刻意平淡——它只保证有名字，
    好听与否交给模型。
    """
    who = facts.top_faction or "本地"
    return f"第{facts.index}纪元里{who}最记住的人"


# --------------------------------------------------------------------------- #
# 存档状态
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EpochState:
    """纪元轨的存档状态，落在 ``progress["_epoch"]``。

    设计稿写的是 ``progress["_frame"]["epithets"]``——frame 与 story 一起下线了，
    所以纪元自成一个键：它有自己的一组字段（长度、序号、称号、结局），
    硬塞进事件状态里会让那儿的字段变成两件不相关的事。
    """

    #: 纪元长度（章）；缺省 4
    length: int = DEFAULT_EPOCH_LENGTH
    #: 已结算的纪元数
    settled_epochs: int = 0
    #: 已授予的称号（新→旧）
    epithets: list[EpithetGrant] = field(default_factory=list)
    #: 结局记录（``{index, ending, event_no}``）
    endings: list[dict[str, Any]] = field(default_factory=list)
    #: 最后一个未达成纪元的代价摘要（给 DM 看）
    last_costs: list[str] = field(default_factory=list)
    #: 达成的结局名（空 = 本局还没收）
    ending: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "length": self.length,
            "settled_epochs": self.settled_epochs,
            "epithets": [e.to_dict() for e in self.epithets],
            "endings": [dict(x) for x in self.endings],
            "last_costs": list(self.last_costs),
            "ending": self.ending,
        }

    @classmethod
    def from_dict(cls, data: Any) -> EpochState:
        d = data if isinstance(data, dict) else {}
        step = epoch_length_of(d)
        return cls(
            length=step,
            settled_epochs=int(d.get("settled_epochs", 0) or 0),
            epithets=[
                e
                for e in (EpithetGrant.from_dict(x) for x in (d.get("epithets") or []))
                if e
            ],
            endings=[dict(x) for x in (d.get("endings") or []) if isinstance(x, dict)],
            last_costs=[str(x) for x in (d.get("last_costs") or [])],
            ending=str(d.get("ending") or ""),
        )

    @property
    def current(self) -> int:
        """当前纪元序号（1 起）。"""
        return self.settled_epochs + 1


def load_epoch(save: Any) -> EpochState:
    """从存档读纪元状态；没有或损坏时返回默认值（不抛错）。"""
    raw = (getattr(save, "progress", None) or {}).get(EPOCH_KEY)
    return EpochState.from_dict(raw if isinstance(raw, dict) else {})


def store_epoch(save: Any, state: EpochState) -> None:
    """把纪元状态写回存档。存档没有 ``progress`` 时静默跳过。"""
    progress = getattr(save, "progress", None)
    if progress is None:
        return
    progress[EPOCH_KEY] = state.to_dict()


def record_epithet(state: EpochState, grant: EpithetGrant) -> EpochState:
    """记一个称号。

    去重按 ``(纪元序号, 称号)`` 而不是只按称号：同一纪元重复结算（重试、
    读档重放）应该幂等，而**不同纪元拿到同一个称号要各记一条**——
    那是这一路的时间线，抹掉就只剩最后一个纪元了。
    """
    state.epithets = [
        e for e in state.epithets if (e.index, e.name) != (grant.index, grant.name)
    ]
    state.epithets.append(grant)
    return state


# --------------------------------------------------------------------------- #
# 文本
# --------------------------------------------------------------------------- #


def epoch_text(
    state: EpochState,
    facts: EpochFacts | None = None,
    *,
    for_player: bool = False,
) -> str:
    """纪元轨的展示文本（DM 侧与玩家侧共用一个函数，靠 ``for_player`` 分流）。

    玩家侧**不给看代价清单**——代价是「世界对你们的回应」，玩家该看到的是
    称号与结局，不是账本。DM 侧才看得到未达成纪元留下了什么。
    """
    lines: list[str] = []
    if state.ending:
        title = ENDINGS.get(state.ending, (state.ending, ""))[0]
        lines.append(f"· 本局结局：{title}")
    if state.epithets:
        lines.append("· 路上留下的称呼：")
        for grant in state.epithets:
            tail = f"（{grant.verdict}）" if grant.verdict else ""
            lines.append(f"  - 第{grant.index}纪元：{grant.name}{tail}")
    if facts is not None and not state.ending:
        lines.append(
            f"· 当前第{facts.index}纪元（第 {facts.first_event}-{facts.last_event} 章）："
            f"目标 {facts.done}/{facts.total}，未结的账 {facts.open_deadlines} 桩"
            + (f"（逾期 {facts.overdue}）" if facts.overdue else "")
        )
    if for_player:
        return "\n".join(lines)
    if state.last_costs:
        lines.append("· 上一个纪元的代价：" + "；".join(state.last_costs[:4]))
    return "\n".join(lines)
