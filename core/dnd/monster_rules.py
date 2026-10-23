"""怪物特性的**机械部分**：从数据卡的 ``rules`` 块读出能自动结算的规则。

为什么单独一个模块
------------------

数据卡里那段「特性」正文是**写给人看的中文**，程序判不动：

    巨魔在其回合开始时恢复 15 生命值。若巨魔受到强酸或火焰伤害，则该特质
    在其下个回合开始时无法生效。

可这些规则在结算里必须生效——「再生」漏一轮，巨魔就白掉 15 血；「魔法抗性」
忘了给优势，火球术就比书上强一截。从前全靠 DM（模型）记着，而模型记不住
五百多只怪各自的脾气。

所以生成脚本（``scripts/build_monsters_from_5etools.py``）在**转表时**就把
这类纯机械的特性挑出来，按 5etools 的英文原名（``Regeneration`` / ``Magic
Resistance`` / ``Pack Tactics``……）写成数据卡里的 ``rules`` 块。本模块只负责
把它读成强类型，并提供**纯判定**——棋盘、战斗状态那些上下文由调用方（:mod:`.combat`）
传进来，这里不 import 战斗模块。

哪些规则在这儿，哪些不在
------------------------

判据只有一条：**程序能自己算出结果**。

- 在这儿：状态免疫、魔法抗性、传奇抗性、集群战术、日照敏感、再生、不死坚韧、
  自爆、虚体 / 无定形 / 集群的移动、攻城怪物、魔法武器、血腥狂怒、移动方式。
- 不在这儿：水下呼吸、屏息、飞掠、蛛行术、恐怖降临、各类吐息与施法。它们要么
  依赖环境（水下了没有？）要么依赖没有建模的机制（借机攻击、物件伤害），要么
  是 DM 该自己叙述的——写成字段也只会变成一条永远没人读的死数据。

本模块**不认文本**：看到的就是生成脚本写进去的结构。加一条规则要动两处——
脚本里认特性、这里加结算分支——两边缺一都不算实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .attributes import get_ability
from .conditions import Advantage, combine_advantage

__all__ = [
    "DeathBurst",
    "MonsterRules",
    "attack_advantage_of",
    "blocked_conditions",
    "rules_of",
    "rules_text",
    "save_advantage",
]


@dataclass(frozen=True, slots=True)
class DeathBurst:
    """自爆（``死亡时爆炸``）的可结算参数。

    ``damage`` 为空表示失败不是掉血、而是陷入某种状态（泥魔蝠 / 烟魔蝠）——
    那种只能提示 DM 按正文叙述，程序不猜状态名。
    """

    save: str = "dex"  #: 豁免属性（内部键：``dex`` / ``con``）
    dc: int = 10
    radius: int = 5  #: 波及半径（尺）
    damage: str = ""  #: 伤害表达式（``"2d4"``）；空 = 不掉血
    damage_type: str = ""

    @property
    def has_damage(self) -> bool:
        return bool(self.damage)

    @property
    def save_cn(self) -> str:
        """豁免属性的中文名（展示用）。"""
        return get_ability(self.save).cn

    def text(self) -> str:
        head = f"{self.radius} 尺内每名生物 {self.save_cn}豁免 DC {self.dc}"
        return (
            f"死亡时爆炸（{head}，失败 {self.damage} {self.damage_type}伤害，成功半伤）"
            if self.has_damage
            else f"死亡时爆炸（{head}，失败按特性正文结算）"
        )


@dataclass(frozen=True, slots=True)
class MonsterRules:
    """一只怪**能自动结算**的规则（读自数据卡的 ``rules`` 块）。"""

    #: 免疫的状态（魅惑 / 中毒……）：想给它加这些状态时直接挡掉
    condition_immunities: tuple[str, ...] = ()
    #: 对抗法术与其他魔法效应的豁免有优势
    magic_resistance: bool = False
    #: 传奇抗性：豁免失败可以改成成功，每日几次（0 = 没有）
    legendary_resistance: int = 0
    #: 集群战术：目标 5 尺内有未失能的同类盟友时攻击有优势
    pack_tactics: bool = False
    #: 日照敏感：身处日光下时攻击检定与察觉检定有劣势
    sunlight_sensitivity: bool = False
    #: 再生：自己回合开始时回复几点（0 = 没有）
    regeneration: int = 0
    #: 再生被打断的伤害类型（火焰 / 强酸）：挨了这类伤害，下个回合不生效
    regeneration_blocked_by: tuple[str, ...] = ()
    #: 不死坚韧：被打到 0 血时掷体质豁免（DC 5 + 伤害），过了就留 1 血
    undead_fortitude: bool = False
    #: 自爆；``None`` = 没有
    death_burst: DeathBurst | None = None
    #: 虚体移动：能穿过物件与生物，移动不受阻挡
    incorporeal: bool = False
    #: 无定形：能挤过窄缝，同样不受阻挡
    amorphous: bool = False
    #: 集群：可以进驻别的生物所在的空间（也能被穿过）
    swarm: bool = False
    #: 武器攻击视为魔法（打得动「抗非魔法武器」的怪）
    magic_weapons: bool = False
    #: 血腥狂怒：对生命值未满的目标攻击有优势
    blood_frenzy: bool = False

    @property
    def passes_through(self) -> bool:
        """移动时是否无视阻挡（虚体 / 无定形 / 集群）。"""
        return self.incorporeal or self.amorphous or self.swarm

    @property
    def any(self) -> bool:
        """有没有任何一条会自动生效的规则（没有就不用费劲展示）。"""
        return bool(
            self.condition_immunities
            or self.magic_resistance
            or self.legendary_resistance
            or self.pack_tactics
            or self.sunlight_sensitivity
            or self.regeneration
            or self.undead_fortitude
            or (self.death_burst is not None)
            or self.incorporeal
            or self.amorphous
            or self.swarm
            or self.magic_weapons
            or self.blood_frenzy
        )


def _texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(item) for item in (value or ()) if str(item).strip())


def rules_from(raw: Mapping[str, Any]) -> MonsterRules:
    """``rules`` 块（YAML 里的 dict）→ 强类型。字段名与生成脚本一一对应。"""
    burst_raw = raw.get("death_burst")
    burst = None
    if isinstance(burst_raw, Mapping):
        burst = DeathBurst(
            save=str(burst_raw.get("save", "dex")),
            dc=int(burst_raw.get("dc", 10) or 10),
            radius=int(burst_raw.get("radius", 5) or 5),
            damage=str(burst_raw.get("damage", "")),
            damage_type=str(burst_raw.get("damage_type", "")),
        )
    regen = raw.get("regeneration")
    regen = regen if isinstance(regen, Mapping) else {}
    return MonsterRules(
        condition_immunities=_texts(raw.get("condition_immunities")),
        magic_resistance=bool(raw.get("magic_resistance")),
        legendary_resistance=int(raw.get("legendary_resistance", 0) or 0),
        pack_tactics=bool(raw.get("pack_tactics")),
        sunlight_sensitivity=bool(raw.get("sunlight_sensitivity")),
        regeneration=int(regen.get("amount", 0) or 0),
        regeneration_blocked_by=_texts(regen.get("blocked_by")),
        undead_fortitude=bool(raw.get("undead_fortitude")),
        death_burst=burst,
        incorporeal=bool(raw.get("incorporeal")),
        amorphous=bool(raw.get("amorphous")),
        swarm=bool(raw.get("swarm")),
        magic_weapons=bool(raw.get("magic_weapons")),
        blood_frenzy=bool(raw.get("blood_frenzy")),
    )


#: 按怪物 key 缓存：整张表是只读的，解析一次就够（一场战斗会查几十次）
_CACHE: dict[str, MonsterRules] = {}


def rules_of(monster: Any) -> MonsterRules:
    """取一只怪的规则；没数据卡 / 没 rules 块时返回**全空**（即「没有规则」）。

    :param monster: :class:`~.monster_types.Monster` 或 ``None``。
    """
    if monster is None:
        return MonsterRules()
    key = str(getattr(monster, "key", "") or "")
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    raw = getattr(monster, "rules", None)
    parsed = rules_from(raw if isinstance(raw, Mapping) else {})
    if key:
        _CACHE[key] = parsed
    return parsed


# --------------------------------------------------------------------------- #
# 判定（纯函数：上下文由调用方给，这里不碰棋盘 / 存档）
# --------------------------------------------------------------------------- #


def save_advantage(rules: MonsterRules, *, magical: bool = False) -> Advantage:
    """这只怪做豁免时的姿态。

    **魔法抗性**只对法术与其他魔法效应生效（火球术、妖火、石化术……）；
    龙息、毒气、掉石头这类非魔法的豁免不吃这条优势——书上写的就是
    「对抗法术和其他魔法效应时」。
    """
    if magical and rules.magic_resistance:
        return Advantage.ADVANTAGE
    return Advantage.NORMAL


def attack_advantage_of(
    rules: MonsterRules,
    *,
    allies_near: bool = False,
    target_wounded: bool = False,
    sunlight: bool = False,
) -> tuple[Advantage, str]:
    """这只怪发动攻击时的优势姿态（只管**特性**带来的，状态另算）。

    上下文（盟友在不在 5 尺内、目标有没有掉血、现在是不是大白天）由调用方
    按棋盘与场景判定后传进来——本模块不看棋盘。

    :return: ``(优势姿态, 原因文本)``；没有特性参与时原因为空串。
    """
    advantage = Advantage.NORMAL
    reasons: list[str] = []
    if rules.pack_tactics and allies_near:
        advantage = combine_advantage(advantage, Advantage.ADVANTAGE)
        reasons.append("集群战术：目标 5 尺内有未失能的同类")
    if rules.blood_frenzy and target_wounded:
        advantage = combine_advantage(advantage, Advantage.ADVANTAGE)
        reasons.append("血腥狂怒：目标已经掉血")
    if rules.sunlight_sensitivity and sunlight:
        advantage = combine_advantage(advantage, Advantage.DISADVANTAGE)
        reasons.append("日照敏感：它正暴露在日光下")
    return advantage, "；".join(reasons)


def blocked_conditions(rules: MonsterRules, names: Any) -> tuple[str, ...]:
    """这些状态里，哪些是它**免疫**的（施加时要挡掉）。"""
    wanted = [str(item).strip() for item in _texts(names)]
    immune = set(rules.condition_immunities)
    return tuple(name for name in wanted if name in immune)


def rules_text(rules: MonsterRules) -> str:
    """给 DM 看的一行：这只怪**哪些规则会自动生效**。

    结算文案里也会写（"集群战术：……"），这里用于开战列敌与查卡时提醒。
    """
    if not rules.any:
        return ""
    parts: list[str] = []
    if rules.condition_immunities:
        parts.append("状态免疫：" + "、".join(rules.condition_immunities))
    if rules.magic_resistance:
        parts.append("魔法抗性（法术豁免优势）")
    if rules.legendary_resistance:
        parts.append(f"传奇抗性 {rules.legendary_resistance}/日")
    if rules.pack_tactics:
        parts.append("集群战术")
    if rules.sunlight_sensitivity:
        parts.append("日照敏感")
    if rules.regeneration:
        blocked = "、".join(rules.regeneration_blocked_by)
        parts.append(
            f"再生 {rules.regeneration}" + (f"（被{blocked}打断）" if blocked else "")
        )
    if rules.undead_fortitude:
        parts.append("不死坚韧")
    if rules.death_burst is not None:
        parts.append(rules.death_burst.text())
    if rules.incorporeal:
        parts.append("虚体移动（不受阻挡）")
    elif rules.amorphous:
        parts.append("无定形（不受阻挡）")
    elif rules.swarm:
        parts.append("集群（可与他人同格）")
    if rules.magic_weapons:
        parts.append("武器攻击视为魔法")
    if rules.blood_frenzy:
        parts.append("血腥狂怒")
    return "；".join(parts)
