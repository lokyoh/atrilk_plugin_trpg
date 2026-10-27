"""子职特性的**结算标记**：暴击阈值、卓越骰、每回合首次命中的额外伤害、治疗加成。

48 个子职、190 多条特性，绝大多数是「读一遍照做」的文本（DM 裁定即可）。
但有四类东西不记账就会算错，而且错得很隐蔽：

- **暴击阈值**（勇士「精通重击」19、「高效重击」18）—— 攻击结算默认只在天然 20
  算暴击，忘了这条，勇士整场少掉一层伤害，DM 根本不会察觉；
- **卓越骰**（战斗大师）—— 4 个 d8 起、随等级长到 6d12，短休回满，用了要扣。
  这是战斗大师的全部主动资源，手算等于让玩家自己记着「我用了几个」；
- **每回合首次命中的额外伤害**（狂战士「狂怒」、狂热者「神性之怒」、
  妖精漫游者「哀惧灵袭」）—— 三条是同一套机制，只是骰式与前置条件不同；
- **治疗加成**（生命领域「生命门徒 / 神佑医者 / 极效治疗」）—— 都是纯数字，
  且「+2 + 环阶」「骰子取满」这类东西 DM 念的时候最容易漏。

所以数据里给这几条特性打了标记（``res/dnd/subclasses.yaml`` 的 ``crit_at`` /
``first_hit`` / ``heal_*``，以及子职层的 ``superiority`` 成长表），这里统一读出来，
供 :mod:`.card`、:mod:`.combat` 与施法结算调用。**其余特性一律不在这里做** ——
子职能力差异太大，统一结算会算出大量例外。
"""

from __future__ import annotations

from typing import Any

from .subclass import FirstHit, SubclassFeature, resolve_subclass

__all__ = [
    "DEFAULT_CRIT_AT",
    "bloodied_regen",
    "crit_threshold",
    "death_save_marks",
    "features_of",
    "first_hit",
    "first_hit_dice",
    "has_raging_dice",
    "heal_marks",
    "subclass_extra_attack",
    "subclass_resistances",
    "subclass_spendable",
    "superiority",
    "ward_granted",
]

#: 默认暴击阈值（天然 20）
DEFAULT_CRIT_AT = 20


def _subclass_of(card: Any) -> Any:
    key = str(getattr(card, "subclass", "") or "").strip()
    return resolve_subclass(key) if key else None


def features_of(card: Any) -> tuple[SubclassFeature, ...]:
    """角色卡当前**已解锁**的子职特性（没有子职 / 认不出子职 → 空元组）。"""
    sub = _subclass_of(card)
    if sub is None:
        return ()
    return sub.features_up_to(int(getattr(card, "level", 0) or 0))


def crit_threshold(card: Any) -> int:
    """这次攻击检定掷到几点即暴击（默认 20；勇士 19 / 18）。

    多个来源取**最小**值（19 与 18 同时存在时按 18 算——正是 15 级勇士的样子）。
    """
    best = DEFAULT_CRIT_AT
    for feat in features_of(card):
        value = int(feat.crit_at or 0)
        if value:
            best = min(best, value)
    return best


def superiority(card: Any) -> tuple[int, int]:
    """卓越骰 ``(个数, 骰面)``；没有这个子职特性返回 ``(0, 0)``。

    数值来自子职的成长表（:attr:`SubClass.superiority`）：战斗大师 3 级 4d8、
    7 级 5 枚、10 级 d10、15 级 6 枚、18 级 d12。
    """
    sub = _subclass_of(card)
    if sub is None:
        return 0, 0
    return sub.superiority_at(int(getattr(card, "level", 0) or 0))


def has_raging_dice(card: Any) -> bool:
    """是否有「狂怒」（狂战士 3 级：狂暴中鲁莽攻击首次命中加骰）。

    判定认的是 :class:`FirstHit` 里带 ``reckless`` 前置的那条 —— 也就是「必须
    本回合鲁莽过才给」的那一条，正是狂怒。
    """
    entry = first_hit(card)
    return bool(entry and "reckless" in entry[0].needs)


def first_hit(card: Any) -> tuple[FirstHit, SubclassFeature] | None:
    """「每回合首次命中的额外伤害」``(标记, 特性)``；没有返回 None。

    多条同时生效时取**等级最高**的那条（同一子职不会给两条，但等级覆盖靠它）。
    """
    best: tuple[int, FirstHit, SubclassFeature] | None = None
    for feat in features_of(card):
        mark = feat.first_hit
        if mark is None:
            continue
        if best is None or feat.level >= best[0]:
            best = (feat.level, mark, feat)
    return (best[1], best[2]) if best else None


def first_hit_dice(mark: FirstHit, level: int, *, rage_damage: int = 0) -> str:
    """把标记解析成骰式（``"2d6"`` / ``"1d6+2"`` / ``"1d4"``）；算不出来给空串。

    - ``dice == "rage"`` → ``狂暴伤害`` 个 d6（狂战士狂怒）；
    - ``at`` 里按等级覆盖骰面（哀惧灵袭 11 级 1d4 → 1d6）；
    - ``plus == "level/2"`` → 职业等级的一半向下取整（神性之怒）。
    """
    top = max(0, int(level or 0))
    dice = str(mark.dice or "")
    for need in sorted(mark.at, reverse=True):
        if need <= top:
            dice = mark.at[need]
            break
    if dice == "rage":
        count = max(0, int(rage_damage or 0))
        dice = f"{count}d6"
    if not dice:
        return ""
    extra = 0
    if str(mark.plus or "").strip().lower() == "level/2":
        extra = top // 2
    return f"{dice}+{extra}" if extra else dice


def subclass_extra_attack(card: Any) -> int:
    """子职给的**额外攻击**：一个攻击动作能打几次（0 = 子职没给）。

    现阶段只有勇气学院 6 级那条；它不叠加，只是把吟游诗人从 1 次抬到 2 次
    （与职业表的额外攻击取大者，见 :attr:`DndCharacter.attacks_per_action`）。
    """
    best = 0
    for feat in features_of(card):
        best = max(best, int(feat.extra_attack or 0))
    return best


def subclass_resistances(card: Any) -> tuple[str, ...]:
    """子职给的**常驻抗性**（战争领域 17 级：钝击 / 穿刺 / 挥砍）。"""
    out: list[str] = []
    for feat in features_of(card):
        for item in feat.resist:
            if item not in out:
                out.append(item)
    return tuple(out)


def death_save_marks(card: Any) -> tuple[bool, int]:
    """勇士 18 级「生还者」：``(死亡豁免有优势, 掷到几点就算天然 20)``。

    没有这个子职给 ``(False, 0)`` —— 天然 20 才是 20（默认行为不动）。
    """
    advantage = False
    threshold = 0
    for feat in features_of(card):
        advantage = advantage or bool(feat.death_save_advantage)
        threshold = max(threshold, int(feat.death_save_at or 0))
    return advantage, threshold


def bloodied_regen(card: Any) -> bool:
    """有没有「英气风发」：回合开始时若**浴血**且还有血，回 `5 + 体质`。"""
    return any(bool(feat.bloodied_regen) for feat in features_of(card))


def ward_granted(card: Any) -> bool:
    """有没有「奥术守御」结界（防护师 3 级）。"""
    return any(bool(feat.ward) for feat in features_of(card))


def heal_marks(card: Any) -> tuple[bool, bool, bool]:
    """生命领域那三条：``(每个目标 +2+环阶, 治疗他人时自己回, 治疗骰取满)``。"""
    bonus = heal_self = heal_max = False
    for feat in features_of(card):
        bonus = bonus or bool(feat.heal_bonus)
        heal_self = heal_self or bool(feat.heal_self)
        heal_max = heal_max or bool(feat.heal_max)
    return bonus, heal_self, heal_max


def subclass_spendable(card: Any) -> dict[str, int]:
    """子职给的可消耗资源上限（并进 :meth:`DndCharacter.spendable_resources`）。"""
    out: dict[str, int] = {}
    count, _die = superiority(card)
    if count > 0:
        out["superiority"] = count
    return out
