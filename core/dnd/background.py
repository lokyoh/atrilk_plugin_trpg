"""DND 5e 背景数据。

背景在建卡时提供：**三项属性的提升**（PHB 2024 起，属性加值只来自背景，种族不再给）、
**一项起源专长**、两项技能熟练、一项工具熟练、以及一段起始行装。

2024 背景**不给语言**（语言来自第二章独立的「选择语言」步），工具也**只有一项**。

**数值在 ``res/dnd/backgrounds.yaml``**，这里只留类型与查询函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .attributes import ABILITIES, get_ability
from .datafiles import as_texts, load_table

__all__ = [
    "BACKGROUNDS",
    "BACKGROUND_MAP",
    "Background",
    "background_choices",
    "get_background",
]


@dataclass(frozen=True, slots=True)
class Background:
    """一项背景。"""

    key: str
    cn: str
    en: str
    skills: tuple[str, ...]  # 提供的技能内部键
    #: PHB 2024「属性值提升」的三项属性（内部键）。玩家从中选一项 +2、另一项 +1，
    #: 或三项各 +1（合计 +3，单项不得推到 20 以上）。
    bonus: tuple[str, ...] = ()
    #: 2024 起背景固定给的一项**起源专长**（第五章 4099）。带括号的是要一并定下
    #: 子选项的专长，如「魔法学徒（牧师）」= 魔法学徒选牧师法表。
    feat: str = ""
    tools: tuple[str, ...] = ()  # 工具熟练（2024 起**只有一项**）
    equipment: tuple[str, ...] = ()  # 起始行装
    desc: str = ""

    @property
    def skill_count(self) -> int:
        return len(self.skills)

    def bonus_abilities(self) -> tuple[str, ...]:
        """三项属性按键的固定顺序（力 / 敏 / 体 / 智 / 感 / 魅）排列，供展示与校验。"""
        order = {ability.key: index for index, ability in enumerate(ABILITIES)}
        return tuple(sorted(self.bonus, key=lambda key: order.get(key, len(order))))

    @property
    def bonus_text(self) -> str:
        """形如 ``智力 / 感知 / 魅力`` 的三项属性名（空则空串）。"""
        return " / ".join(get_ability(key).cn for key in self.bonus_abilities())


def _background(row: dict[str, Any]) -> Background:
    """一项背景（``res/dnd/backgrounds.yaml`` 的一项）。"""
    return Background(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        skills=as_texts(row.get("skills")),
        bonus=as_texts(row.get("bonus")),
        feat=str(row.get("feat", "")),
        tools=as_texts(row.get("tools")),
        equipment=as_texts(row.get("equipment")),
        desc=str(row.get("desc", "")),
    )


#: 背景表：数值在 ``res/dnd/backgrounds.yaml``
BACKGROUNDS: tuple[Background, ...] = tuple(
    _background(row) for row in load_table("backgrounds")
)

BACKGROUND_MAP: dict[str, Background] = {b.key: b for b in BACKGROUNDS}


def get_background(value: Background | str) -> Background:
    """按背景对象 / 内部键 / 中文名取出背景。"""
    if isinstance(value, Background):
        return value
    text = str(value).strip()
    if text in BACKGROUND_MAP:
        return BACKGROUND_MAP[text]
    for bg in BACKGROUNDS:
        if bg.cn == text or bg.en.lower() == text.lower():
            return bg
    raise KeyError(f"未知背景：{value}")


def background_choices() -> tuple[str, ...]:
    """全部背景中文名。"""
    return tuple(b.cn for b in BACKGROUNDS)
