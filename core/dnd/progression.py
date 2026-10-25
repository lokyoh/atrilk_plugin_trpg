"""经验值、挑战等级经验与升级。

对应三个问题里的「战斗结束后是否有经验奖励」与「玩家是否可以升级」：

- **挑战等级 → 经验值**（:data:`CR_XP`）：DND 5e 的标准表。一只 CR2 的食人魔值 450 XP。
- **等级 → 经验阈值**（:data:`LEVEL_XP_THRESHOLDS`）：升到第 N 级所需的累计经验。
- **升级结算**（:func:`level_up`）：等级 +1、生命上限随等级自动重算、熟练加值自动重算、
  法术位按新等级自动放开；**属性提升（ASI）与职业特性交给 DM 裁决**，这里只提示。
  属性提升按 `asi_levels_for()`（通用 4/8/12/16，战士多 6/14、游荡者多 10）；
  **19 级给的是传奇恩惠**，不是属性提升（2024 口径）。

设计约定：

- 经验值累计存在角色卡的 ``exp`` 字段上（见 :class:`~.card.DndCharacter`）。
- 5e 规则「一只怪物的经验由全队共享」：结算时先累加被击败怪物的经验，再**除以参战人数**
  平分给每位玩家（见 :mod:`.settlement`）。
- 本模块只做规则与算术，**不碰存档**（升级的具体落盘由调用方负责）。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ASI_LEVELS",
    "CLASS_ASI_LEVELS",
    "CR_XP",
    "EPIC_BOON_LEVEL",
    "LEVEL_XP_THRESHOLDS",
    "MAX_LEVEL",
    "asi_levels_for",
    "can_level_up",
    "level_for_xp",
    "level_up",
    "next_level_threshold",
    "xp_for_cr",
    "xp_progress_text",
    "xp_to_next_level",
]


#: 等级上限（DND 5e 为 20）
MAX_LEVEL = 20

#: 升到第 N 级所需的**累计**经验值；下标 0 = 1 级（0 XP）
LEVEL_XP_THRESHOLDS: tuple[int, ...] = (
    0,  # 1
    300,  # 2
    900,  # 3
    2_700,  # 4
    6_500,  # 5
    14_000,  # 6
    23_000,  # 7
    34_000,  # 8
    48_000,  # 9
    64_000,  # 10
    85_000,  # 11
    100_000,  # 12
    120_000,  # 13
    140_000,  # 14
    165_000,  # 15
    195_000,  # 16
    225_000,  # 17
    265_000,  # 18
    305_000,  # 19
    355_000,  # 20
)

#: 挑战等级 → 该等级怪物被击败后给出的经验值（DND 5e 标准表）
CR_XP: dict[float, int] = {
    0: 10,
    0.125: 25,
    0.25: 50,
    0.5: 100,
    1: 200,
    2: 450,
    3: 700,
    4: 1_100,
    5: 1_800,
    6: 2_300,
    7: 2_900,
    8: 3_900,
    9: 5_000,
    10: 5_900,
    11: 7_200,
    12: 8_400,
    13: 10_000,
    14: 11_500,
    15: 13_000,
    16: 15_000,
    17: 18_000,
    18: 20_000,
    19: 22_000,
    20: 25_000,
}

#: 通用的**属性提升**等级（PHB 2024：4 / 8 / 12 / 16，每次 +2 点或换一项专长）。
#: ⚠️ 19 级**不在**这里 —— 2024 的 19 级给的是「传奇恩惠」，不是 +2 属性。
ASI_LEVELS: tuple[int, ...] = (4, 8, 12, 16)

#: 额外多给属性提升的职业（第三章各职业特性表）：战士 6 / 14、游荡者 10。
CLASS_ASI_LEVELS: dict[str, tuple[int, ...]] = {
    "fighter": (4, 6, 8, 12, 14, 16),
    "rogue": (4, 8, 10, 12, 16),
}

#: 传奇恩惠等级：到 19 级拿的是一项传奇恩惠（第五章），不是属性提升。
EPIC_BOON_LEVEL = 19


def asi_levels_for(profession: str | None) -> tuple[int, ...]:
    """某职业的属性提升等级；不认识的职业 / 无职业按通用表。"""
    return CLASS_ASI_LEVELS.get(str(profession or "").strip(), ASI_LEVELS)


def xp_for_cr(cr: float) -> int:
    """一只挑战等级为 ``cr`` 的怪物给出的经验值。

    表中没有的 CR 取**不超过**它的最大档（如 CR 2.5 按 CR 2 算，CR 0.1 按 CR 0 算）。
    """
    value = float(cr)
    if value <= 0:
        return CR_XP[0]
    best = 0.0
    for key in CR_XP:
        if key <= value and key > best:
            best = key
    return CR_XP[best]


def next_level_threshold(level: int) -> int | None:
    """升到下一级所需的累计经验；已满级返回 ``None``。"""
    level = max(1, min(int(level), MAX_LEVEL))
    if level >= MAX_LEVEL:
        return None
    return LEVEL_XP_THRESHOLDS[level]


def level_for_xp(xp: int) -> int:
    """给定累计经验能到的等级（封顶 20）。"""
    value = max(0, int(xp))
    level = 1
    for index, threshold in enumerate(LEVEL_XP_THRESHOLDS, start=1):
        if value >= threshold:
            level = index
        else:
            break
    return min(level, MAX_LEVEL)


def xp_to_next_level(card: Any) -> int | None:
    """距离下一级还差多少经验；已满级返回 ``None``。"""
    threshold = next_level_threshold(int(getattr(card, "level", 1) or 1))
    if threshold is None:
        return None
    return max(0, threshold - int(getattr(card, "exp", 0) or 0))


def can_level_up(card: Any) -> bool:
    """这张卡现在能不能升级（经验够了、且没满级、且没在死亡豁免里）。"""
    level = int(getattr(card, "level", 1) or 1)
    if level >= MAX_LEVEL:
        return False
    if bool(getattr(card, "is_dead", False)):
        return False
    threshold = next_level_threshold(level)
    if threshold is None:
        return False
    return int(getattr(card, "exp", 0) or 0) >= threshold


def level_up(card: Any) -> str:
    """把角色卡升一级并返回给 DM 看的说明文本。

    规则要点：

    - 等级 +1；**生命上限**由 :attr:`DndCharacter.max_hp` 按新等级自动重算
      （1 级取生命骰最大值，之后每级取生命骰平均值 + 体质调整值）；
      **当前生命值不变**（升级不回血）。
    - 熟练加值、法术位上限都按等级自动重算，无需额外处理。
    - **属性提升（ASI）**按职业给等级（战士 6 / 14、游荡者 10 额外有一次）；
      **19 级是传奇恩惠**，两者都只在提示里点出来，具体选择由 DM 裁决。
    """
    level = int(getattr(card, "level", 1) or 1)
    if level >= MAX_LEVEL:
        return (
            f"{getattr(card, 'name', '') or '角色'} 已经是 {MAX_LEVEL} 级，无法再升级。"
        )
    threshold = next_level_threshold(level)
    if threshold is None:
        return "已达等级上限。"
    if int(getattr(card, "exp", 0) or 0) < threshold:
        need = threshold - int(getattr(card, "exp", 0) or 0)
        return f"经验不足，还差 {need} XP 才能升到 {level + 1} 级。"

    old_max = int(getattr(card, "max_hp", 0) or 0)
    card.level = level + 1
    new_max = int(getattr(card, "max_hp", 0) or 0)
    # 当前生命值不因升级而增加（5e 规则：升级不回血）；未记录时视为满血。
    lines = [
        f"【{getattr(card, 'name', '') or '角色'}】升级！Lv.{level} → Lv.{card.level}",
        f"生命上限 {old_max} → {new_max}（当前生命不变）",
        f"熟练加值 +{getattr(card, 'proficiency', 0)}",
    ]
    if card.level in asi_levels_for(getattr(card, "profession", "")):
        lines.append(
            f"Lv.{card.level} 可以选择属性提升（+2 点属性，或换成一项专长）——"
            "请 DM 询问玩家的选择后再用 update_character 落盘。"
        )
    elif card.level == EPIC_BOON_LEVEL:
        lines.append(
            f"Lv.{EPIC_BOON_LEVEL} 获得一项**传奇恩惠**（Epic Boon，见第五章）——"
            "**不是 +2 属性**；请 DM 与玩家一起挑一项，再用 update_character 的"
            "「特性」落盘。"
        )
    lines.append(
        "职业技能特性 / 新法术按职业表解锁；若该职业在此等级有新特性，请 DM 用 "
        "update_character 的「特性」补上。"
    )
    return "\n".join(lines)


def xp_progress_text(card: Any) -> str:
    """角色卡的经验进度文本（展示用，如「经验 1200/2700」）。"""
    level = int(getattr(card, "level", 1) or 1)
    exp = int(getattr(card, "exp", 0) or 0)
    threshold = next_level_threshold(level)
    if threshold is None:
        return f"经验 {exp}（已达 {MAX_LEVEL} 级上限）"
    return f"经验 {exp}/{threshold}"
