"""事件难度预算与表现系数。

这一模块回答两个问题：

1. **这一章该给玩家多少压力？** —— :func:`event_cr`
2. **玩家打得太顺 / 打得太惨，该不该调？** —— :func:`performance_factor`

它只做算术与统计，**不碰存档、不调模型**。所有入参都是从存档与事件日志里取出来的
数值，所以每个函数都是纯函数、可直接断言（这是本项目里最容易做对也最该做测试的部分）。

规则锚点是 DMG 的 **Adventuring Day**：一个 4 小时时段能承受的经验量约等于
「阈值低档 × 人数 × 难度倍率」，难度倍率 easy 2 / medium 4 / hard 6。
难度倍率直接除以 4 得到「每场遭遇」的口径，因为一场遭遇大约吃掉一个时段的四分之一到三分之一。

三层混合难度（见 :func:`event_cr`）：

- **基础轨**：队伍平均等级，与 :func:`~.encounter.target_cr_for` 的单人口径一致；
- **事件轨**：随事件序号缓慢爬升，并叠加威胁层级（20 级后的压力走**规则层**，不动数值）；
- **表现轨**：:func:`performance_factor`，玩家打得太顺就加压、翻车就减压。

> 为什么 20 级后不堆怪：等级表（:data:`~.progression.MAX_LEVEL`）与
> :data:`~.progression.CR_XP`（最高档 CR20）在 20 级同时封顶，而预算要放大约 3000 倍。
> 所以 :class:`ThreatTier` 的加成都写成**规则描述**而不是数字，交给 DM 落地。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import exp as _exp
from typing import Any

try:  # pragma: no cover - typing 只在 3.9+ 存在
    from collections.abc import Mapping
except ImportError:  # pragma: no cover
    from collections.abc import Mapping  # type: ignore[assignment]

__all__ = [
    "DEFAULT_TIERS",
    "DIFFICULTY_RATIO",
    "PERFORMANCE_WEIGHTS",
    "Performance",
    "ThreatTier",
    "active_tier",
    "active_tiers",
    "day_budget",
    "encounter_budget",
    "event_cr",
    "event_cr_text",
    "event_xp_budget",
    "performance_factor",
    "summarize_performance",
]


#: DMG Adventuring Day 的难度倍率（× 阈值低档 × 人数）
DIFFICULTY_RATIO: dict[str, float] = {
    "easy": 2.0,
    "medium": 4.0,
    "hard": 6.0,
    "deadly": 8.0,
}

#: 一场遭遇大约吃掉一个 4 小时时段的多少
_ENCOUNTER_SHARE = 1.0 / 3.0

#: 难度倍率 → 表现系数的缓动系数。困难局本就该一直紧绷，不该由表现大幅摆动。
_EASE_BY_RATIO: dict[float, float] = {
    2.0: 0.45,  # easy
    4.0: 0.38,  # medium
    6.0: 0.30,  # hard
    8.0: 0.24,  # deadly
}

#: 表现系数各项的权重。四项相加为 1，单项封顶，保证系数落在 [-1, +1]。
PERFORMANCE_WEIGHTS: dict[str, float] = {
    "combat_speed": 0.3,  # 打得太快 → 说明太轻松 → 加压
    "knockdown": 0.35,  # 倒地次数 → 越多次说明越吃力 → 减压
    "resource": 0.2,  # 长休次数 → 消耗不起 → 减压
    "attrition": 0.15,  # 战斗场次偏多 → 战线拉太长 → 减压
}


# --------------------------------------------------------------------------- #
# 预算
# --------------------------------------------------------------------------- #


def _threshold_low(level: int) -> int:
    """取 DMG 阈值表的低档（升到下一级所需的累计经验）。"""
    from .progression import LEVEL_XP_THRESHOLDS, MAX_LEVEL

    lv = max(1, min(int(level), MAX_LEVEL))
    if lv >= MAX_LEVEL:
        return LEVEL_XP_THRESHOLDS[MAX_LEVEL - 1]
    return LEVEL_XP_THRESHOLDS[lv]


def day_budget(level: int, party_size: int = 4, difficulty: str = "medium") -> int:
    """一个 4 小时冒险时段能承受的经验总量。

    :param level: 队伍平均等级
    :param party_size: 参与玩家人数
    :param difficulty: ``easy`` / ``medium`` / ``hard`` / ``deadly``
    """
    ratio = DIFFICULTY_RATIO.get(
        str(difficulty or "medium"), DIFFICULTY_RATIO["medium"]
    )
    size = max(1, int(party_size))
    return round(_threshold_low(level) * size * ratio)


def encounter_budget(
    level: int, party_size: int = 4, difficulty: str = "medium"
) -> int:
    """单场遭遇该给的经验量（时段预算 ÷ 3）。"""
    return round(day_budget(level, party_size, difficulty) * _ENCOUNTER_SHARE)


def event_xp_budget(
    level: int, story_points: int = 3, party_size: int = 4, difficulty: str = "medium"
) -> int:
    """一章该花掉的经验总量。

    Story Point 约合 30 分钟（DMG 口径），一个时段装4 个推进点，所以
    「一章 ≈ 3 个推进点」≈ 三分之一个时段。这个函数把节奏锚翻译成数字，
    让「这一章打几场」有据可依，而不是靠 DM 自觉。
    """
    points = max(1, min(int(story_points), 12))
    return round(encounter_budget(level, party_size, difficulty) * points)


# --------------------------------------------------------------------------- #
# 表现系数
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Performance:
    """一章之内的战斗表现统计（全部由已有数据算出，不新增存档字段）。

    :param combats: 战斗场次
    :param rounds_total: 所有战斗的回合数之和
    :param knockdowns: 玩家倒地次数（含阵亡）
    :param long_rests: 长休次数
    """

    combats: int = 0
    rounds_total: int = 0
    knockdowns: int = 0
    long_rests: int = 0

    @property
    def rounds_avg(self) -> float:
        """平均每场战斗的回合数。"""
        return self.rounds_total / self.combats if self.combats > 0 else 0.0


def summarize_performance(events: Iterable[Any]) -> Performance:
    """从事件日志里数出本章的表现统计。

    认得两种事件：``kind`` 里带 ``combat`` / ``check`` 关键词的、以及正文里出现
    「长休」的事件。**刻意只做子串匹配**——事件日志是自由文本，
    往里塞结构化字段会给每个写入点增加负担，而统计口径本来就允许粗糙。

    :param events: 事件对象序列（需有 ``kind`` 与 ``content``）或 (kind, content) 二元组。
    """
    perf = Performance()
    for item in events:
        kind, content = _event_fields(item)
        low_kind = kind.lower()
        if "combat" in low_kind or "战斗" in content:
            perf.combats += 1
            perf.rounds_total += _rounds_from(content)
        if "倒地" in content or "阵亡" in content or "重伤" in content:
            perf.knockdowns += 1
        if "长休" in content or "long_rest" in low_kind:
            perf.long_rests += 1
    return perf


def _event_fields(item: Any) -> tuple[str, str]:
    """从事件里取出 ``(kind, content)``。

    认得三种形态：属性对象、``save_store.recent_events`` 返回的 dict、
    以及裸 (kind, content) 二元组。**dict 必须单独处理**——那是线上真实形态，
    用 ``getattr`` 取会永远拿到空串，表现系数就恒为 0。
    """
    if isinstance(item, Mapping):
        return str(item.get("kind") or ""), str(item.get("content") or "")
    kind = getattr(item, "kind", "")
    content = getattr(item, "content", "")
    if not kind and isinstance(item, tuple | list) and len(item) >= 2:
        kind, content = str(item[0] or ""), str(item[1] or "")
    return str(kind or ""), str(content or "")


def _rounds_from(content: str) -> int:
    """从战斗事件文本里提取回合数；认不出就按 3 回合估（保守）。"""
    for marker in ("回合", "轮"):
        idx = content.find(marker)
        if idx > 0:
            digits = ""
            for ch in reversed(content[:idx]):
                if ch.isdigit():
                    digits = ch + digits
                elif digits:
                    break
                if len(digits) >= 2:
                    break
            if digits:
                return max(1, min(int(digits), 30))
    return 3


#: 各项指标在这个值以内算「正常」，超过则线性加压/ 减压。
#: ``combat_speed`` 是**回合数**：DMG 口径下一场战斗通常 3 轮左右结束，
#: 所以甜点位取 3——比它快算轻松，比它慢才算吃力。
_SWEET_SPOT: dict[str, float] = {
    "combat_speed": 3.0,  # 平均回合数：3 轮以内算打得快
    "knockdown": 0.0,  # 一次不倒最好
    "resource": 0.0,  # 不长休最好
    "attrition": 2.0,  # 两场以内算正常
}

#: 「打满负面」所需的回合数。DMG 的 5e 战斗通常 3 轮左右结束，
#: 打满 3 轮仍然结束说明队伍游刃有余，拖到 8 轮才叫真的吃力——
#: 甜点位设在 3 而不是 2.5，否则一场正常的 4 轮战斗就被当成「翻车」。
_FULL_STRAIN = 8.0


def _normalize(name: str, value: float) -> float:
    """把单项指标压到 [-1, 1]：负号表示「吃力」（该减压），正号表示「轻松」（该加压）。

    回合数走**分段插值**而不是除法：0~2 轮算轻松，3 轮（DMG 常规）算持平，
    之后线性滑向 8 轮的「吃力」。用除法的话 4 轮就打满负面，
    结果是几乎每章都在减压——那不是自适应，是单向漂移。
    """
    sweet = _SWEET_SPOT.get(name, 0.0)
    value = max(0.0, float(value))
    if name == "combat_speed":
        if value <= sweet:
            span = max(1.0, sweet)
            return max(-1.0, min(1.0, (sweet - value) / span))
        span = max(1.0, _FULL_STRAIN - sweet)
        return -min(1.0, (value - sweet) / span)
    # 其余都是「越多越吃力」：0 → +1，超过 3 倍甜点位 → -1
    span = max(1.0, sweet * 3 if sweet > 0 else 3.0)
    return max(-1.0, min(1.0, (sweet - value) / span * 2))


def performance_factor(
    perf: Performance, *, difficulty: str = "medium", enabled: bool = True
) -> float:
    """表现系数：``+0.2`` 表示加两成压力，``-0.2`` 表示减两成。

    难度越高，系数越收敛——困难局本来就该一直紧绷，不该由表现大幅摆动。
    缓动系数分别是 easy 0.45 / medium 0.38 / hard 0.30 / deadly 0.24。

    **没有任何统计时返回 0**（按基准难度来）。这不是「表现完美」而是「没数据」：
    新开一局的事件日志是空的，各项归一化却会把「零场战斗、零倒地」读成
    「打得异常顺利」，于是第一章凭空加压两成。区分不了就别猜。
    """
    if not enabled:
        return 0.0
    if perf.combats <= 0 and perf.knockdowns <= 0 and perf.long_rests <= 0:
        return 0.0
    ratio = DIFFICULTY_RATIO.get(
        str(difficulty or "medium"), DIFFICULTY_RATIO["medium"]
    )
    scale = _EASE_BY_RATIO.get(ratio, 0.38)

    terms = {
        "combat_speed": _normalize(
            "combat_speed", perf.rounds_avg if perf.combats else 0.0
        ),
        "knockdown": _normalize("knockdown", perf.knockdowns),
        "resource": _normalize("resource", perf.long_rests),
        # 只有真的打过两场以上才谈得上「消耗」：0 场不是「消耗为零」，
        # 而是没数据，那一项必须中性（上面的零数据短路已覆盖，这里是双保险）
        "attrition": (
            _normalize("attrition", perf.combats - 1) if perf.combats > 1 else 0.0
        ),
    }
    total = sum(PERFORMANCE_WEIGHTS[k] * v for k, v in terms.items())
    return round(max(-1.0, min(1.0, total)) * scale, 3)


# --------------------------------------------------------------------------- #
# 事件难度合成
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ThreatTier:
    """威胁层级：20 级之后靠**规则层**加压，不动经验与数值。

    :param name: 层级名
    :param mod: 规则描述（自由文本，交给 DM 落地，如「首领具传奇行动」）
    :param tag: 机器可读的标签，供生成器与校验器使用
    """

    name: str
    mod: str
    tag: str = ""


#: 默认的四档威胁层级。``after_event`` 是「从第几章之后生效」。
DEFAULT_TIERS: tuple[tuple[int, ThreatTier], ...] = (
    (
        5,
        ThreatTier(
            "白银压境",
            "敌方专攻：优先攻击看起来最脆弱的冒险者，而不是 AC 最高的",
            "elite",
        ),
    ),
    (
        9,
        ThreatTier(
            "灰潮涨落",
            "每场战斗结束前追加一波增援（杂兵，按剩余预算打折）",
            "attrition",
        ),
    ),
    (
        13,
        ThreatTier(
            "冬王亲临",
            "首领具传奇行动：每个自己的回合额外一次行动，不占动作",
            "legendary",
        ),
    ),
    (
        17,
        ThreatTier(
            "世界律失效",
            "场地规则改变（如涨潮、落雪、灯火限制），并叠加天气代价",
            "rulebreak",
        ),
    ),
)


def active_tiers(
    event_no: int, tiers: Iterable[tuple[int, ThreatTier]] = DEFAULT_TIERS
) -> list[ThreatTier]:
    """取该事件**全部**生效的威胁层级（按生效章号升序）。

    **层级是叠加的，不是互斥的。** 第 13 章同时满足 elite（第 5 章后）、
    attrition（第 9 章后）与 legendary（第 13 章后）三档——它们加压的方式各不相同
    （换目标 / 补增援 / 多一次行动），互不冲突，所以要一起生效。

    早先这里只返回最高的一档（:func:`active_tier` 仍是那个行为），
    结果第 13 章时前两档被静默丢弃——压力凭空少了两层。
    """
    event_no = max(1, int(event_no))
    picked: list[tuple[int, ThreatTier]] = []
    for after, tier in tiers:
        if event_no > after:
            picked.append((after, tier))
    picked.sort(key=lambda item: item[0])
    return [tier for _after, tier in picked]


def active_tier(
    event_no: int, tiers: Iterable[tuple[int, ThreatTier]] = DEFAULT_TIERS
) -> ThreatTier | None:
    """取该事件生效的**最高**威胁层级（兼容旧调用；多数场合该用 :func:`active_tiers`）。"""
    picked = active_tiers(event_no, tiers)
    return picked[-1] if picked else None


def event_cr(
    *,
    level: int,
    event_no: int = 1,
    party_size: int = 4,
    difficulty: str = "medium",
    perf_factor: float = 0.0,
    world_pressure: float = 0.0,
    ladder_cap: float = 3.0,
    ladder_half_life: float = 6.0,
    tiers: Iterable[tuple[int, ThreatTier]] = DEFAULT_TIERS,
    adapt: bool = True,
) -> float:
    """合成这一章的目标 CR。

    三层混合：

    - **基础轨**：队伍平均等级（与 :func:`~.encounter.target_cr_for` 的单人口径一致）；
    - **事件轨**：随事件序号爬升，**递减趋近** :paramref:`ladder_cap`
      （指数半衰），所以前期涨得明显、后期每几章才动一点，不会早早撞到天花板变平；
    - **表现轨**：`perf_factor`（-1~+1），难度越高越收敛；
    - **世界演化轨**：:paramref:`world_pressure`（见 :func:`~.evolution.pressure_bonus`），
      世界自己在往前走的那个部分。

    威胁层级**不参与**这里的加法——它是规则层的压力，混进 CR 会让数值失真。
    但演化轨**要**参与：它是「同一个 NPC 位置变高了」，本来就该换更硬的怪。
    所以本模块不import :mod:`.evolution`（纯层之间不互相依赖），
    由编排层 :mod:`..agents.dnd.event_progress_flow` 算出再传进来。

    **为什么高等级要收敛表现轨**：5e 里等级阈值涨得越来越慢（1→10 级是十倍经验，
    17→20 级只多一半），所以「这一章打得顺不顺」对高等级队伍的影响本就该更小。
    不做收敛的话，线性加一个 ±1 的 CR 在18 级队伍身上的相对幅度是等级 3 时的六倍，
    那是在噪声上加压。见 :func:`_adapt_damp`。
    """
    lv = max(1, int(level))
    ch = max(1, int(event_no))
    size = max(1, int(party_size))

    base = float(lv) + 0.5 * (size - 4)
    cap = max(0.0, float(ladder_cap))
    half = max(0.5, float(ladder_half_life))
    ladder = cap * (1.0 - _exp(-(ch - 1) / half))
    adapt_term = float(perf_factor) if adapt else 0.0
    pressure = max(0.0, float(world_pressure))
    return round(max(0.0, base + ladder + pressure + adapt_term * _adapt_damp(lv)), 2)


#: 表现轨的收敛：等级 1 不衰减，每级 ×衰减，最低到 :data:`_ADAPT_FLOOR`。
_ADAPT_DECAY = 0.03
_ADAPT_FLOOR = 0.45


def _adapt_damp(level: int) -> float:
    """等级越高，表现轨的加压/减压幅度越小（见 :func:`event_cr` 的说明）。"""
    return max(_ADAPT_FLOOR, 1.0 - _ADAPT_DECAY * (max(1, int(level)) - 1))


def event_cr_text(
    *,
    level: int,
    event_no: int = 1,
    party_size: int = 4,
    difficulty: str = "medium",
    perf_factor: float = 0.0,
    world_pressure: float = 0.0,
    tiers: Iterable[tuple[int, ThreatTier]] = DEFAULT_TIERS,
    adapt: bool = True,
) -> str:
    """:func:`event_cr` 的文本写法（CR 0.5 → ``1/2``），附威胁层级提示。"""
    from .encounter import _cr_text

    cr = event_cr(
        level=level,
        event_no=event_no,
        party_size=party_size,
        difficulty=difficulty,
        perf_factor=perf_factor,
        world_pressure=world_pressure,
        tiers=tiers,
        adapt=adapt,
    )
    text = _cr_text(cr)
    tiers_on = active_tiers(event_no, tiers)
    if tiers_on:
        detail = "；".join(f"{t.name}——{t.mod}" for t in tiers_on)
        text = f"{text}（威胁层级：{detail}）"
    return text
