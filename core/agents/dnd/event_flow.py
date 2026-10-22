"""事件运行时：世界时间、每跳判定、空窗期小事件、事件进出。

## 这一层做什么

纯逻辑在 :mod:`.events`（判定比例、事件流转）与 :mod:`.vignettes`（素材），
这层只负责**把它们接在玩家的动作上**：

- 切场景 = 走若干**跳**，每跳扣时间、判一次刷不刷；
- 玩家走到事件节点所在的场景 = **走进了这个事件**（自动 ``start_event``）；
- 一直在走却没走进 = **闲逛**，累够 ``idle_hops`` 就该织新事件；
- 程序生的东西（时间、小事件、流言）**直接进玩家播报队列**，
  不指望 DM 转述——那正是「玩家侧消息」的硬规矩。

## 为什么「走进事件」用场景判定

玩家手里没有「进入事件」这个按钮（工具清单只剩 7 字符，加不了）。
而事件的节点都写在某个场景上（``Node.at`` 是**场景**名，不是地点名）——
所以「他站在那儿」本身就是「他进去了」。零新工具，而且比任何「确认进入吗」都自然。
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import pairwise

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ...dnd.clock import (
    COMBAT_MINUTES,
    FATIGUE_DC,
    HOURS_PER_LONG_REST,
    HOURS_PER_SHORT_REST,
    HUNGER_HOURS,
    TURN_MINUTES,
    abs_minutes,
    advance,
    fatigue_due,
    period_of,
)
from ...dnd.event_progress import set_current
from ...dnd.events import (
    HOP_SKIRMISH,
    HOP_VIGNETTE,
    MAX_OPEN_SLOTS,
    WEAVE_AFTER_HOPS,
    EventPool,
    EventSlot,
    NodeOutcome,
    advance_hours,
    close_event,
    current_slot,
    judge_hop,
    load_pool,
    new_event_id,
    note_hop,
    open_slots,
    recent_modifiers,
    resolve_node,
    should_weave,
    start_event,
    store_pool,
    text_to_event,
    world_day_text,
)
from ...dnd.module_graph import (
    GROW_KINDS,
    NODE_KINDS,
    Convergence,
    Fact,
    GrowResult,
    ModuleGraph,
    Node,
    abandon_summary,
    abandon_text,
    advice,
    graph_text,
    grow_node,
    open_nodes,
    text_to_node_spec,
    validate,
)
from ...dnd.save_store import get_character, save_save, set_character
from ...dnd.travel import (
    SAME_PROVINCE_HOURS,
    crossing_days,
    crossing_level,
    province_of,
    travel_path,
)
from ...dnd.vignettes import Skirmish, skirmish_material, vignette
from ...dnd.world_store import (
    get_world_map,
    list_events,
    load_doc,
    names_at_level,
    province_crossing,
    province_index,
    scene_site_index,
)

__all__ = [
    "DEFAULT_HOP_HOURS",
    "MoveReport",
    "WeaveEvent",
    "after_move",
    "check_fatigue",
    "combat_time",
    "current_event_no",
    "event_brief",
    "event_scale",
    "event_scene_names",
    "finish_event",
    "grow_graph",
    "mirror_event",
    "party_level",
    "preset_events",
    "push_notice",
    "rest_time",
    "sample_event",
    "settle_node",
    "step_move",
    "sync_world",
    "take_notices",
    "turn_time",
    "weave_event",
]

#: 算不出路的时候按一跳、这么多小时算。
#: **宁可给一个粗糙的数，也不要「这次移动不花时间」**——那会让事件永远挂在那里。
DEFAULT_HOP_HOURS = 4

#: 织事件失败时的重试次数。校验器是「拒绝」而不是「修补」，
#: 所以不给它一次机会的话模型会稳定地写出同一种错；两次足够暴露真问题。
_EVENT_RETRIES = 2

#: 进度回调
ProgressCallback = Callable[[str], "Awaitable[None] | None"]

#: 玩家侧待播报的进度键
_NOTICE_KEY = "_notice"


@dataclass(slots=True)
class MoveReport:
    """一次移动之后发生了什么。"""

    hops: int = 0
    hours: float = 0.0
    day: int = 1
    day_text: str = ""
    period: str = ""
    #: 走进来的事件 id（空 = 只是路过）
    entered: str = ""
    #: 底色小事件（已填好槽位，可直接给玩家看）
    vignettes: list[str] = field(default_factory=list)
    #: 高光小事件的素材（给 DM，不直接给玩家——要不要打是玩家的选择）
    skirmishes: list[Skirmish] = field(default_factory=list)
    #: 顺带听到的流言
    known: list[str] = field(default_factory=list)
    #: 这一趟走完，该织新事件了
    weave_due: bool = False
    #: 被放弃的事件标题（走够阈值还没回来 → 按默认结局收场）
    abandoned: list[str] = field(default_factory=list)
    #: 与 ``abandoned`` 对齐的槽位（收尾要用，见 :func:`after_move`）
    abandoned_slots: list[EventSlot] = field(default_factory=list)
    #: 与 ``abandoned`` 对齐的记账文本（做过什么、还差什么）
    ledgers: list[str] = field(default_factory=list)
    #: 刚走进事件时，那里现在能做的局面（DM 侧）
    opening: str = ""

    def notice(self) -> str:
        """玩家侧播报。**程序算出来的东西不靠 DM 转述**。"""
        lines = [f"🕯 {self.day_text} · {self.period}"]
        days = int(self.hours // 24)
        if days >= 1:
            lines.append(f"（这一趟走了 {days} 天）")
        for text in self.vignettes:
            lines.append("　" + text)
        for rumor in self.known:
            lines.append(f"　「{rumor}」")
        return "\n".join(lines)

    def dm_hint(self) -> str:
        """DM 侧提示：这一趟实际过去了多久、路上出了什么、下一步该注意什么。"""
        lines = [f"【这一趟】{self.hops} 跳，现在 {self.day_text} · {self.period}。"]
        if self.vignettes:
            lines.append(
                f"【路上】{len(self.vignettes)} 段小插曲已直接播给玩家，你不用复述，可接着它往下写。"
            )
        if self.skirmishes:
            parts = []
            for item in self.skirmishes:
                parts.append(
                    f"{item.hook}（{item.count} 只{item.name}，CR {item.cr:g}）"
                )
            lines.append("【路上的机会】" + "；".join(parts))
            lines.append(
                "　它只是**放在那里**：玩家介入就 start_combat，绕开也不用阻拦；"
                "打完照常 settle_combat 结算。别把它写成「主线」——空窗期的机会不是任务。"
            )
        if self.known:
            lines.append("【顺带听到】" + "；".join(self.known))
        if self.entered:
            lines.append(f"【进入事件】{self.entered}——他们已经站在这件事里了。")
            if self.opening:
                lines.append(self.opening)
        if self.weave_due:
            lines.append(
                "【该有新的了】这一带闲着有一阵了。新事件由系统另织"
                "（织没织成会另说）——**不要自己编一件大事顶上**，照常过日常。"
            )
        if self.abandoned:
            lines.append(
                "【放下了】"
                + "；".join(
                    f"「{title}」再没人管，按默认结局收场" for title in self.abandoned
                )
                + "。世界只是继续往前走——不惩罚，也不等他们。"
            )
            for ledger in self.ledgers:
                lines.append(ledger)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 时间与跳数
# --------------------------------------------------------------------------- #


def party_level(save) -> int:
    """队伍平均等级（取整，至少 1）。读不到就当 1 级。"""
    try:
        from ...dnd.combat import party_level as _level

        return max(1, int(_level(save)))
    except Exception as e:  # 算不出等级不该拦住一次移动
        log.debug(f"读队伍等级失败：{e!r}")
        return 1


def _same_site(world_key: str, origin: str, destination: str) -> bool:
    """这一走是不是**同一个场所里挪个位置**（场景组内）。

    判定看场景的 ``meta.site``：同一个 ``place`` 底下的几个场景是同一处地方的
    房间（官方模组里一艘船有主甲板、船长室、储藏室）。组内移动：

    - **不扣世界时间**——官方是几分钟的事，算成三小时就离谱了；
    - **不判一次「路上有没有事」**——在自家船舱里走不该撞上伏击；
    - **不记一次「闲逛」**——在同一处地方里挪位置，不构成「对某件事失去兴趣」。

    认不出组（场景没写 ``meta.site``）时返回 ``False``：没有组就没有豁免，
    移动按普通场景处理。
    """
    if not origin or not destination or origin == destination:
        return False
    try:
        sites = scene_site_index(world_key)
    except Exception as e:  # 世界库读不出来就不豁免
        log.debug(f"读场景组失败：{e!r}")
        return False
    start = str(sites.get(str(origin).strip()) or "")
    goal = str(sites.get(str(destination).strip()) or "")
    return bool(start) and start == goal


def _legs(world_key: str, origin: str, destination: str) -> list[float]:
    """每一跳耗时（小时）。空列表 = 算不出路。

    跨省按省邻接求最短路，**逐跳取两侧难度的较大者**再折成小时——
    这样「翻一座险岭再走一段通衢」不会被平均成「两段好走」。
    """
    if not origin or not destination or origin == destination:
        return []
    try:
        places = province_index(world_key)
        _regions, adjacency = get_world_map(world_key)
        crossing = province_crossing(world_key)
    except Exception as e:  # 世界库读不出来，退回缺省
        log.debug(f"读世界地图失败：{e!r}")
        return []

    start = province_of(origin, places)
    goal = province_of(destination, places)
    if not start or not goal:
        return []
    if start == goal:
        return [float(SAME_PROVINCE_HOURS)]

    path = travel_path(start, goal, adjacency)
    if len(path) < 2:
        return []
    return [
        max(
            crossing_days(crossing_level(a, crossing)),
            crossing_days(crossing_level(b, crossing)),
        )
        * 24
        for a, b in pairwise(path)
    ]


def _stamp(pool) -> str:
    """「第 1 年 第 3 天 14:20」——年份由事件层给，钟点由 :mod:`.clock` 给。"""
    return f"{world_day_text(pool.day)} {pool.hours:02d}:{pool.minutes:02d}"


def _hours_text(hours: float) -> str:
    value = max(0.0, float(hours))
    if value < 1:
        return f"{round(value * 60)} 分钟"
    if value < 24:
        return f"{value:.1f} 小时".replace(".0 ", " ")
    return f"{value / 24:.1f} 天".replace(".0 ", " ")


# --------------------------------------------------------------------------- #
# 场景 / 势力
# --------------------------------------------------------------------------- #


def _event_scenes(slot: EventSlot | None) -> set[str]:
    """这个事件涉及哪些场景。"""
    if slot is None:
        return set()
    return {str(n.at).strip() for n in slot.graph.nodes.values() if str(n.at).strip()}


def _local_faction(world_key: str) -> str:
    """当地势力名，用来填小事件的槽位。读不到就空（模板会退回「他们」）。"""
    try:
        from ...dnd.world_bible import load_bible

        bible = load_bible(world_key)
    except Exception as e:
        log.debug(f"读世界圣经失败：{e!r}")
        return ""
    for entity in getattr(bible, "entities", []) or []:
        if entity.kind == "faction":
            return entity.name
    return ""


# --------------------------------------------------------------------------- #
# 存档镜像（给玩家手册）
# --------------------------------------------------------------------------- #


def mirror_event(save, pool: EventPool) -> None:
    """把「眼下这件事与它的局面」写进 :data:`~...dnd.event_progress` 的镜像。

    玩家手册由 :mod:`...dnd.world_brief` 生成，那一层只准读存档，
    不该反过来摸事件池——所以这里主动把摘要写进 ``progress["_event"]``。

    **事件了结时传空**：镜像清空，手册那一节整块消失。玩家该看到的是
    「这一带眼下没有追着不放的事」，而不是一个上一件事的残留标题。
    """
    slot = current_slot(pool)
    if slot is None:
        set_current(save, "")
        return
    node_of = getattr(slot.graph, "nodes", None) or {}
    fired = {str(x).strip() for x in (slot.fired or []) if str(x).strip()}
    open_names = [
        n.title
        for n in open_nodes(slot.graph, facts=slot.facts, visited=slot.fired)
        if str(n.title or "").strip()
    ]
    done_names = [
        node_of[nid].title
        for nid in sorted(fired)
        if nid in node_of and str(node_of[nid].title or "").strip()
    ]
    set_current(
        save, slot.title or slot.id, open_nodes=open_names, done_nodes=done_names
    )


# --------------------------------------------------------------------------- #
# 玩家侧播报队列
# --------------------------------------------------------------------------- #


def push_notice(save, text: str) -> None:
    """把一段**程序生成、玩家必须看到**的文字排进队列。

    下一轮 ``run_dm_round`` 会原样拼进玩家消息——不指望 DM 转述，
    因为「DM 的功能调用结果只有 DM 看得到」。
    """
    body = str(text or "").strip()
    if not body:
        return
    progress = getattr(save, "progress", None)
    if progress is None:
        return
    queue = progress.get(_NOTICE_KEY)
    if not isinstance(queue, list):
        queue = []
    queue.append(body)
    # 只留最近几条：积压太多说明好几轮没跑，旧的一次性播出来没有意义
    progress[_NOTICE_KEY] = queue[-4:]


def take_notices(save) -> list[str]:
    """取走并清空播报队列。"""
    progress = getattr(save, "progress", None)
    if not isinstance(progress, dict):
        return []
    queue = progress.pop(_NOTICE_KEY, None)
    if not isinstance(queue, list):
        return []
    return [str(x) for x in queue if str(x).strip()]


# --------------------------------------------------------------------------- #
# 一次移动
# --------------------------------------------------------------------------- #


def _abandon_if_gone(
    pool: EventPool, *, day: int | None = None
) -> list[tuple[EventSlot, str]]:
    """玩着玩人走了，且走够阈值没回来 → 按默认结局放弃。

    **不猜心理，只看行为**：兴趣 = 你朝它走（走进就清零，见 :func:`note_hop`）；
    反过来，连续 ``WEAVE_AFTER_HOPS`` 跳都没朝它走，就是「不想做了」。

    放弃不是失败，是这段故事到此为止：走 ``default_outcome``（可能好可能坏），
    记一条修正，世界继续往前走。不结掉的话这个事件会永远挂在 ``playing``
    状态里挡住新事件（:func:`should_weave` 见 ``playing`` 一律不织）。
    """
    if int(pool.idle_hops) < WEAVE_AFTER_HOPS:
        return []
    out: list[tuple[EventSlot, str]] = []
    for slot in [s for s in pool.slots.values() if s.state == "playing"]:
        # 记账要在 close 之前算：它只看图与手里的线索，与状态无关，
        # 但先算出来省得 close 之后再摸一遍槽位。
        ledger = abandon_text(
            slot.graph,
            abandon_summary(slot.graph, facts=slot.facts, visited=slot.fired),
        )
        close_event(pool, slot.id, day=day)
        out.append((slot, ledger))
    return out


def step_move(
    save,
    destination: str,
    *,
    origin: str = "",
    rng: random.Random | None = None,
) -> MoveReport:
    """把「从 origin 走到 destination」这件事算完：时间、路上的事、事件的进出。

    **每跳判一次**，不是每次移动判一次——一次跨省要走三天，
    按一次移动判等于说「只有目的地才有事」，路上的世界就是空的。
    """
    pool: EventPool = load_pool(save)
    start = str(origin or getattr(save, "current_scene", "") or "")
    world_key = str(getattr(save, "world_key", "") or "")
    # 同一个场所里挪位置（主甲板 → 船长室）：不扣时间、不判路上有没有事、
    # 不记闲逛。理由见 :func:`_same_site`。
    in_site = _same_site(world_key, start, destination)
    if in_site:
        legs: list[float] = []
    else:
        legs = _legs(world_key, start, destination)
        if not legs:
            legs = [float(DEFAULT_HOP_HOURS)]

    report = MoveReport(hops=len(legs), hours=float(sum(legs)))
    slot = current_slot(pool)
    scenes = _event_scenes(slot)
    faction = _local_faction(str(getattr(save, "world_key", "") or ""))
    level = party_level(save)

    for _leg in legs:
        advance_hours(pool, _leg)
        kind = judge_hop(rng)
        if kind == HOP_VIGNETTE:
            item = vignette(destination, place=destination, faction=faction, rng=rng)
            advance_hours(pool, item.minutes / 60)
            report.hours += item.minutes / 60
            report.vignettes.append(item.text)
            if item.carries and slot is not None:
                rumor = slot.rumor_for(pool.day)
                if rumor and rumor not in report.known:
                    report.known.append(rumor)
        elif kind == HOP_SKIRMISH:
            report.skirmishes.append(skirmish_material(level, destination, rng=rng))

    # 走进事件：他站在这件事的某个场景上，本身就是进去了。
    # **组内移动也算**——玩家在船上从主甲板挪到船长室，而船长室正是这件事的
    # 节点场景，那他就是进去了。这一条是「零新工具」的关键：没有「进入事件」
    # 按钮，站在那儿即是。
    inside = bool(slot is not None and destination in scenes)
    if inside and slot is not None:
        if slot.state == "pending":
            start_event(pool, slot.id, day=pool.day)
        report.entered = slot.id
        # 进门就把「这里现在能做什么」给 DM：节点活性全看玩家手里的线索，
        # 而线索是这一趟之前攒下来的——不主动列出来，DM 只能凭印象编。
        report.opening = graph_text(
            slot.graph, facts=slot.facts, visited=slot.fired, max_open=4
        )
    # 组内移动不记「闲逛」：在同一处地方里挪位置，不构成「离某件事越来越远」
    if not in_site:
        note_hop(pool, entered=inside)

    # 放弃：走够阈值还没回来 → 按默认结局收场（世界只是继续往前走）
    dropped = _abandon_if_gone(pool, day=pool.day)
    for gone, ledger in dropped:
        report.abandoned.append(gone.title or gone.id)
        report.abandoned_slots.append(gone)
        report.ledgers.append(ledger)

    report.weave_due = should_weave(pool)
    report.day = pool.day
    report.day_text = _stamp(pool)
    report.period = period_of(pool.hours)

    store_pool(save, pool)
    if report.vignettes or report.known or report.hours > 0:
        push_notice(save, report.notice())
    for gone, _ledger in dropped:
        tail = f"：{gone.outcome}" if gone.outcome else ""
        push_notice(
            save, f"　（「{gone.title or gone.id}」再没人管，就这样过去了{tail}）"
        )
    try:
        save_save(save)
    except Exception as e:  # 落盘失败不该毁掉这一轮
        log.warning(f"保存移动后的世界时间失败：{e!r}")
    return report


# --------------------------------------------------------------------------- #
# 其他动作的时间
# --------------------------------------------------------------------------- #


def _flush(save, pool: EventPool, notice: str = "") -> None:
    """写回事件池、排播报、落盘。几个时间入口共用。"""
    store_pool(save, pool)
    if notice:
        push_notice(save, notice)
    try:
        save_save(save)
    except Exception as e:  # 落盘失败不该毁掉这一轮
        log.warning(f"保存世界时间失败：{e!r}")


def rest_time(save, *, long: bool = True) -> list[str]:
    """结算休息占掉的时间：**长休 8 小时、短休 1 小时**。

    长休会把「上次长休」推到**结束时刻**——疲惫是按「距上次长休多久」算的，
    所以这一步必须和 :func:`check_fatigue` 用同一个基准。
    """
    pool = load_pool(save)
    hours = HOURS_PER_LONG_REST if long else HOURS_PER_SHORT_REST
    advance_hours(pool, hours)
    if long:
        pool.last_rest = abs_minutes(pool)
    word = "长休" if long else "短休"
    _flush(
        save,
        pool,
        f"🕯 {_stamp(pool)} · {period_of(pool.hours)}（{word} {hours} 小时过去了）",
    )
    return [f"{word}用掉 {hours} 小时，现在是 {_stamp(pool)}。"]


def turn_time(save, *, minutes: int = TURN_MINUTES) -> None:
    """一轮对话 / 调查 / 磨嘴皮占掉的时间。

    **不播报**：每一轮都刷一句「时间过去了 10 分钟」会烦。
    它只在世界日变化时由 :func:`check_fatigue` 之类的检查体现出来。
    """
    pool = load_pool(save)
    advance(pool, minutes)
    _flush(save, pool)


def combat_time(save, *, minutes: int = COMBAT_MINUTES) -> str:
    """一场战斗占掉的时间。返回一句话，由调用方决定塞进哪里。

    **不按轮数 × 6 秒**：十轮才一分钟，记了等于没记。
    这里给的是「这一场过去了多久」，含收尸、包扎、喘气。
    """
    pool = load_pool(save)
    advance(pool, minutes)
    _flush(save, pool)
    return f"（这一场过去大约 {minutes} 分钟，现在 {_stamp(pool)}）"


def check_fatigue(save, *, rng=None, hours: float = HUNGER_HOURS) -> list[str]:
    """太久没休息 → 每个活着的角色 DC 10 体质豁免，失败 +1 级力竭。

    **结算完就把 ``last_rest`` 推到当下**：不然每一轮都会重掷一次，
    那叫「掷到失败为止」，不叫规则。

    长休会自己重置这个基准，所以正常节奏下不会频繁触发；
    触发说明玩家真的连着赶了好几天路。
    """
    pool = load_pool(save)
    if not fatigue_due(pool, pool.last_rest, hours=int(hours)):
        return []

    lines: list[str] = []
    for user_id in list(getattr(save, "player_ids", None) or []):
        card = get_character(save, user_id)
        if card is None or card.is_dead:
            continue
        name = card.name or user_id
        result = card.check_save("con", FATIGUE_DC, rng=rng)
        if result.success:
            lines.append(f"{name}：体质豁免 {result.total} ≥ {FATIGUE_DC}，撑住了")
            continue
        card.exhaustion = min(6, int(card.exhaustion or 0) + 1)
        set_character(save, card)
        lines.append(
            f"{name}：体质豁免 {result.total} < {FATIGUE_DC}，力竭升到 {card.exhaustion} 级"
        )

    # 无论成败都推进基准：这一轮已经算过一次
    pool.last_rest = abs_minutes(pool)
    _flush(save, pool, ("🌙 " + "；".join(lines)) if lines else "")
    return lines


# --------------------------------------------------------------------------- #
# 节点结算（DM 报一个局面名，程序发事实）
# --------------------------------------------------------------------------- #


def settle_node(save, ref: str, *, ok: bool = True) -> str | None:
    """DM 报「这个局面成了 / 崩了」→ 程序授事实、删互斥、查收束。

    返回给 DM 的说明；``None`` 表示这句话**不是节点引用**，
    调用方按普通线索处理（事件线索照旧记，行为不变）。
    """
    pool = load_pool(save)
    outcome = resolve_node(pool, ref, ok=ok)
    if not (outcome.node_id or outcome.ambiguous):
        return None
    mirror_event(save, pool)
    _flush(save, pool)
    return _node_text(outcome)


async def finish_event(
    save,
    slot: EventSlot,
    *,
    note: str = "",
    difficulty: str = "medium",
    agent: object | None = None,
) -> list[str]:
    """一件事了结之后的统一收尾：**推刻度 → 推进化轨 → 判纪元**。

    走回收束与「走够阈值没人管」两条路都走这里。分开各写一套的话，
    「已了结几件事」这个数就会两条路各算一份，纪元与难度轨随即对不上。

    :param agent: 给纪元称号命名的 agent；``None`` 表示这次不调模型，
        称号走程序兜底（自检 / 单测 / 预览用）。
    :return: 要播报给玩家的几行（纪元结算摘要之类）；没结算就是空表。
    """
    from ...dnd.event_progress import set_current
    from .epoch_flow import advance_event, settle_epoch

    fired = {str(x).strip() for x in (slot.fired or []) if str(x).strip()}
    advance_event(
        save,
        difficulty=difficulty,
        note=note or slot.title or slot.id,
        done=len(fired),
        total=len(getattr(slot.graph, "nodes", None) or {}),
    )
    # 镜像清掉：这件事已经过去了，手册不该继续挂着它的标题
    set_current(save, "")
    sync_world(save, current_event_no(save))

    epoch = await settle_epoch(save, difficulty=difficulty, agent=agent)
    return [epoch.summary()] if epoch.settled else []


async def after_move(
    save,
    report: MoveReport,
    *,
    difficulty: str = "medium",
    agent: object | None = None,
) -> None:
    """移动之后需要 await 的那一段：放弃掉的事件走 :func:`finish_event`。

    ``step_move`` 本身是同步的（它只算时间与判定），而收尾要发纪元称号，
    所以拆成这一步，由移动工具在 async 上下文里接着跑。
    """
    for slot in report.abandoned_slots:
        for line in await finish_event(save, slot, difficulty=difficulty, agent=agent):
            push_notice(save, line)


def _node_text(outcome: NodeOutcome) -> str:
    """把结算结果说给 DM 听。事实的文本**就是玩家会知道的说法**，照抄进叙述即可。"""
    if outcome.ambiguous:
        return (
            f"「{outcome.ref}」能指这些局面："
            + "、".join(outcome.ambiguous)
            + "。请写全名——这条暂不结算，没有发任何事实。"
        )
    if outcome.already:
        return f"「{outcome.title}」之前已经结算过了，没有新的事实。"
    if outcome.blocked:
        return (
            f"「{outcome.title}」还开不了：要先有 {outcome.blocked}。"
            "这条没有结算，也没有发任何事实。"
        )
    how = "谈崩" if not outcome.ok else "成立"
    lines = [f"已结算局面「{outcome.title}」（{how}）"]
    if outcome.gained:
        lines.append("新的事实：" + "；".join(outcome.gained))
    else:
        lines.append("没有新的事实。")
    if outcome.lost:
        lines.append("因互斥失去：" + "；".join(outcome.lost))
    for conv in outcome.settled[:2]:
        lines.append(f"收束已成立：「{conv.title or conv.id}」")
    if outcome.closed:
        lines.append(
            "**本事件已了结**：结局条件都齐了，请在本轮叙述里照它收尾——"
            "别把没发生的节点说成发生过。"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 图可长（DM 现场造节点：允许生长，不允许长坏）
# --------------------------------------------------------------------------- #

#: 被拒时回给 DM 的写法教学。**放这里而不是工具描述里**——工具清单有 4300
#: 字符的硬上限，而这段恰好是「写错了才会看到」的东西，不必每轮都占额度。
_GROW_HOWTO = (
    "写法：新局面 "
    '{"title":"局面的名字","kind":"battle","at":"场景名",'
    '"grants":["玩家会知道的说法"],"requires":["已有事实的原文"],'
    '"cost":"代价","hint":"给自己的提示"}\n'
    "　title、grants 必填；kind 可用 "
    + "、".join(GROW_KINDS)
    + "；requires 只能写**已经存在**的事实（原文或 id 都认，"
    "要新钥匙让这个节点自己 grants）；at 不写就按玩家眼下所在场景。"
)


def grow_graph(save, text: str) -> str | None:
    """DM 写「新局面 {json}」→ 现场造一个节点接进**当前被引导事件**的图。

    返回给 DM 的说明；``None`` 表示这句话不是在造节点，调用方按老路走
    （事件线索照旧记，行为不变）。

    **允许生长，不允许长坏**：接之前先跑局部检查，接上之后整张图再过一遍
    :func:`~...dnd.module_graph.validate`——不过审原样退回，图一个字节不改
    （详见 :func:`~...dnd.module_graph.grow_node`）。只对**被引导的事件**生效，
    与 :func:`settle_node` 同一条规矩：玩家没在玩的事，DM 不该替它做主。
    """
    spec = text_to_node_spec(text)
    if spec is None:
        return None

    pool = load_pool(save)
    slot = current_slot(pool)
    graph = getattr(slot, "graph", None) if slot is not None else None
    if slot is None or graph is None:
        return (
            "眼下没有被引导的事件：没有图可长。\n"
            "　先等一件（玩家走向某件事，或这一带闲够了会另织一件），再现场造节点。"
        )

    scenes = event_scene_names(str(getattr(save, "world_key", "") or ""))
    here = str(getattr(save, "current_scene", "") or "").strip()
    # 玩家站着的地方天然是个合法落点：候选来自世界第 5 层场景名，
    # 而「他现在在哪」本身就是答案，不该因为对不上表就拒绝。
    if here and here not in scenes:
        scenes = [*scenes, here]

    result = grow_node(graph, spec, scenes=scenes, at_default=here)
    if not result.ok:
        return (
            "没能接进图（图没有改）：\n- "
            + "\n- ".join(result.problems)
            + "\n"
            + _GROW_HOWTO
        )
    mirror_event(save, pool)
    _flush(save, pool)
    return _grow_text(slot, result)


def _grow_text(slot: EventSlot, result: GrowResult) -> str:
    """把一次成功的生长说给 DM 听。事实的文本**就是玩家会知道的说法**。"""
    node = result.node
    kind = NODE_KINDS.get(node.kind, node.kind) if node else ""
    lines = [
        f"已接进事件「{slot.title}」的图：新局面「{node.title}」"
        f"（{kind}，在 {node.at}，第 {node.depth} 层）"
    ]
    if result.new_facts:
        lines.append(
            "新的事实（照抄进叙述即可）：" + "；".join(f.text for f in result.new_facts)
        )
    if result.reused:
        lines.append(
            "复用已有的钥匙（同一把，能喂给现有收束）：" + "；".join(result.reused)
        )
    alive = open_nodes(slot.graph, facts=slot.facts, visited=slot.fired)
    fresh = [n for n in alive if n.id == node.id]
    if fresh:
        lines.append(
            f"它**现在就能做**——玩家解决了它，就用 note_event 写「{node.title}」"
            "这个名字结算（前缀「失败：」表示谈崩），事实由程序发。"
        )
    else:
        lines.append(
            "它还没活：玩家得先拿到它要的事实。前置齐了之后，"
            "同样用 note_event 写它的名字结算。"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 织新事件（走够了还没走进当前事件 → 该出点事了）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class WeaveEvent:
    """一次织事件的结果。"""

    slot: EventSlot | None = None
    #: 最后一轮校验的问题（空 = 通过）
    problems: list[str] = field(default_factory=list)
    #: 最后一轮的**软建议**（不拒绝，只回给模型让它自己改好）
    tips: list[str] = field(default_factory=list)
    #: 重试次数（0 = 一次就过）
    attempts: int = 0
    #: 玩家侧的一句话（新事件挂上了、眼下传的话）
    notice: str = ""
    usages: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.slot is not None


def event_scene_names(world_key: str) -> list[str]:
    """事件节点可以落的场景名。

    节点写在**场景**上（玩家在场景之间移动），所以候选取第 5 层。
    世界里还没有场景时退到第 4 层（据点 / 野外地点 / 地下城）——
    那至少保证新世界也织得出事件，代价是图的地理粒度粗一格。

    **场景可以是区域，也可以是房间**：官方模组（风骸岛之龙）一间舱房一份
    文档，那份文档就是场景。两个场景同属一个 ``place`` 时是**同一处地方的
    房间**，它们之间移动不扣时间（场所组）。
    """
    try:
        scenes = names_at_level(world_key, 5)
        if scenes:
            return scenes
        return names_at_level(world_key, 4)
    except Exception as e:  # 世界库读不出来时返回空，由调用方决定怎么办
        log.debug(f"读场景候选失败：{e!r}")
        return []


def event_scale(save) -> str:
    """这件事多大：light / medium / heavy。

    **它只决定战斗场数下限**（难度另有三轨按队伍水平算）。这里拿队伍等级当
    代理——没有比它更可靠的信号，而「这件事本身多大」在织出来之前本来就不存在。
    """
    level = party_level(save)
    if level <= 2:
        return "light"
    if level <= 9:
        return "medium"
    return "heavy"


def _event_inputs(
    save, pool: EventPool, scale: str, scenes: list[str]
) -> dict[str, object]:
    """拼给 :class:`~.gen_event.EventAgent` 的输入。

    修正表**必须**进上下文：生成器要知道「之前发生过什么」，
    只读当前状态不够——状态只说现在怎样，不说为什么变成这样。
    """
    from ...dnd.world_store import MAIN_DOC_KEY, load_doc

    world_text = (
        load_doc(str(getattr(save, "world_key", "") or ""), "world", MAIN_DOC_KEY) or ""
    )
    context: list[str] = [f"世界时间：{_stamp(pool)}"]
    scene = str(getattr(save, "current_scene", "") or "").strip()
    if scene:
        context.append(f"玩家眼下在：{scene}")
    context.append(f"队伍 {party_level(save)} 级左右")

    mods = recent_modifiers(pool, limit=6)
    if mods:
        context.append("玩家改过什么：" + "；".join(m.line() for m in mods))
    hanging = open_slots(pool)
    if hanging:
        context.append(
            "还挂着、没人管的事："
            + "、".join(s.title for s in hanging[:4])
            + "（别跟这些重复）"
        )
    return {
        "world": world_text,
        "scenes": "、".join(scenes),
        "scale": scale,
        "context": "\n".join(context),
    }


async def _notify(on_progress: ProgressCallback | None, message: str) -> None:
    """调用进度回调；回调出错只记日志，不影响织事件。"""
    if on_progress is None:
        return
    try:
        result = on_progress(message)
        if hasattr(result, "__await__"):
            await result
    except Exception as e:
        log.warning(f"事件进度回调失败：{e!r}")


# --------------------------------------------------------------------------- #
# 预写事件（世界自带的第一章们）
# --------------------------------------------------------------------------- #


def _event_notice(slot: EventSlot, day: int) -> str:
    """新事件挂上了时给玩家的一句话。流言按天挑，零模型调用。"""
    rumor = slot.rumor_for(day)
    tail = f"——{rumor}" if rumor else ""
    return f"（这一带有了新的事：「{slot.title}」{tail}）"


def _at_opening(pool: EventPool) -> bool:
    """这一局还在不在**开局**：一件都没了结，也没有正在玩的。

    规则 12（入口必须是硬钩子）只在这个时候查——等玩家有了历史，
    「有人请你去看看」就是正当入口，别人会来找一个有名声的人。
    """
    return not any(s.state in ("playing", "closed") for s in pool.slots.values())


def preset_events(save) -> list[EventSlot]:
    """把世界里的**预写**事件装进事件池（预设的六份开局事件就这么进来的）。

    读世界 ``event`` 文档里的 ``event-graph`` 围栏。三条规矩：

    1. **只装眼前的**：事件有节点落在玩家当前省才装——沙盒里玩家只听说
       身边的事，走过去了才听说下一处（装一堆远处的等于一堆噪音）；
    2. **幂等**：池里已有同 id 的跳过（文档给的 ``ev_*`` id 就是身份，
       不重新编号、不重复挂，了结过的也不再回来）；
    3. **不超载**：开着的事件到 :data:`~...dnd.events.MAX_OPEN_SLOTS` 就停装——
       同时挂太多件事等于一件都没挂。

    装上且没有事件正在进行时，顺手把**当前被引导的**指向新装的第一件，
    并照惯例给玩家播报一条。返回本次装上的那些；空表 = 没有现成的可装，
    调用方该走模型那条路。

    **与 :func:`weave_event` 的差别只有一处**：那里的 ``scale`` 由
    :func:`event_scale` 按队伍等级定并覆盖模型输出，这里**用文档写死的档位**。
    这不是遗漏——六份预写跨 light/medium/heavy，图的就是「开局在哪一省，
    就遇到哪一档强度」，而难度本身仍由
    :func:`~...dnd.encounter_budget.event_cr` 按队伍等级算。档位在这里只决定
    战斗场数下限（:data:`~...dnd.module_graph.BATTLE_MIN`）。
    """
    world_key = str(getattr(save, "world_key", "") or "").strip()
    if not world_key:
        return []
    pool = load_pool(save)
    scenes = event_scene_names(world_key)
    if not scenes:
        return []
    try:
        refs = list_events(world_key)
        places = province_index(world_key)
    except Exception as e:  # 世界库读不出来就当没有预写事件
        log.warning(f"读世界事件失败（{world_key}）：{e!r}")
        return []

    here = province_of(str(getattr(save, "current_scene", "") or ""), places)
    if not here:
        return []  # 说不出玩家在哪，就说不清什么算「眼前」

    # **按序位装**：``event_no`` 小的是这一省里更靠前的那件事（1 = 开局那件）。
    # 池子同时只挂 :data:`~...dnd.events.MAX_OPEN_SLOTS` 件，装不下时
    # 留谁不留谁不能看运气——``list_events`` 按更新时间倒序，序位小的
    # 反而可能排在后面，于是**开局那件被后三件挤掉**（风骸岛就发生过）。
    # 序位是作者定的意图，更该由它决定先装谁。
    refs.sort(key=lambda r: r.event_no)

    found: list[EventSlot] = []
    for ref in refs:
        text = load_doc(world_key, "event", ref.key)
        if not text:
            continue
        slot = text_to_event(text)
        if slot is None or slot.graph.empty:
            continue  # 这份事件不是事件图（旧的事件卡之类）
        if slot.id and slot.id in pool.slots:
            continue  # 装过了（包括已经了结的）
        ats = {n.at for n in slot.graph.nodes.values() if n.at}
        if here not in {province_of(at, places) for at in ats}:
            continue  # 不在这附近
        if ats - set(scenes):
            # 节点写在不是场景的地方：玩家永远走不到它，这图是坏的
            log.warning(f"预写事件「{slot.title}」的所在地不在场景候选里，跳过")
            continue
        if validate(slot.graph):
            # 和 DM 现场造的节点同一条纪律：不过校验不进池。
            # 入口硬钩子（规则 12）**不在这里查**——它是软提示，
            # 由 tests/test_dnd_preset_seed.py 那条「预写事件不许有开局提示」钉住。
            log.warning(f"预写事件「{slot.title}」没过校验，跳过")
            continue
        found.append(slot)

    if not found:
        return []

    open_now = sum(1 for s in pool.slots.values() if s.state == "pending")
    installed: list[EventSlot] = []
    for slot in found:
        if open_now >= MAX_OPEN_SLOTS:
            break
        slot.since_day = pool.day
        slot.state = "pending"
        pool.slots[slot.id] = slot
        installed.append(slot)
        open_now += 1

    if installed:
        # 有事件正在玩时不改引导目标（中途换引比延期更伤），但照旧播报
        if not any(s.state == "playing" for s in pool.slots.values()):
            pool.current = installed[0].id
            pool.idle_hops = 0
        mirror_event(save, pool)
        _flush(save, pool, _event_notice(installed[0], pool.day))
    return installed


async def weave_event(
    save,
    *,
    scale: str = "",
    agent=None,
    on_progress: ProgressCallback | None = None,
) -> WeaveEvent:
    """织一件新事件，落进事件池并设为当前被引导的那件。

    入口条件是 :func:`~...dnd.events.should_weave`（没有待触发事件，或连续
    ``WEAVE_AFTER_HOPS`` 跳没走向当前事件）。**正在玩的事件不织**——
    中途切断比延期更伤。

    与 :func:`~.gen_event.weave_event` 同一条纪律：模型只写内容，
    场景候选、规模档、id 由程序给定；:func:`~...dnd.module_graph.validate`
    不过就**带着问题重织**（最多 :data:`_EVENT_RETRIES` 次），不做静默修补。

    但**先试预写的**（:func:`preset_events`）：世界自带的第一章们装得上，
    就一个模型调用都不花。
    """
    outcome = WeaveEvent()
    pool = load_pool(save)
    if any(slot.state == "playing" for slot in pool.slots.values()):
        outcome.problems = ["还有事件正在进行：先让它玩完，中途不换件新的"]
        return outcome

    # 这个世界有**预写**的事件（预设的六份第一章就这么进来的）：
    # 先装现成的，装得上就不调模型。装的是「玩家当前省」的那几件，
    # 远处的等玩家走远了再听说。
    ready = preset_events(save)
    if ready:
        chosen = ready[0]
        outcome.slot = chosen
        outcome.notice = _event_notice(chosen, pool.day)
        return outcome

    from .gen_event import event_agent as _agent

    scenes = event_scene_names(str(getattr(save, "world_key", "") or ""))
    if not scenes:
        outcome.problems = ["这个世界里还没有任何场景，织不出事件"]
        return outcome

    level = scale or event_scale(save)
    inputs = _event_inputs(save, pool, level, scenes)
    chosen: EventSlot | None = None
    problems: list[str] = []
    # 软建议不进 ``problems``（那会造成重织），但要原样带回给模型。
    tips: list[str] = []
    for attempt in range(_EVENT_RETRIES + 1):
        outcome.attempts = attempt + 1
        await _notify(on_progress, f"正在织一件新事（第 {attempt + 1} 次尝试）……")
        try:
            result = await (agent or _agent).run(**inputs)
        except Exception as e:  # 织不出来不该炸掉这一轮
            log.warning(f"事件生成失败：{e!r}")
            problems = [f"模型调用失败：{e}"]
            tips = []
            continue
        outcome.usages.extend(getattr(result, "usages", None) or [])

        candidate = result.slot
        problems = list(result.problems)
        tips = []  # 每轮重算：上一版的建议对这一版不一定还成立
        if candidate is not None:
            # 程序决定项：id 不撞车、从今天起挂、图与事件同档
            candidate.id = new_event_id(pool)
            candidate.since_day = pool.day
            candidate.scale = level
            candidate.graph.scale = level
            allowed = set(scenes)
            stray = sorted(
                {
                    n.at
                    for n in candidate.graph.nodes.values()
                    if n.at and n.at not in allowed
                }
            )
            if stray:
                problems.append("这些所在地不在场景候选里：" + "、".join(stray))
            problems.extend(validate(candidate.graph))
            # 软建议单独走一趟：不进 problems（那会造成重织），但**原样带回**
            # 给下一次——模型该知道上一版哪里「最好改」，和「必须改」分开说。
            tips = advice(candidate.graph, opening=_at_opening(pool))
            if tips:
                outcome.tips = list(tips)
            if not problems:
                chosen = candidate
                break
        # 把问题原样带回给下一次：模型得知道上一版哪里不合法才会改
        inputs["context"] = (
            str(inputs.get("context") or "")
            + "\n\n上一次输出被退回，请修正这些问题：\n- "
            + "\n- ".join(problems)
            + ("\n\n另外这些是建议，不是必须：\n- " + "\n- ".join(tips) if tips else "")
        )

    outcome.problems = problems
    if chosen is None:
        log.warning(f"新事件没织成：{problems}")
        return outcome

    # 落池：超上限就顶掉最旧的 pending。存在期到「被影响」为止，
    # **新织的这件就是影响它的那件**——只动 pending，playing 的不动。
    pending = [s for s in pool.slots.values() if s.state == "pending"]
    overflow = len(pending) + 1 - MAX_OPEN_SLOTS
    if overflow > 0:
        pending.sort(key=lambda s: (s.since_day, s.id))
        for old in pending[:overflow]:
            close_event(pool, old.id, day=pool.day, by=chosen.title or chosen.id)

    pool.slots[chosen.id] = chosen
    pool.current = chosen.id
    pool.idle_hops = 0
    outcome.slot = chosen
    outcome.notice = _event_notice(chosen, pool.day)
    mirror_event(save, pool)
    _flush(save, pool, outcome.notice)
    return outcome


# --------------------------------------------------------------------------- #
# DM 侧简报
# --------------------------------------------------------------------------- #


def sync_world(save, event_no: int) -> int:
    """把世界演化推进到第 ``event_no`` 个事件，返回本档的世界压力加成。

    **幂等**（见 :func:`~...dnd.evolution.advance_world`），所以多个调用点
    都调它也不会重复加压——这正是把它做成幂等的原因。
    读世界圣经失败时按「没有势力」处理，不该拦住算难度。
    """
    from ...dnd.evolution import (
        advance_world,
        load_evolution,
        pressure_bonus,
        store_evolution,
    )
    from ...dnd.world_bible import load_bible

    state = load_evolution(save)
    entities: list[object] = []
    try:
        entities = list(load_bible(save.world_key).entities)
    except (OSError, ValueError) as e:
        log.warning(f"读世界圣经失败（世界演化轨按无势力处理）：{str_traceback(e)}")
    advance_world(state, event_no, entities=entities)
    store_evolution(save, state)
    return pressure_bonus(state)


def current_event_no(save) -> int:
    """眼下在跑第几个事件（已完成数 + 1）。

    难度序号轨、威胁层级、世界演化轨、纪元结算都拿它当刻度——以前这个数
    是「完成到第几章」，章就是事件，所以这里直接数已了结的事件槽。
    """
    pool = load_pool(save)
    return sum(1 for s in pool.slots.values() if s.state == "closed") + 1


def event_brief(save) -> str:
    """给 DM 看的事件简报：世界时间、当前事件、流言、玩家留下的账。

    和 ``_event_brief`` 并列注入。**没有事件时也要说话**——
    「眼下没有值得追的事」本身就是信息，不然 DM 会以为是自己漏了。
    """
    pool = load_pool(save)
    lines = [f"【世界时间】{_stamp(pool)} · {period_of(pool.hours)}"]
    slot = current_slot(pool)
    if slot is None:
        lines.append("【当前事件】无。这一带眼下没有值得追的事——照常过日常即可。")
    else:
        lines.append(f"【当前事件】{slot.title}")
        scale = {"light": "小", "medium": "中", "heavy": "大"}.get(
            slot.scale, slot.scale
        )
        where = f"，在{slot.stage}" if slot.stage else ""
        lines.append(f"　规模：{scale}｜大概时候：{slot.duration_days} 天{where}")
        scenes = sorted(_event_scenes(slot))
        if scenes:
            lines.append("　相关场景：" + "、".join(scenes))
        rumor = slot.rumor_for(pool.day)
        if rumor:
            lines.append(f"　眼下传的话：「{rumor}」")
        lines.append(
            "　玩家手里的线索（事实）决定哪些局面会活过来——**别把还没拿到的说成已经知道**。"
        )
        # 局面清单：冲突、已握有、现在能做的（含代价与提示）、到点的时钟、已成立的收束。
        # 不列整张图——每轮重发用不上的九成，纯浪费。
        state = graph_text(slot.graph, facts=slot.facts, visited=slot.fired)
        if state:
            lines.append(state)
        lines.append(
            "　推进方式：玩家解决了哪个局面，就用 note_event 写那个局面的名字"
            "（前缀「失败：」表示谈崩）——发什么事实、丢什么互斥、哪个收束成立，"
            "**全由程序结算**，你别凭空发线索。"
        )
        lines.append(
            "　局面不够用时：note_event 的 clue 写「新局面 {json}」现场造节点"
            "接进图（过校验才接，写法见系统规则）。"
        )

    from ...dnd.events import recent_modifiers

    done = recent_modifiers(pool, limit=4)
    if done:
        lines.append("【他们做过的事】" + "；".join(m.line() for m in done))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 手写样例事件（生成器接入前用它验证运行时）
# --------------------------------------------------------------------------- #


def sample_event(world_key: str = "", *, day: int = 1) -> EventSlot:
    """一个**手写**的完整事件，用来在生成器接入前跑通运行时。

    它同时是一份「合格长什么样」的参照：两个收束（一个有两套条件、一个要打完）、
    一场战斗、一条不需要战斗的撤退路、五个各有代价的节点。

    **它不是内容方案**——真实事件由 ``gen_event`` 织，这里只是把链路先跑起来。
    """
    graph = ModuleGraph(
        conflict="盐仓在把官盐换成私盐，而码头的账面上一切正常——谁在替他们记这笔账",
        scale="light",
    )
    graph.facts = {
        "gap": Fact(id="gap", text="渡口报上来的泊位钱比往年少了一成", kind="proof"),
        "ledger": Fact(id="ledger", text="渡口还留着第二套账簿", kind="proof"),
        "tunnel": Fact(id="tunnel", text="旧盐井底下有条通海的窄道", kind="access"),
        "guard": Fact(id="guard", text="桩林水下的守卫已经被清了", kind="access"),
        "witness": Fact(id="witness", text="被关在浮屋里的那个记账人", kind="debt"),
    }
    graph.nodes = {
        "n1": Node(
            id="n1",
            title="渡头棚的两本账",
            at="渡鸦渡口",
            kind="clue",
            grants=["gap"],
            hint="上层是公会账，下层隔板后还压着一叠。",
        ),
        "n2": Node(
            id="n2",
            title="跟老鸦陈摊牌",
            at="渡鸦渡口",
            kind="encounter",
            requires=["gap"],
            grants=["ledger"],
            on_fail=["gap"],
            cost="他会记住你的脸，之后渡口不再做你的生意",
            hint="他怕的不是被查，是查的人问出「免掉的那笔船钱」。",
        ),
        "n3": Node(
            id="n3",
            title="下到旧盐井底层",
            at="旧盐井",
            kind="clue",
            requires=["gap"],
            grants=["tunnel"],
            cost="每下一次，井口就多一道别人的记号",
            hint="底层东北有水声，还有拖痕。",
        ),
        "n4": Node(
            id="n4",
            title="桩林水下的守卫",
            at="旧盐井",
            kind="battle",
            requires=["tunnel"],
            grants=["guard"],
            cost="在水里打，火把与卷轴都用不上",
            hint="箱子旁边有东西在动，先动的是它。",
        ),
        "n5": Node(
            id="n5",
            title="拆开钉死的浮屋",
            at="渡鸦渡口",
            kind="encounter",
            requires=["ledger"],
            grants=["witness"],
            cost="拆钉会惊动整片浮屋，当晚就走不掉了",
            hint="里面的人才是真账本。",
        ),
    }
    graph.convergences = [
        Convergence(
            id="c_report",
            title="把账交到码头账房",
            # 两套条件：对质拿到账簿，或者从窄道直接摸进去
            requires=[["gap", "ledger"], ["gap", "tunnel"]],
        ),
        Convergence(
            id="c_burn",
            title="烧掉，谁也不提",
            requires=[["gap", "ledger", "guard"]],
        ),
    ]
    return EventSlot(
        id="ev_pier_ledger",
        title="最少的泊位钱",
        since_day=int(day),
        duration_days=3,
        scale="light",
        stage="白港一带",
        default_outcome="渡口换了个把头，账目照旧，私盐照走——只是换了一条船",
        rumors=[
            "听说渡口夜里不太平。",
            "听说是教团的人在背后收货。",
            "三号仓昨夜塌了一角，掉出来的东西被连夜运走了。",
        ],
        graph=graph,
    )
