"""世界演化轨：世界自己往前走，玩家不干预也会变强。

设计背景（见 ``docs/trpg-endless-module-design.md`` 第 3.3 节）：难度三轴里的
第三轴。等级轴被 20 级封顶、威胁轴（见 :mod:`.threat`）改的是**战斗规则**，
这一轴改的是**世界本身**——它是最便宜的一条：零模型成本，纯程序。

三件事：

============  ==================  ==============================================
内容          载体                程序负责的事
============  ==================  ==============================================
势力推进      ``factions``        按章数推进势力的**位置**（不是数值）
地点状态      ``places``          痕迹累积：驻过军 / 烧过半条街 / 封了渡口
期限压力      ``deadlines``       逐章推进，到期不因为玩家完成任务而停下
============  ==================  ==============================================

**为什么势力变强是「位置变」而不是「数值变」**：20 级之后经验表与 CR 表双封顶
（见设计文档 1.1），堆数值这条路已经走死了。剩下的加压方式是**同一个 NPC 在世界
里的位置变了**——从「可疑的账房」变成「东区的实际控制者」。同一批人、同一套
数值，但他们的命令现在没人敢不接。这条轴和威胁轴互补：威胁轴改战斗规则，
演化轴改世界格局。

**为什么全是程序算的**：阶段推进是除法、痕迹累积是取模、期限是计数器。交给模型
它会写成「势力日益壮大」这种没有后果的散文。这里每个阶段、每条痕迹都有确定的
后果（:func:`pressure_bonus` 把它换成难度上的实际让步），模型只负责叙述。

落盘在 ``save.progress["_world_state"]``（下划线开头 = 内部键，不会被原样发给玩家）。
这一模块**不碰存档**：读存档的活儿在 :mod:`..agents.dnd.chapter_flow`，
和 :mod:`.threat` 一样的分层纪律。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEADLINE_EVERY",
    "DEADLINE_MAX",
    "EVOLUTION_KEY",
    "FACTION_STAGES",
    "FactionState",
    "LOG_MAX",
    "PLACE_MARKS",
    "PlaceState",
    "WorldEvolution",
    "advance_world",
    "deadline_names",
    "evolution_text",
    "faction_agenda",
    "load_evolution",
    "mark_place",
    "note_scene_change",
    "place_mark_for",
    "pressure_bonus",
    "store_evolution",
]


#: 演化状态在 ``save.progress`` 里的键名。下划线开头 = 内部键。
EVOLUTION_KEY = "_world_state"

#: 演化日志条数上限。每章都要注入提示词，条数失控等于成本失控。
LOG_MAX = 8

#: 势力位置的档数。**位置变，不是数值变**——见模块文档。
FACTION_STAGES: tuple[str, ...] = (
    "还没人听说过",
    "开始在明面上活动",
    "已经无人敢当众反驳",
    "这一带的事都由他们说了算",
)

#: 每推进这么多章，势力往上走一档
_STAGE_EVERY = 4

#: 势力走完全部档位之后，每这么多章再记一条「盘得更深」的表现
_MATURE_EVERY = 5

#: 地点痕迹池。**按顺序**取，不随机——同一地点第二次被打应当是「又烧了一层」，
#: 而不是「这回改成淹了」。
PLACE_MARKS: tuple[str, ...] = (
    "驻过军",
    "封了渡口",
    "烧了半条街",
    "征了粮",
    "贴了告示",
    "断了商路",
    "换了旗",
)

#: 关键词 -> 痕迹措辞。DM 在 ``update_scene`` 里写的类型与内容命中关键词时
#: 就落这条痕迹，认不出就不记（**不硬套**——把「井水变咸了」归成「断了商路」
#: 是往存档里塞假信息，比不记更坏）。
_PLACE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("烧", "烧了半条街"),
    ("焚", "烧了半条街"),
    ("火", "烧了半条街"),
    ("驻军", "驻过军"),
    ("驻", "驻过军"),
    ("兵", "驻过军"),
    ("封", "封了渡口"),
    ("渡", "封了渡口"),
    ("关", "封了渡口"),
    ("征粮", "征了粮"),
    ("征", "征了粮"),
    ("收租", "征了粮"),
    ("告示", "贴了告示"),
    ("榜", "贴了告示"),
    ("断", "断了商路"),
    ("封路", "断了商路"),
    ("换旗", "换了旗"),
    ("易主", "换了旗"),
)


def place_mark_for(kind: str = "", text: str = "") -> str:
    """从一条场景变化里认出地点痕迹；认不出返回空串。

    刻意保守：只在**命中关键词**时给痕迹。地点状态是玩家看得见的东西，
    认错等于凭空往世界里加一件没发生过的事——那比漏记坏得多。
    """
    haystack = f"{kind} {text}"
    for word, mark in _PLACE_KEYWORDS:
        if word in haystack:
            return mark
    return ""

#: 每个势力在这一地区留下的痕迹条数门槛（每 ``_MARK_EVERY`` 章加一条）
_MARK_EVERY = 2

#: 世界级期限：每这么多章生成一个
DEADLINE_EVERY = 5

#: 未结期限的条数上限（超了强制结算最老的一个，别让期限无限堆）
DEADLINE_MAX = 3

#: 期限名模板。``{n}`` 是第几个期限。
_DEADLINE_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("查税", "{n} 拨的税要查下来，谁也躲不过"),
    ("封渡", "官府要在第 {n} 个期限前封了渡口"),
    ("征兵", "这一带在第 {n} 个期限前要征一轮兵"),
    ("清剿", "上头下了清剿令，第 {n} 个期限前要有结果"),
    ("收租", "第 {n} 期的租子到期不缴就要动人"),
    ("换防", "第 {n} 批换防的兵要到了"),
)

#: 期限到期的后果。``faction`` 表示那个势力因此得势。
_DEADLINE_OUTCOMES: tuple[tuple[str, str], ...] = (
    ("欠账", "这笔账记在了某个人头上，期限一过就有人上门来收"),
    ("势力得势", "空出来的位置被某个势力顺势吃下了"),
    ("道路不通", "路封了，绕道要多走两天"),
    ("人心散了", "该给的东西没给到，下面的人开始各找各的门路"),
)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


def _h(name: str) -> int:
    """名字的稳定整数散列（同一名字每次都得到同一个值）。

    用 sha256 而不是内置 ``hash()``：内置散列每个进程都换种子，
    那样「这个势力比其他势力快一步」重启一次就变了。
    """
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


@dataclass(slots=True)
class FactionState:
    """一个势力的演化状态。"""

    #: 规范名（与 :class:`~.world_bible.Entity` 一致）
    name: str
    #: 所属地区（决定它往哪儿扩张，也是痕迹累积的地方）
    region: str = ""
    #: 一句话备注（来自圣经登记；位置描述会拿它做前缀）
    note: str = ""
    #: 当前档位（0 起）
    stage: int = 0
    #: 走完全部档位后的额外表现计数
    mature: int = 0
    #: 这个势力已经在这个地区留下的痕迹条数
    marks: int = 0
    #: 最早登记的一章（用来算「它从第几章开始动的」）
    since: int = 0
    #: 演化日志（玩家完成不了的时候也得走）

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "region": self.region,
            "note": self.note,
            "stage": self.stage,
            "mature": self.mature,
            "marks": self.marks,
            "since": self.since,
        }

    @classmethod
    def from_dict(cls, data: Any) -> FactionState | None:
        d = data if isinstance(data, dict) else {}
        name = str(d.get("name", "") or "").strip()
        if not name:
            return None
        return cls(
            name=name,
            region=str(d.get("region", "") or ""),
            note=str(d.get("note", "") or ""),
            stage=int(d.get("stage", 0) or 0),
            mature=int(d.get("mature", 0) or 0),
            marks=int(d.get("marks", 0) or 0),
            since=int(d.get("since", 0) or 0),
        )

    @property
    def stage_label(self) -> str:
        """位置描述（这是 DM 叙述时要用的那句，不是数值）。"""
        if self.stage < len(FACTION_STAGES):
            return FACTION_STAGES[self.stage]
        return f"已经盘得极深（{FACTION_STAGES[-1]}，而且没人管得住）"

    def line(self) -> str:
        where = f"（{self.region}）" if self.region else ""
        return f"{self.name}{where}：{self.stage_label}"


@dataclass(slots=True)
class PlaceState:
    """一个地点累积到的状态。"""

    name: str
    #: 累积的痕迹（按发生顺序，:data:`PLACE_MARKS` 里的措辞）
    marks: list[str] = field(default_factory=list)
    #: 最早出现的一章
    since: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "marks": list(self.marks), "since": self.since}

    @classmethod
    def from_dict(cls, data: Any) -> PlaceState | None:
        d = data if isinstance(data, dict) else {}
        name = str(d.get("name", "") or "").strip()
        if not name:
            return None
        return cls(
            name=name,
            marks=[str(m) for m in (d.get("marks") or []) if str(m).strip()],
            since=int(d.get("since", 0) or 0),
        )

    def line(self) -> str:
        if not self.marks:
            return f"{self.name}：还看不出异样"
        return f"{self.name}：{'、'.join(self.marks)}"


@dataclass(slots=True)
class Deadline:
    """一个世界级期限。**逐章推进，不因为玩家完成任务而停下。**"""

    name: str
    #: 起始章
    born: int = 0
    #: 到期章
    due: int = 0
    #: 一句话说明
    note: str = ""
    #: 到期后果（还没到期时为空）
    outcome: str = ""
    #: 到期后果的类别（欠账 / 势力得势 / ……）；用来程序化接后果，不给模型判断
    outcome_key: str = ""
    #: 是否已经结清
    settled: bool = False
    #: 逾期未结的章数
    overdue: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "born": self.born,
            "due": self.due,
            "note": self.note,
            "outcome": self.outcome,
            "outcome_key": self.outcome_key,
            "settled": self.settled,
            "overdue": self.overdue,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Deadline | None:
        d = data if isinstance(data, dict) else {}
        name = str(d.get("name", "") or "").strip()
        if not name:
            return None
        return cls(
            name=name,
            born=int(d.get("born", 0) or 0),
            due=int(d.get("due", 0) or 0),
            note=str(d.get("note", "") or ""),
            outcome=str(d.get("outcome", "") or ""),
            outcome_key=str(d.get("outcome_key", "") or ""),
            settled=bool(d.get("settled", False)),
            overdue=int(d.get("overdue", 0) or 0),
        )

    @property
    def remaining(self) -> int:
        """还剩几章（已逾期为负）。"""
        return self.due - self.overdue

    def line(self) -> str:
        if self.settled:
            return f"{self.name}：已了结"
        left = self.remaining
        if left > 0:
            return f"{self.name}：还剩 {left} 章（{self.note}）"
        return f"{self.name}：已逾期 {-left} 章（{self.outcome or self.note}）"


@dataclass(slots=True)
class WorldEvolution:
    """世界演化轨的完整状态，落在 ``save.progress["_world_state"]``。"""

    #: 已推进到的章节序号（同一章重复推进是幂等的）
    chapter: int = 0
    factions: list[FactionState] = field(default_factory=list)
    places: list[PlaceState] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    #: 演化日志（每条 ≤ 30 字，进 DM 提示词与玩家手册）
    log: list[str] = field(default_factory=list)
    #: 已生成的期限总数（用来取模板）
    deadline_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter": self.chapter,
            "factions": [f.to_dict() for f in self.factions],
            "places": [p.to_dict() for p in self.places],
            "deadlines": [d.to_dict() for d in self.deadlines],
            "log": list(self.log),
            "deadline_count": self.deadline_count,
        }

    @classmethod
    def from_dict(cls, data: Any) -> WorldEvolution:
        d = data if isinstance(data, dict) else {}
        return cls(
            chapter=int(d.get("chapter", 0) or 0),
            factions=[
                f
                for f in (FactionState.from_dict(x) for x in (d.get("factions") or []))
                if f
            ],
            places=[
                p
                for p in (PlaceState.from_dict(x) for x in (d.get("places") or []))
                if p
            ],
            deadlines=[
                x
                for x in (Deadline.from_dict(d) for d in (d.get("deadlines") or []))
                if x
            ],
            log=[str(x) for x in (d.get("log") or []) if str(x).strip()],
            deadline_count=int(d.get("deadline_count", 0) or 0),
        )

    # -- 查 ------------------------------------------------------------- #

    def faction(self, name: str) -> FactionState | None:
        for item in self.factions:
            if item.name == name:
                return item
        return None

    def place(self, name: str) -> PlaceState | None:
        for item in self.places:
            if item.name == name:
                return item
        return None

    def open_deadlines(self) -> list[Deadline]:
        return [d for d in self.deadlines if not d.settled]

    def dominant_faction(self) -> FactionState | None:
        """位置最高的那个势力（并列时取登记最早的——先来的是正史）。"""
        if not self.factions:
            return None
        return sorted(
            self.factions, key=lambda f: (-f.stage, -f.mature, f.since, f.name)
        )[0]

    # -- 写 ------------------------------------------------------------- #

    def note(self, text: str) -> None:
        """记一条演化日志（自动截长、超限丢最旧的）。"""
        body = str(text or "").strip()[:30]
        if not body:
            return
        if body in self.log:
            return
        self.log.append(body)
        while len(self.log) > LOG_MAX:
            self.log.pop(0)


# --------------------------------------------------------------------------- #
# 落盘（由读存档的那一层调用）
# --------------------------------------------------------------------------- #


def load_evolution(save: Any) -> WorldEvolution:
    """从存档读演化状态；没有或损坏时返回空状态（不抛错）。"""
    raw = (getattr(save, "progress", None) or {}).get(EVOLUTION_KEY)
    if not isinstance(raw, dict):
        return WorldEvolution()
    try:
        return WorldEvolution.from_dict(raw)
    except (TypeError, ValueError):
        return WorldEvolution()


def store_evolution(save: Any, state: WorldEvolution) -> None:
    """把演化状态写回存档。"""
    progress = getattr(save, "progress", None)
    if progress is None:
        return
    progress[EVOLUTION_KEY] = state.to_dict()


def note_scene_change(save: Any, place: str, kind: str = "", text: str = "") -> str:
    """从一条场景变化里顺带给地点记痕迹（零额外模型成本）。

    返回记下的痕迹措辞；认不出关键词时返回空串（**不硬套**，见
    :func:`place_mark_for` 的说明）。
    """
    mark = place_mark_for(kind, text)
    if not mark or not str(place or "").strip():
        return ""
    state = load_evolution(save)
    chapter = 0
    done = (getattr(save, "progress", None) or {}).get("_chapter")
    if isinstance(done, dict):
        chapter = int(done.get("completed", 0) or 0)
    mark_place(state, place, mark, chapter=chapter)
    store_evolution(save, state)
    return mark


# --------------------------------------------------------------------------- #
# 势力
# --------------------------------------------------------------------------- #


def faction_agenda(faction: FactionState) -> str:
    """这个势力在追求什么（一句话，程序合成的）。

    **为什么不调模型**：agenda 要的是「它要什么」这种可以从地区与身份推出来的
    东西，而模型每次给的说法都不一样，反而让「它一直在追求同一件事」这件事
    失效了。有 :attr:`FactionState.note` 就用它（有登记的事实优先于合成），
    没有才合成。
    """
    if faction.note:
        return faction.note
    where = faction.region or "本地"
    verb = ("扩张在%s的盘子" % where, "把%s握在自己手里" % where)[_h(faction.name) % 2]
    return f"{faction.name}想要{verb}"


def _seed_factions(state: WorldEvolution, entities: list[Any], chapter: int) -> None:
    """从圣经里的势力登记播种（零模型成本，重复调用幂等）。"""
    known = {f.name for f in state.factions}
    for entity in entities:
        if getattr(entity, "kind", "") != "faction":
            continue
        name = str(getattr(entity, "name", "") or "").strip()
        if not name or name in known:
            continue
        state.factions.append(
            FactionState(
                name=name,
                region=str(getattr(entity, "region", "") or ""),
                note=str(getattr(entity, "note", "") or ""),
                since=chapter,
            )
        )
        known.add(name)


def _advance_factions(state: WorldEvolution, chapter: int) -> list[str]:
    """推进所有势力的位置，返回本次要记的日志。"""
    lines: list[str] = []
    for faction in state.factions:
        span = chapter - faction.since
        if span <= 0:
            continue
        # 每个势力的相位错开（按名字散列），否则所有势力同一章一起动，太整齐
        offset = _h(faction.name) % _STAGE_EVERY
        target = (span + offset) // _STAGE_EVERY
        while faction.stage < target and faction.stage < len(FACTION_STAGES) - 1:
            faction.stage += 1
            lines.append(f"{faction.name}：{faction.stage_label}")
        if faction.stage >= len(FACTION_STAGES) - 1:
            mature_target = (span + offset) // (_STAGE_EVERY * _MATURE_EVERY)
            if mature_target > faction.mature:
                faction.mature = mature_target
                lines.append(f"{faction.name}已经不需要明着来了")
        # 痕迹累积：势力在自己地区留下的痕迹逐章变多
        mark_target = (span + offset) // _MARK_EVERY
        if mark_target > faction.marks:
            state.note(f"{faction.region or faction.name}：{_mark_text(state, faction)}")
            faction.marks = mark_target
    return lines


def _mark_text(state: WorldEvolution, faction: FactionState) -> str:
    """按累计条数取痕迹措辞（顺序取，不随机——见 :data:`PLACE_MARKS`）。"""
    where = faction.region or faction.name
    place = state.place(where)
    index = len(place.marks) if place else faction.marks
    mark = PLACE_MARKS[index % len(PLACE_MARKS)]
    if place is None:
        place = PlaceState(name=where, since=faction.since)
        state.places.append(place)
    place.marks.append(mark)
    return mark


def mark_place(state: WorldEvolution, name: str, mark: str, *, chapter: int = 0) -> PlaceState:
    """给一个地点记一条痕迹（战斗打过的场景、外部事件都能往这儿挂）。"""
    where = str(name or "").strip()
    if not where:
        raise ValueError("地点名为空")
    place = state.place(where)
    if place is None:
        place = PlaceState(name=where, since=chapter)
        state.places.append(place)
    body = str(mark or "").strip()[:20] or PLACE_MARKS[len(place.marks) % len(PLACE_MARKS)]
    place.marks.append(body)
    state.note(f"{where}：{body}")
    return place


# --------------------------------------------------------------------------- #
# 期限
# --------------------------------------------------------------------------- #


def deadline_names(count: int) -> tuple[str, str]:
    """第 ``count`` 个期限的名字与说明（模板按序取，不重复同一段说明）。"""
    key, note = _DEADLINE_TEMPLATES[count % len(_DEADLINE_TEMPLATES)]
    return f"第{count}件事·{key}", note.format(n=count)


def _advance_deadlines(state: WorldEvolution, chapter: int) -> list[str]:
    """推进期限：到期的结算后果，逾期的继续挂（**不会因为玩家做完事就停下**）。"""
    lines: list[str] = []
    for deadline in state.deadlines:
        if deadline.settled:
            continue
        if chapter >= deadline.due:
            if not deadline.outcome:
                key, text = _DEADLINE_OUTCOMES[
                    _h(deadline.name) % len(_DEADLINE_OUTCOMES)
                ]
                deadline.outcome = text
                deadline.outcome_key = key
            deadline.settled = True
            deadline.overdue = chapter - deadline.due
            lines.append(f"{deadline.name}到期了：{deadline.outcome}")
            continue
        deadline.overdue = max(0, deadline.overdue)
        if deadline.due - chapter <= 1:
            lines.append(f"{deadline.name}只差 {deadline.due - chapter} 章了")

    # 生成新期限：按 DEADLINE_EVERY 的节拍，超上限就强制结清最老的一个
    if chapter > 0 and chapter % DEADLINE_EVERY == 0:
        state.deadline_count += 1
        name, note = deadline_names(state.deadline_count)
        fresh = Deadline(
            name=name,
            born=chapter,
            due=chapter + _h(name) % 3 + 2,
            note=note,
        )
        state.deadlines.append(fresh)
        lines.append(f"新的期限：{fresh.line()}")
        while len(state.open_deadlines()) > DEADLINE_MAX:
            oldest = min(
                (d for d in state.open_deadlines()),
                key=lambda d: (d.due, d.born),
            )
            oldest.settled = True
            lines.append(f"{oldest.name}没人管，过了期限")
    return lines


# --------------------------------------------------------------------------- #
# 推进一章
# --------------------------------------------------------------------------- #


def advance_world(
    state: WorldEvolution,
    chapter: int,
    *,
    entities: list[Any] | None = None,
) -> WorldEvolution:
    """把世界推进到第 ``chapter`` 章（就地修改并返回同一个对象）。

    **幂等**：同一章重复推进不会重复加压——``chapter`` 不比当前大就直接返回。
    这样「结束一章」「建下一章」两处都能放心调用。

    :param entities: 圣经里的实体（用于播种势力），一般由调用方读好传进来。
    """
    chapter = max(0, int(chapter))
    if entities:
        _seed_factions(state, entities, chapter)
    if chapter <= state.chapter:
        return state

    lines: list[str] = []
    lines.extend(_advance_factions(state, chapter))
    lines.extend(_advance_deadlines(state, chapter))
    for line in lines:
        state.note(line)
    state.chapter = chapter
    return state


# --------------------------------------------------------------------------- #
# 对难度的实际影响
# --------------------------------------------------------------------------- #


def pressure_bonus(state: WorldEvolution) -> int:
    """世界演化给目标 CR 的加成（CR 档）。

    这是这一轴**唯一的数值出口**——压力必须落在某个可结算的地方，否则
    「世界在动」只是气氛。算法刻意很浅：主要档位跟着最高位置的势力走，
    逾期未结的期限再各加一点。这样玩家能反推：**我们拖了几件事没办**，
    世界就真的更危险一点。
    """
    bonus = 0
    top = state.dominant_faction()
    if top:
        # stage 0→4 分别 +0..+4 档 CR，每走完档再加半档
        bonus += min(6, top.stage + top.mature // 2)
    bonus += sum(1 for d in state.deadlines if not d.settled and d.overdue > 0)
    return min(8, bonus)


# --------------------------------------------------------------------------- #
# 文本
# --------------------------------------------------------------------------- #


def evolution_text(state: WorldEvolution, *, for_player: bool = False) -> str:
    """世界演化轨的展示文本。

    :param for_player: 玩家侧**不给看档位与期限的到期后果**——那是 DM 的账。
        玩家只该知道「世界确实在动」（痕迹、势力的位置），不该知道差几章到期。
    """
    lines: list[str] = []
    if state.factions:
        lines.append("【世界】")
        for faction in state.factions[:4]:
            lines.append(f"· {faction.line()}")
    if state.places:
        marks = [p for p in state.places if p.marks][:4]
        if marks:
            lines.append("· 各地留下的痕迹：" + "；".join(p.line() for p in marks))
    if for_player:
        return "\n".join(lines)
    if state.open_deadlines():
        lines.append("· 未结的期限：" + "；".join(d.line() for d in state.deadlines[:4]))
    if state.log:
        lines.append("· 最近：" + "；".join(state.log[-3:]))
    return "\n".join(lines)