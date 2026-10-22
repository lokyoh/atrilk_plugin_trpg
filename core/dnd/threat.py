"""威胁层级：把「后期加压」从一句描述变成可结算的机制。

设计背景（见 ``docs/trpg-endless-module-design.md`` 第 3.2 节）：20 级之后
经验表与等级表同时封顶，而预算要放大约 3000 倍。所以后期难度**不能**走数值层
（堆怪、堆经验），只能走**规则层**——加压的方式是「战斗规则变了」，
而不是「数字变大了」。

这一模块把四档威胁层级拆成**机器可执行的效果**：

============  ==================  ==============================================
档位           标签                程序负责的事
============  ==================  ==============================================
白银压境       ``elite``           算出「专攻目标」：敌方优先打最脆弱的那个
灰潮涨落       ``attrition``       算出增援波：什么时候来、来几只、什么怪
冬王亲临       ``legendary``       标出首领：每轮可以多一次行动（不占动作）
世界律失效     ``rulebreak``       改写场地：地形、掩体、天气代价
============  ==================  ==============================================

**为什么由程序算而不是交给模型**：这些全是可计算的部分——「谁最脆弱」
可以按 AC 与生命排序，「增援来几只」可以按预算除，「首领是哪一只」
可以按 CR 挑。程序算得出来就绝不该交给模型：交给模型它就会写成
「敌人显得格外凶险」这种没有结算的口头禅。模型只该负责把这些规则
**讲出来**（见 :meth:`ThreatProfile.dm_lines`）。

**四档是叠加的**，不是互斥的：第 13 章同时有 elite / attrition / legendary 三档生效。
早先的 :func:`~.encounter_budget.active_tier` 只返回最高的一档，
结果前两档被静默丢弃——压力凭空少了两层。现在用 :func:`~.encounter_budget.active_tiers` 取全部。

这一模块只做算术与规则判定，**不碰存档、不调模型**。
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .encounter_budget import DEFAULT_TIERS, ThreatTier, active_tiers

__all__ = [
    "ARENA_RULES",
    "ArenaRule",
    "ThreatProfile",
    "apply_arena_terrain",
    "arena_rule_for",
    "profile_for",
    "reinforcement_plan",
    "target_focus",
]


# --------------------------------------------------------------------------- #
# 场地规则（rulebreak 档）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ArenaRule:
    """一条场地规则：世界律失效时，战场变成什么样。

    :param key: 机器可读标签
    :param name: 规则名（给 DM 看）
    :param desc: 规则说明（给 DM 看，要能直接讲出来）
    :param terrain: 要铺进战场的地形键（``""`` 表示不改地形）
    :param cover_delta: 掩体加值的增减（负数 = 掩体变差）
    :param weather: 天气代价的一句话（每场战斗开头由 DM 叙述）
    :param sight_penalty: 视线受阻时的额外说明（``""`` = 不影响视线）
    """

    key: str
    name: str
    desc: str
    terrain: str = ""
    cover_delta: int = 0
    weather: str = ""
    sight_penalty: str = ""


#: 四条场地规则，按章号轮换。第 17 章之后每章都可能换一种——
#: 同一套规则连着用几章会腻，轮换比堆叠好。
ARENA_RULES: tuple[ArenaRule, ...] = (
    ArenaRule(
        "flood",
        "涨潮",
        "海水漫进低处：靠下的一半战场变成浅水，移动变慢",
        terrain="water",
        cover_delta=-1,
        weather="潮水在涨，靴子泡在水里，每一步都更沉",
        sight_penalty="水汽让远处的人看不太清",
    ),
    ArenaRule(
        "blizzard",
        "落雪",
        "风雪掩埋道路：开阔地变成困难地形，且遮断了远处视线",
        terrain="difficult",
        cover_delta=0,
        weather="雪子横着抽在脸上，呼出的气都结成霜",
        sight_penalty="风雪遮住了视线",
    ),
    ArenaRule(
        "lamplight",
        "灯火限制",
        "城中不许明火：掩体失效了一半，黑暗里谁也看不清谁",
        terrain="",
        cover_delta=-2,
        weather="四下里没有一点火光，只有铁器偶尔磕到石头",
    ),
    ArenaRule(
        "collapse",
        "地面塌陷",
        "年久失修的地基裂开：地面变得难以通行，掩体也塌了",
        terrain="difficult",
        cover_delta=-1,
        weather="脚下不时传来空响，像有什么东西在下面移动",
    ),
)


def arena_rule_for(
    event_no: int, rules: Iterable[ArenaRule] = ARENA_RULES
) -> ArenaRule | None:
    """按章号取场地规则（轮换）。没有 rulebreak 档生效时不该调用本函数。"""
    items = list(rules)
    if not items:
        return None
    return items[(max(1, int(event_no)) - 1) % len(items)]


# --------------------------------------------------------------------------- #
# 威胁剖面
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ThreatProfile:
    """某一章生效的全部威胁层级 + 由此算出的具体参数。

    这是「程序算、模型讲」的中枢：程序在这里把规则**算定**，
    DM 拿着 :meth:`dm_lines` 把它讲成场面。
    """

    #: 章号
    event_no: int = 1
    #: 生效的层级（累积，按生效章号升序）
    tiers: list[ThreatTier] = field(default_factory=list)
    #: 专攻目标：敌方优先攻击的玩家的 cid（elite 档）
    focus_cid: str = ""
    #: 首领 cid：享有传奇行动的那只敌人（legendary 档）
    boss_cid: str = ""
    #: 增援计划：来几只、什么怪（attrition 档）
    reinforcement: dict[str, Any] = field(default_factory=dict)
    #: 场地规则（rulebreak 档）
    arena: ArenaRule | None = None

    @property
    def tags(self) -> list[str]:
        """生效档位的标签列表。"""
        return [tier.tag for tier in self.tiers if tier.tag]

    def has(self, tag: str) -> bool:
        """某一档是否生效。"""
        return tag in self.tags

    @property
    def active(self) -> bool:
        """有没有任何威胁层级生效。"""
        return bool(self.tiers)

    def dm_lines(self) -> list[str]:
        """给 DM 看的规则行（一条档位一行）。

        这些行是**规则**，不是台词——DM 要照着结算，不是照着渲染气氛。
        """
        lines: list[str] = []
        for tier in self.tiers:
            if tier.tag == "elite":
                lines.append(
                    f"【{tier.name}】敌方专攻：优先攻击看起来最脆弱的冒险者"
                    + (f"（当前专攻目标：{self.focus_cid}）" if self.focus_cid else "")
                )
            elif tier.tag == "attrition":
                count = int(self.reinforcement.get("count", 0) or 0)
                lines.append(
                    f"【{tier.name}】每场战斗结束前追加一波增援"
                    + (
                        f"（本次：{count} 只 {self.reinforcement.get('label', '杂兵')}）"
                        if count
                        else ""
                    )
                )
            elif tier.tag == "legendary":
                lines.append(
                    f"【{tier.name}】首领具传奇行动：每个自己的回合额外一次行动，不占动作"
                    + (f"（首领：{self.boss_cid}）" if self.boss_cid else "")
                )
            elif tier.tag == "rulebreak":
                if self.arena is not None:
                    lines.append(f"【{tier.name}】{self.arena.name}：{self.arena.desc}")
            else:
                lines.append(f"【{tier.name}】{tier.mod}")
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event_no,
            "tiers": [tier.name for tier in self.tiers],
            "tags": self.tags,
            "focus_cid": self.focus_cid,
            "boss_cid": self.boss_cid,
            "reinforcement": dict(self.reinforcement),
            "arena": self.arena.key if self.arena is not None else "",
        }


# --------------------------------------------------------------------------- #
# 各档的具体算法
# --------------------------------------------------------------------------- #


def target_focus(combatants: Iterable[Any]) -> str:
    """挑出「看起来最脆弱」的参战者（elite 档的专攻目标）。

    口径：**AC 低优先，其次生命值低**。AC 是「看起来」的防线，
    生命值是「实际」的防线；敌人先挑看起来好欺负的那个。

    :param combatants: 参战者序列（需要有 ``cid`` / ``ac`` / ``hp`` 与
        ``kind`` 属性）。
    :return: 目标 cid；一个都没有时返回空串。
    """
    best: Any = None
    best_key: tuple[int, int, str] | None = None
    for combatant in combatants or []:
        cid = str(getattr(combatant, "cid", "") or "")
        if not cid:
            continue
        if bool(getattr(combatant, "out", False)):
            continue
        key = (
            int(getattr(combatant, "ac", 10) or 10),
            int(getattr(combatant, "hp", 0) or 0),
            cid,
        )
        if best_key is None or key < best_key:
            best, best_key = combatant, key
    return str(getattr(best, "cid", "") or "") if best is not None else ""


def pick_boss(combatants: Iterable[Any], monster_lookup: Any = None) -> str:
    """挑出享受传奇行动的首领（legendary 档）。

    口径：**CR 最高的活着的敌人**；没有怪物数据时退化为第一个敌人。
    传奇行动是首领的特权，给杂兵没有意义。

    :param monster_lookup: 取怪物数据的函数（形如 ``get_monster(key) -> Monster | None``）。
    """
    best_cid = ""
    best_cr = -1.0
    fallback = ""
    for combatant in combatants or []:
        if str(getattr(combatant, "kind", "")) != "enemy":
            continue
        if bool(getattr(combatant, "out", False)):
            continue
        cid = str(getattr(combatant, "cid", "") or "")
        if not cid:
            continue
        if not fallback:
            fallback = cid
        cr = 0.0
        if monster_lookup is not None:
            monster = monster_lookup(str(getattr(combatant, "monster", "") or ""))
            if monster is not None:
                cr = float(getattr(monster, "cr", 0.0) or 0.0)
        if cr > best_cr:
            best_cid, best_cr = cid, cr
    return best_cid or fallback


def reinforcement_plan(
    *,
    event_no: int,
    level: int,
    party_size: int = 4,
    count: int = 2,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """算出增援波的规模与成分（attrition 档）。

    口径（**打折**的杂兵，不是新的主力）：

    - 目标 CR = 队伍平均等级 **−2**（增援是拖时间的东西，不是新的威胁）；
    - 数量 = ``count``（默认 2），队伍越多人越多，但有上限；
    - 标签写进 ``label``，方便 DM 叙述（「一队手持鱼叉的码头打手」）。

    :return: ``{"count", "target_cr", "label", "cr_text"}``；不启用时 ``count`` 为 0。
    """
    rng = rng or random.Random()
    size = max(1, int(party_size))
    wave = max(1, min(4, int(count) + (1 if size >= 5 else 0)))
    target_cr = max(0.5, float(level) - 2.0)
    labels = (
        "闻讯赶来的杂兵",
        "从暗处涌出的打手",
        "增援的私兵",
        "被裹挟来的帮众",
    )
    label = labels[rng.randrange(len(labels))]
    return {
        "count": wave,
        "target_cr": target_cr,
        "label": label,
        "cr_text": _cr_text(target_cr),
    }


def _cr_text(cr: float) -> str:
    """CR 的习惯写法（0.5 → ``1/2``）。"""
    from .encounter import _cr_text as cr_text

    return cr_text(cr)


def profile_for(
    *,
    event_no: int,
    level: int = 1,
    party_size: int = 4,
    combatants: Iterable[Any] = (),
    enemies: Iterable[Any] = (),
    monster_lookup: Any = None,
    tiers: Iterable[tuple[int, ThreatTier]] = DEFAULT_TIERS,
    reinforcement_count: int = 2,
    rng: random.Random | None = None,
) -> ThreatProfile:
    """算出这一章的威胁剖面。

    :param event_no: 章号（决定哪些档生效）。
    :param level: 队伍平均等级（增援的 CR 打折基准）。
    :param party_size: 队伍人数（影响增援数量）。
    :param combatants: 全部参战者（算专攻目标用）。
    :param enemies: 敌人（算首领用）。
    :param monster_lookup: 取怪物数据的函数（算首领 CR 用）。
    :param reinforcement_count: 增援波的基础数量。

    **没有敌人时也算得出来**：专攻目标与首领会在战斗真正开始时补算
    （见 :func:`~.combat.apply_threat_profile`），这里的入参只是提前量。
    """
    event_no = max(1, int(event_no))
    profile = ThreatProfile(event_no=event_no, tiers=active_tiers(event_no, tiers))
    if not profile.tiers:
        return profile

    if profile.has("elite"):
        profile.focus_cid = target_focus(combatants)
    if profile.has("legendary"):
        profile.boss_cid = pick_boss(enemies, monster_lookup)
    if profile.has("attrition"):
        profile.reinforcement = reinforcement_plan(
            event_no=event_no,
            level=level,
            party_size=party_size,
            count=reinforcement_count,
            rng=rng,
        )
    if profile.has("rulebreak"):
        profile.arena = arena_rule_for(event_no)
    return profile


# --------------------------------------------------------------------------- #
# 场地规则落到战场上
# --------------------------------------------------------------------------- #


def apply_arena_terrain(
    tiles: dict[str, str],
    width: int,
    height: int,
    rule: ArenaRule,
    *,
    occupied: Iterable[tuple[int, int]] = (),
    rng: random.Random | None = None,
) -> int:
    """把场地规则铺进战场格子，返回改了多少格。

    **只改空地**：站人的格子不能变成水（会把人埋在水里），
    障碍与掩体也不动（规则改变的是环境，不是建筑）。

    铺法：随机挑一部分空地（按规则定的比例）铺成规则地形。
    比例刻意不高（约三成）——整张图都变成水会让战斗没法走。

    :param tiles: 战场格子字典（``"x,y" -> 地形键``），就地修改。
    :param occupied: 有人站着的格子（不动它们）。
    :return: 实际改写的格子数。
    """
    if rule is None or not rule.terrain:
        return 0
    rng = rng or random.Random()
    taken = {(int(x), int(y)) for x, y in occupied}
    candidates = [
        (x, y)
        for y in range(int(height))
        for x in range(int(width))
        if (x, y) not in taken
        and tiles.get(f"{x},{y}", "floor") in ("floor", "difficult")
    ]
    if not candidates:
        return 0
    rng.shuffle(candidates)
    count = max(1, round(len(candidates) * 0.3))
    changed = 0
    for x, y in candidates[:count]:
        tiles[f"{x},{y}"] = rule.terrain
        changed += 1
    return changed
