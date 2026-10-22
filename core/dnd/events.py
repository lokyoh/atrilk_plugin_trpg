"""事件池：世界时间、触发式事件、修正表。**纯逻辑层，不调模型、不碰 DM 工具。**

这是「章 = 一个事件」那条设计的数据底座。三件事：

## 1. 世界时间

世界日从 1 起算，显示成「第 N 年 第 M 天」（365 天一年，只用来给玩家一个时间感）。
时间**只从玩家动作里扣**：移动、休息、调查。程序记账，不靠 DM 口头宣布。

## 2. 触发式事件（:class:`EventSlot`）

一个事件有两个时间尺度：

- **存在期**：从 ``since_day`` 起一直挂着，**直到被另一个事件影响**才结束——
  它不是日期区间，所以没有 ``until_day``。「过期」这个概念在这个系统里不存在；
- **过程期**：玩家真去处理它，大概花 ``duration_days`` 天。这是难度与篇幅的刻度。

于是玩家**永远不会「来不及」**，只会「不想去」。

## 3. 修正表（:class:`EventModifier`）

记录「玩家改过什么」。它跟 ``evolution`` 的演化轨分工：

==================  ==================================  ============================
                   演化轨                               修正表
==================  ====================================  ============================
是什么              世界自己走到的**当前状态**            玩家留下的**偏转账本**
形态                一份会被覆盖的状态                    一条条按时间排的记录
谁读                难度、叙述、玩家手册                  **生成下一个事件时**
==================  ====================================  ============================

分开的理由：**状态会被后来的覆盖，账本不会。** 生成器要知道「之前发生过什么」，
只读状态不够——状态只说现在怎样，不说为什么变成这样。

## 4. 节点记账（:func:`resolve_node`）

事件内部的进度记在槽位上（``facts`` / ``fired``），**不记在图里**：图是剧本，
同一份图两个存档该走出不同的路。DM 只需要在局面了结时报一个节点名，
发什么事实、删什么互斥、哪个收束成立，全是程序算的。

## 每跳判定

移动拆成**跳**（同省相邻场景 1 跳，跨省按省邻接 BFS）。**路过的每个场景各判一次**：

===========  ==========  ======
区间          结果        占比
===========  ==========  ======
``< 0.40``   **不刷**     40%
``< 0.85``   **底色**     45%
其余          **高光**     15%
===========  ==========  ======

底色 : 高光 = 3 : 1（已定）。留 40% 不刷是因为**空白本身是节奏**——
每一跳都出事，「有事」就不值钱了。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from .module_graph import (
    Convergence,
    ModuleGraph,
    Node,
    fire_node,
    reached_convergences,
)

log = logging.getLogger(__name__)

__all__ = [
    "DAYS_PER_YEAR",
    "EVENT_FENCE",
    "HOP_SKIRMISH",
    "HOP_VIGNETTE",
    "MAX_OPEN_SLOTS",
    "MODIFIER_KINDS",
    "SLOT_STATES",
    "WEAVE_AFTER_HOPS",
    "EventModifier",
    "EventPool",
    "EventSlot",
    "NodeOutcome",
    "add_modifier",
    "advance_hours",
    "close_event",
    "current_slot",
    "idle_hops",
    "judge_hop",
    "load_pool",
    "new_event_id",
    "note_hop",
    "open_slots",
    "pick_current",
    "recent_modifiers",
    "resolve_node",
    "should_weave",
    "start_event",
    "store_pool",
    "text_to_event",
    "world_day_text",
]

#: 一年多少天。**只用于显示**（「第 2 年 第 30 天」），不参与任何结算。
DAYS_PER_YEAR = 365

#: 一条跳的判定结果
HOP_NONE = "none"
HOP_VIGNETTE = "vignette"
HOP_SKIRMISH = "skirmish"

#: 模型输出里包着事件 JSON 的围栏名（与 ``event-card`` 同一套约定）
EVENT_FENCE = "event-graph"

#: 池里同时「开着」的事件上限。超过就把最旧的 pending 顶掉——
#: 存在期到「被另一个事件影响」为止，新织的一件就是影响它的那件。
#: **只动 pending**：playing 的不能动（玩家正在里面）。
MAX_OPEN_SLOTS = 3

#: 判定区间（累计）：< 0.40 不刷；< 0.85 底色；其余高光。底色 : 高光 = 3 : 1
_HOP_CUT_VIGNETTE = 0.40
_HOP_CUT_SKIRMISH = 0.85

#: 连续多少跳没走向当前事件，就算「没兴趣 / 长时间闲逛」
WEAVE_AFTER_HOPS = 5

#: 事件状态
SLOT_STATES = ("pending", "playing", "closed")

#: 修正表里「改了什么」的类别
MODIFIER_KINDS = (
    "ending",  # 改了某个事件的结局
    "faction",  # 改了势力的位置
    "place",  # 给地点留了痕
    "person",  # 改了某个人的处境
    "event",  # 直接影响了另一个事件
    "skirmish",  # 小事件（高光）的战果，只当背景读
)


# --------------------------------------------------------------------------- #
# 修正表
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EventModifier:
    """一条「玩家改过这件事」的记录。

    它是**账本**不是状态：写下来就不会被后来的覆盖。
    生成下一个事件时要读它，否则「上次你们在这里烧过一层」会被忘掉。
    """

    day: int
    event_id: str
    #: 玩家做了什么（一句话）
    deed: str
    #: 改了什么的类别，见 :data:`MODIFIER_KINDS`
    kind: str = "ending"
    #: 被改的对象（事件 id / 势力名 / 地名 / 人名）
    target: str = ""
    #: 改成什么样（一句话）
    effect: str = ""

    def line(self) -> str:
        tail = f"（{self.effect}）" if self.effect else ""
        where = f" → {self.target}" if self.target else ""
        return f"第 {self.day} 天：{self.deed}{where}{tail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "event_id": self.event_id,
            "deed": self.deed,
            "kind": self.kind,
            "target": self.target,
            "effect": self.effect,
        }

    @classmethod
    def from_dict(cls, data: Any) -> EventModifier:
        d = data if isinstance(data, dict) else {}
        try:
            day = int(d.get("day") or 0)
        except (TypeError, ValueError):
            day = 0
        return cls(
            day=day,
            event_id=str(d.get("event_id") or ""),
            deed=str(d.get("deed") or ""),
            kind=str(d.get("kind") or "ending"),
            target=str(d.get("target") or ""),
            effect=str(d.get("effect") or ""),
        )


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #


@dataclass
class EventSlot:
    """一个触发式事件。

    它包着一张 :class:`~.module_graph.ModuleGraph`——**图是「发生了会怎么走」，
    这里是「什么时候可能发生、结束了世界变成什么样」**。
    """

    id: str
    title: str
    #: 存在期的**起点**：从第几天起挂着
    since_day: int = 1
    #: 过程期：玩家真去处理它，大概花几天（难度与篇幅的刻度，不是存在期）
    duration_days: int = 2
    #: 规模档：light / medium / heavy（决定战斗节点数与 CR 缩放，替代推荐等级）
    scale: str = "medium"
    #: 大概在哪带。**不是硬绑**：写成「白港一带」就够，硬绑就等于又回到「必须先到某地」
    stage: str = ""
    #: 没人管它时，这件事会怎么收场。**可能好可能坏**，不是惩罚
    default_outcome: str = ""
    #: 流言（2–4 条，**只有真与偏**，不要无关的）
    rumors: list[str] = field(default_factory=list)
    #: 事件内部的节点图
    graph: ModuleGraph = field(default_factory=ModuleGraph)
    #: 玩家在这个事件里**已经拿到的事实**（图上的钥匙）。
    #: 存在槽位上而不是图里：图是剧本，同一份图两个存档该走出不同的路。
    facts: list[str] = field(default_factory=list)
    #: 已经触发过的节点 id。记下来是为了「同一局面不重复给东西」。
    fired: list[str] = field(default_factory=list)
    #: pending（挂着）/ playing（玩家在里面）/ closed（已了结）
    state: str = "pending"
    #: 被哪个事件影响而结束（空 = 还挂着）。**存在期没有到期日，只有这个**
    closed_by: str = ""
    #: 结束于第几天
    closed_day: int = 0
    #: 实际结局（玩家打出来的，或 ``default_outcome``）
    outcome: str = ""

    @property
    def open(self) -> bool:
        return self.state in ("pending", "playing")

    def rumor_for(self, day: int) -> str:
        """按「挂了多少天」挑一条流言：挂得越久说得越具体。

        生成时就把这几条写好（从模糊到具体），运行时只是**按时间挑**——
        零模型调用。
        """
        if not self.rumors:
            return ""
        age = max(0, int(day) - int(self.since_day))
        # 0–1 天用最模糊的，之后逐步往后挑；最后一条最具体
        index = min(len(self.rumors) - 1, age // 3)
        return self.rumors[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "since_day": self.since_day,
            "duration_days": self.duration_days,
            "scale": self.scale,
            "stage": self.stage,
            "default_outcome": self.default_outcome,
            "rumors": list(self.rumors),
            "graph": self.graph.to_dict(),
            "facts": list(self.facts),
            "fired": list(self.fired),
            "state": self.state,
            "closed_by": self.closed_by,
            "closed_day": self.closed_day,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: Any) -> EventSlot:
        d = data if isinstance(data, dict) else {}

        def _int(key: str, default: int) -> int:
            try:
                return int(d.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        return cls(
            id=str(d.get("id") or ""),
            title=str(d.get("title") or ""),
            since_day=_int("since_day", 1),
            duration_days=max(1, _int("duration_days", 2)),
            scale=str(d.get("scale") or "medium"),
            stage=str(d.get("stage") or ""),
            default_outcome=str(d.get("default_outcome") or ""),
            rumors=[str(x) for x in (d.get("rumors") or [])],
            graph=ModuleGraph.from_dict(d.get("graph") or {}),
            facts=[str(x) for x in (d.get("facts") or [])],
            fired=[str(x) for x in (d.get("fired") or [])],
            state=str(d.get("state") or "pending"),
            closed_by=str(d.get("closed_by") or ""),
            closed_day=_int("closed_day", 0),
            outcome=str(d.get("outcome") or ""),
        )


# --------------------------------------------------------------------------- #
# 池
# --------------------------------------------------------------------------- #


@dataclass
class EventPool:
    """事件池：整个世界的时间与事件。

    **池里可以挂多个事件，但被引导的只有一个**——玩家的注意力只有一个，
    同时给两条线索等于给零条。
    """

    #: 世界日（从 1 起）
    day: int = 1
    #: 当天的第几小时（0–23）。**同省移动是几小时、小事件是十几分钟**，
    #: 只记天数会把它们全抹平——那「天亮之前赶到」这类判断就没法做了。
    hours: int = 0
    #: 当天的第几分钟（0–59）。内部按分钟累加、展示才折成「14:20」，
    #: 见 :mod:`.clock`。
    minutes: int = 0
    #: 上次长休的绝对分钟（``0`` = 开局以来还没长休过）。
    #: 用来判「24 小时没休息」——5e 的疲惫规则要它，光看当前时间判不出来。
    last_rest: int = 0
    slots: dict[str, EventSlot] = field(default_factory=dict)
    modifiers: list[EventModifier] = field(default_factory=list)
    #: 连续多少跳没走向当前事件（「没兴趣」的可测代理）
    idle_hops: int = 0
    #: 被引导的事件 id（空 = 还没有 / 刚了结）
    current: str = ""

    def slot(self, slot_id: str) -> EventSlot | None:
        return self.slots.get(str(slot_id or ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "hours": self.hours,
            "minutes": self.minutes,
            "last_rest": self.last_rest,
            "slots": {k: v.to_dict() for k, v in self.slots.items()},
            "modifiers": [m.to_dict() for m in self.modifiers],
            "idle_hops": self.idle_hops,
            "current": self.current,
        }

    @classmethod
    def from_dict(cls, data: Any) -> EventPool:
        d = data if isinstance(data, dict) else {}
        try:
            day = int(d.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        try:
            idle = int(d.get("idle_hops") or 0)
        except (TypeError, ValueError):
            idle = 0
        try:
            hours = int(d.get("hours") or 0)
        except (TypeError, ValueError):
            hours = 0
        try:
            minutes = int(d.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        try:
            last_rest = int(d.get("last_rest") or 0)
        except (TypeError, ValueError):
            last_rest = 0
        return cls(
            day=max(1, day),
            hours=max(0, hours) % 24,
            minutes=max(0, minutes) % 60,
            last_rest=max(0, last_rest),
            slots={
                str(k): EventSlot.from_dict({**v, "id": k})
                for k, v in (d.get("slots") or {}).items()
                if isinstance(v, dict)
            },
            modifiers=[
                EventModifier.from_dict(m)
                for m in (d.get("modifiers") or [])
                if isinstance(m, dict)
            ],
            idle_hops=max(0, idle),
            current=str(d.get("current") or ""),
        )

    @property
    def empty(self) -> bool:
        return not self.slots


# --------------------------------------------------------------------------- #
# 世界时间
# --------------------------------------------------------------------------- #


def world_day_text(day: int) -> str:
    """世界日 -> 「第 N 年 第 M 天」。只给玩家一个时间感，不参与结算。"""
    value = max(1, int(day))
    year = (value - 1) // DAYS_PER_YEAR + 1
    return f"第 {year} 年 第 {(value - 1) % DAYS_PER_YEAR + 1} 天"


def advance_hours(pool: EventPool, hours: float) -> int:
    """把世界时间往前推，返回**跨过了几天**（0 表示当天之内）。

    时间**只从玩家的动作里扣**（移动、休息、战斗、一轮对话），不靠 DM 口头宣布——
    否则「挂了多少天」「流言该说得多具体」都无从判定。

    实现在 :mod:`.clock`（那一层只管时间，不认识事件）；这里留一个薄包装，
    因为「推进世界时间」这个动作在事件语境里被调用得最多。
    """
    from .clock import advance_hours as _advance

    return _advance(pool, hours)


# --------------------------------------------------------------------------- #
# 每跳判定
# --------------------------------------------------------------------------- #


def judge_hop(rng: random.Random | None = None) -> str:
    """走一跳之后判一次：``none`` / ``vignette``（底色）/ ``skirmish``（高光）。

    比例已定：不刷 40% / 底色 45% / 高光 15%（底色 : 高光 = 3 : 1）。
    **留 40% 不刷是因为空白本身是节奏**——每一跳都出事，「有事」就不值钱了。
    """
    generator = rng if rng is not None else random
    roll = generator.random()
    if roll < _HOP_CUT_VIGNETTE:
        return HOP_NONE
    if roll < _HOP_CUT_SKIRMISH:
        return HOP_VIGNETTE
    return HOP_SKIRMISH


def note_hop(pool: EventPool, *, entered: bool = False) -> None:
    """记一次移动。

    :param entered: 玩家这一步走进了当前事件的图 → 兴趣确实存在，计数清零。
    """
    if entered:
        pool.idle_hops = 0
    else:
        pool.idle_hops += 1


def idle_hops(pool: EventPool) -> int:
    return int(pool.idle_hops)


def should_weave(pool: EventPool, *, threshold: int = WEAVE_AFTER_HOPS) -> bool:
    """该不该织一个新事件。

    条件：池里没有**待触发**的事件，而且（空闲跳数够了 或 有挂着但没进展的）。

    玩家正在某个事件里时**不织**——推延，让他玩完（中途切断比延期更伤）。
    """
    if any(slot.state == "playing" for slot in pool.slots.values()):
        return False
    pending = [slot for slot in pool.slots.values() if slot.state == "pending"]
    if not pending:
        return True
    return int(pool.idle_hops) >= max(1, int(threshold))


# --------------------------------------------------------------------------- #
# 事件流转
# --------------------------------------------------------------------------- #


def open_slots(pool: EventPool) -> list[EventSlot]:
    """还开着的事件，按「挂得最久」排（``since_day`` 小的先）。"""
    alive = [s for s in pool.slots.values() if s.open]
    alive.sort(key=lambda s: (s.since_day, s.id))
    return alive


def pick_current(pool: EventPool) -> EventSlot | None:
    """挑出**被引导的那一个**：挂得最久、还开着的那个。

    已经在进行中的优先（玩家进去过就别把他引到别处）。
    """
    playing = [s for s in pool.slots.values() if s.state == "playing"]
    if playing:
        playing.sort(key=lambda s: (s.since_day, s.id))
        return playing[0]
    alive = open_slots(pool)
    return alive[0] if alive else None


def current_slot(pool: EventPool) -> EventSlot | None:
    """当前被引导的事件；``current`` 指向的事件已经了结时返回 ``None``。"""
    slot = pool.slot(pool.current)
    return slot if slot is not None and slot.open else None


def start_event(
    pool: EventPool, slot_id: str, *, day: int | None = None
) -> EventSlot | None:
    """玩家走进某个事件：``pending`` -> ``playing``，空闲计数清零。"""
    slot = pool.slot(slot_id)
    if slot is None or slot.state != "pending":
        return None
    slot.state = "playing"
    pool.current = slot.id
    pool.idle_hops = 0
    if day is not None:
        pool.day = max(pool.day, int(day))
    return slot


def close_event(
    pool: EventPool,
    slot_id: str,
    *,
    day: int | None = None,
    by: str = "",
    outcome: str = "",
    deed: str = "",
) -> EventSlot | None:
    """结束一个事件。

    :param by: 被哪个事件影响而结束（``""`` 表示不是被别的事件顶掉的）
    :param outcome: 实际结局；留空就用 ``default_outcome``（放弃 / 被影响时就是这样）
    :param deed: 记进修正表的「玩家做了什么」；留空时按结局自动拼一句

    这一步会**记一条修正表记录**——那是跨事件的记忆，生成下一个事件时要读。
    """
    slot = pool.slot(slot_id)
    if slot is None or not slot.open:
        return None

    finish_day = int(day) if day is not None else pool.day
    slot.state = "closed"
    slot.closed_by = str(by or "")
    slot.closed_day = finish_day
    slot.outcome = str(outcome or slot.default_outcome or "")

    add_modifier(
        pool,
        EventModifier(
            day=finish_day,
            event_id=slot.id,
            deed=str(deed or _auto_deed(slot, by=by)),
            kind="event" if by else "ending",
            target=slot.title or slot.id,
            effect=slot.outcome,
        ),
    )

    if pool.current == slot.id:
        pool.current = ""
        pool.idle_hops = 0
    return slot


def _auto_deed(slot: EventSlot, *, by: str) -> str:
    """没人写 deed 时自动拼一句——比留空好，至少账本读得懂。"""
    if by:
        other = by
        return f"「{slot.title or slot.id}」被「{other}」顶掉，没人管"
    return f"「{slot.title or slot.id}」没人管，就这么过去了"


# --------------------------------------------------------------------------- #
# 节点记账
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class NodeOutcome:
    """结算一个局面的结果（DM 报「这个节点成了 / 崩了」之后程序算出来的东西）。"""

    #: DM 写的原文（没配上节点时原样带回，好让他知道是哪句没对上）
    ref: str = ""
    slot_id: str = ""
    node_id: str = ""
    title: str = ""
    #: 顺利（``True``）还是谈崩（``False``，走 ``on_fail``）
    ok: bool = True
    #: 新拿到的事实（**玩家会知道的说法**，不是系统 id）
    gained: list[str] = field(default_factory=list)
    #: 因互斥**永久失去**的事实
    lost: list[str] = field(default_factory=list)
    #: 因此成立的收束（可能多个；越具体的排前面）
    settled: list[Convergence] = field(default_factory=list)
    #: 这个节点之前已经结算过（不重复发事实）
    already: bool = False
    #: 前置事实不齐时的缺口说明（**这条没有结算**）
    blocked: str = ""
    #: 有 ``ending`` 收束成立 → 事件已随之了结
    closed: bool = False
    #: 配上了多个节点时的候选标题（**不猜**，让 DM 写全名）
    ambiguous: list[str] = field(default_factory=list)


def _match_node(slot: EventSlot, ref: str) -> tuple[Node | None, list[str]]:
    """把 DM 写的话对上图里的节点。

    顺序：id → 全题 → 互相包含。**多个都能配上时不猜**——猜错会白发事实，
    而事实发出去就收不回来（互斥也是拿到那一刻才生效的，没法回滚）。
    """
    graph = slot.graph
    body = str(ref or "").strip()
    if not body:
        return None, []
    exact = graph.node(body)
    if exact is not None:
        return exact, []
    for candidate in graph.nodes.values():
        if candidate.title and candidate.title == body:
            return candidate, []
    partial = [
        n
        for n in graph.nodes.values()
        if n.title and (body in n.title or n.title in body)
    ]
    if len(partial) == 1:
        return partial[0], []
    if len(partial) > 1:
        return None, [n.title or n.id for n in partial]
    return None, []


def resolve_node(
    pool: EventPool,
    ref: str,
    *,
    ok: bool = True,
    day: int | None = None,
) -> NodeOutcome:
    """结算当前事件里的一个局面：发事实、删互斥、查收束。

    只有**被引导的那个事件**可以推进（:func:`current_slot`）——同时让两个事件
    可结算，就会得到一份谁都能改的世界。

    ``ok=False`` 走 ``on_fail``：**失败也前进**。谈崩不是空手而归，
    只是拿到的是另一条路上的东西。

    有 ``ending`` 收束成立时**顺手结掉这个事件**：结局条件都齐了，这个故事
    就已经讲完。不结的话它会一直挂在池里挡住新事件（:func:`should_weave`），
    最后被按默认结局放弃——那等于把玩家打出来的结局换成「没人管」。
    """
    body = str(ref or "").strip()
    slot = current_slot(pool)
    if slot is None or not body:
        return NodeOutcome(ref=body)
    node, ambiguous = _match_node(slot, body)
    if node is None:
        return NodeOutcome(ref=body, slot_id=slot.id, ambiguous=ambiguous)
    if node.id in slot.fired:
        return NodeOutcome(
            ref=body,
            slot_id=slot.id,
            node_id=node.id,
            title=node.title or node.id,
            ok=ok,
            already=True,
        )
    # 前置事实不齐 = 这个局面还没活。**不许结算**：否则 DM 一笔就能
    # 把玩家从没走进过的门后面的东西发出来，图的「网状」就成了装饰。
    missing = [f for f in node.requires if f not in slot.facts]
    if missing:
        return NodeOutcome(
            ref=body,
            slot_id=slot.id,
            node_id=node.id,
            title=node.title or node.id,
            ok=ok,
            blocked="、".join(slot.graph.fact_text(f) or f for f in missing),
        )

    gained, lost = fire_node(slot.graph, node.id, facts=slot.facts, ok=ok)
    slot.fired.append(node.id)
    for fact_id in gained:
        if fact_id not in slot.facts:
            slot.facts.append(fact_id)
    for fact_id in lost:
        if fact_id in slot.facts:
            slot.facts.remove(fact_id)

    settled = reached_convergences(slot.graph, slot.facts)
    endings = [c for c in settled if c.kind == "ending"]
    closed = False
    if endings:
        close_event(
            pool,
            slot.id,
            day=day,
            outcome=endings[0].outcome or endings[0].title,
            deed=f"「{slot.title or slot.id}」了结：{endings[0].title}",
        )
        closed = True
    return NodeOutcome(
        ref=body,
        slot_id=slot.id,
        node_id=node.id,
        title=node.title or node.id,
        ok=ok,
        gained=[slot.graph.fact_text(f) or f for f in gained],
        lost=[slot.graph.fact_text(f) or f for f in lost],
        settled=settled,
        closed=closed,
    )


def new_event_id(pool: EventPool) -> str:
    """给新事件一个不撞车的 id：``ev1`` / ``ev2`` …

    手写样本里 ``ev_pier_ledger`` 这种不带数字的不占号——编号只数纯数字尾缀的，
    所以混用两种命名不会互相挤。
    """
    used = {k for k in pool.slots if k.startswith("ev")}
    index = 1
    while f"ev{index}" in used:
        index += 1
    return f"ev{index}"


def text_to_event(text: str) -> EventSlot | None:
    """从模型输出里解析出一个事件（``event-graph`` 围栏包着的 JSON）。

    围栏里的 ``id`` **会被上层覆盖**（:func:`new_event_id`）——模型不分配 id，
    否则两次生成可能撞号，而撞号会静默覆盖池里已有的事件。
    """
    import json
    import re

    raw = str(text or "")
    if f"```{EVENT_FENCE}" not in raw:
        return None
    match = re.search(rf"```{EVENT_FENCE}\s*({{.*?}})\s*```", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return EventSlot.from_dict(data)


# --------------------------------------------------------------------------- #
# 修正表
# --------------------------------------------------------------------------- #


def add_modifier(pool: EventPool, modifier: EventModifier) -> EventModifier:
    """记一条修正。**不去重**：同一件事做过两次就是两条记录。"""
    if modifier.kind not in MODIFIER_KINDS:
        modifier.kind = "event"
    pool.modifiers.append(modifier)
    return modifier


def recent_modifiers(pool: EventPool, limit: int = 8) -> list[EventModifier]:
    """最近几条修正，新的在前。生成下一个事件时读这个。"""
    count = max(0, int(limit))
    return list(reversed(pool.modifiers[-count:])) if count else []


# --------------------------------------------------------------------------- #
# 存档
# --------------------------------------------------------------------------- #


def load_pool(save: Any) -> EventPool:
    """从存档读出事件池；没有就返回一个空池（``day=1``）。"""
    progress = getattr(save, "progress", None) or {}
    return EventPool.from_dict(progress.get("_events") or {})


def store_pool(save: Any, pool: EventPool) -> None:
    """写回存档。``progress`` 是自由 dict，这一层不碰别的键。"""
    progress = getattr(save, "progress", None)
    if progress is None:
        return
    progress["_events"] = pool.to_dict()
