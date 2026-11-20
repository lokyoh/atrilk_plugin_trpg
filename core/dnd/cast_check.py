"""施法前置校验（PHB 2024 第四章「施法」节）。

这里只放**现有数据判得出来**的几条，判不出来的一律留给 DM —— 引擎没有
「哪只手拿着什么」的概念，所以：

- **言语（V）**：能判。「被堵住嘴或者身处魔法性沉默区域中的生物不能施展带
  有言语成分的法术」。引擎没有区域效果，所以「沉默」按**状态**近似（DM 给
  区域内的人挂上「沉默」），见 :func:`component_block`。
- **姿势（S）/ 材料（M）**：判不了。原文要求「至少一只手摆姿势」「一只空闲的
  手拿材料」，而引擎的装备栏只记「身上有什么」，不记「手里拿着什么」。硬判
  会把「手持盾牌 + 长剑」这类常态误杀，所以这两项**不拦**，`cast_spell` 的
  回执照旧把成分原文带给 DM。
- **法师护甲那条**（无熟练护甲不能施法）不在这里，在 :meth:`DndCharacter.can_cast`。

另外两条与成分无关、但同属「施法限制」，也归这里：

- 每回合**至多消耗一个法术位**（戏法 / 仪式不扣位，不受限）；
- 仪式施法（2024 起是**通用规则**，不再绑职业，但要求法术已准备）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

    from .spells import Spell

__all__ = [
    "VOICE_BLOCKING",
    "component_block",
    "is_ritual_timing",
    "ritual_block",
    "slot_limit_block",
]

#: 会让人发不出声的状态名。「沉默」不是标准状态而是**区域效果**，引擎没有区域
#: 概念，由 DM 给区域内的人挂这个状态来近似；「堵嘴」「禁言」是同一回事的写法。
VOICE_BLOCKING: tuple[str, ...] = ("沉默", "堵嘴", "禁言", "无声")

#: 法术 ``components`` 串里表示「要出声」的字样（数据写法：语言、姿势、材料）
_VERBAL = "语言"


def component_block(spell: Spell, conditions: Iterable[str]) -> str:
    """带**言语成分**却发不出声 → 返回拒绝原因；能施则返回空串。

    :param spell: 要施展的法术。
    :param conditions: 施法者身上的状态名（角色卡 / 参战者都行）。
    """
    if _VERBAL not in str(spell.components or ""):
        return ""
    held = {str(name).strip() for name in conditions or ()}
    for name in VOICE_BLOCKING:
        if name in held:
            return (
                f"{spell.cn} 带言语成分，而施法者处于「{name}」——"
                "发不出声就念不出咒文，无法施展。"
            )
    return ""


def slot_limit_block(spell: Spell, *, slot_spent: bool) -> str:
    """本回合已经扣过法术位，这道法术还要扣 → 返回拒绝原因。

    PHB 2024：「每个回合中，你通过施展法术的方式至多只能消耗一个法术位。」
    ⚠️ 2024 限制的是**消耗法术位**，不是施法本身——所以戏法（0 环）与仪式
    （不扣位）都不受这条约束，2014 那套「附赠施法后只能再放戏法」已作废。
    """
    if not slot_spent:
        return ""
    if int(spell.level) <= 0:
        return ""
    return (
        f"本回合已经消耗过一个法术位了（PHB 2024：每回合至多只能消耗一个法术位）。"
        f"{spell.cn} 是 {spell.level} 环、需要再扣一个位——本回合放不了；"
        "戏法不扣位，仍然可以施展。"
    )


def ritual_block(spell: Spell, prepared: Iterable[str]) -> str:
    """能否**以仪式方式**施展：法术要有仪式标签，且施法者已准备它。

    PHB 2024「无需法术位的施法 —— 仪式」：「一道法术的仪式版本的施法时间比其
    通常版本要多 10 分钟，但无需消耗法术位。要想将一道法术作为仪式施展，施法
    者必须准备了这道法术。」

    ⚠️ 2024 起仪式施法是**通用规则**，不再像 2014 那样绑「职业特性」——任何
    施法职业都能仪式施放自己准备了的仪式法术。

    ``prepared`` 为空表示这张卡没在管准备列表，按现有口径**放行**（宁可松）。
    """
    if not spell.ritual:
        return f"{spell.cn} 没有仪式标签，不能以仪式方式施展（照常耗法术位）。"
    keys = {str(item).strip() for item in prepared or () if str(item).strip()}
    if keys and spell.key not in keys:
        return (
            f"{spell.cn} 有仪式标签，但施法者没有**准备**它——"
            "PHB 2024 要求仪式施法的对象必须是已准备的法术；"
            "先用 update_character 的「准备」把它记进准备列表。"
        )
    return ""


def is_ritual_timing(spell: Spell) -> bool:
    """法术是否标了仪式标签（给回执与提示用）。"""
    return bool(spell.ritual)
