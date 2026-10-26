"""子职特性的**结算标记**：暴击阈值、卓越骰、狂怒。

48 个子职、190 多条特性，绝大多数是「读一遍照做」的文本（DM 裁定即可）。
但有三种东西不记账就会算错，而且错得很隐蔽：

- **暴击阈值**（勇士「精通重击」19、「高效重击」18）—— 攻击结算默认只在天然 20
  算暴击，忘了这条，勇士整场少掉一层伤害，DM 根本不会察觉；
- **卓越骰**（战斗大师）—— 4 个 d8，短休回满，用了要扣。这是战斗大师的全部
  主动资源，手算等于让玩家自己记着「我用了几个」；
- **狂怒**（狂战士）—— 狂暴中鲁莽攻击首次命中额外 ``狂暴伤害`` 个 d6，
  每回合只能触发一次，得有个地方记「这回合用过了」。

所以数据里给这几条特性打了标记（``res/dnd/subclasses.yaml`` 的 ``crit_at`` /
``superiority_die`` / ``raging_dice``），这里统一读出来，供
:mod:`.card` 与 :mod:`.combat` 调用。**其余特性一律不在这里做**——
子职能力差异太大，统一结算会算出大量例外。
"""

from __future__ import annotations

from typing import Any

from .subclass import SubclassFeature, resolve_subclass

__all__ = [
    "DEFAULT_CRIT_AT",
    "crit_threshold",
    "features_of",
    "has_raging_dice",
    "subclass_spendable",
    "superiority",
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
    """是否有「狂怒」（狂战士 3 级：狂暴中鲁莽攻击首次命中加骰）。"""
    return any(bool(feat.raging_dice) for feat in features_of(card))


def subclass_spendable(card: Any) -> dict[str, int]:
    """子职给的可消耗资源上限（并进 :meth:`DndCharacter.spendable_resources`）。"""
    out: dict[str, int] = {}
    count, _die = superiority(card)
    if count > 0:
        out["superiority"] = count
    return out
