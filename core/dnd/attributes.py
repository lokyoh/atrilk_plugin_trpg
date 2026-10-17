"""DND 5e 属性（六大属性）定义与相关计算。

属性值（Score）本身不是加值，所有检定 / 豁免 / 攻击用的是**调整值**（Modifier）：

.. code-block:: text

    调整值 = (属性值 - 10) // 2

本模块只负责属性本身的定义与运算；种族 / 职业带来的加值在各自数据表里描述，
由 :class:`~.card.DndCharacter` 组装时统一结算。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

__all__ = [
    "ABILITIES",
    "ABILITY_MAP",
    "ABILITY_SCORE_MAX",
    "ABILITY_SCORE_MIN",
    "POINT_BUY_BUDGET",
    "POINT_BUY_COST",
    "STANDARD_ARRAY",
    "Ability",
    "AbilitySet",
    "ability_modifier",
    "format_ability_block",
    "get_ability",
    "proficiency_bonus",
]

ABILITY_SCORE_MIN = 3
ABILITY_SCORE_MAX = 20

#: DND 5e 标准属性数组（可任意顺序分配给六大属性）
STANDARD_ARRAY: tuple[int, ...] = (15, 14, 13, 12, 10, 8)

#: 购点法总预算与单项花费（属性值 8~15）
POINT_BUY_BUDGET = 27
POINT_BUY_COST: dict[int, int] = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}


@dataclass(frozen=True, slots=True)
class Ability:
    """一项属性。"""

    key: str  #: 内部键，如 ``"str"``
    cn: str  #: 中文名，如 ``"力量"``
    en: str  #: 英文名
    abbr: str  #: 三字母缩写，如 ``"STR"``

    def __str__(self) -> str:
        return self.cn


ABILITIES: tuple[Ability, ...] = (
    Ability("str", "力量", "Strength", "STR"),
    Ability("dex", "敏捷", "Dexterity", "DEX"),
    Ability("con", "体质", "Constitution", "CON"),
    Ability("int", "智力", "Intelligence", "INT"),
    Ability("wis", "感知", "Wisdom", "WIS"),
    Ability("cha", "魅力", "Charisma", "CHA"),
)

#: 属性键 -> 属性
ABILITY_MAP: dict[str, Ability] = {a.key: a for a in ABILITIES}


def get_ability(value: Ability | str) -> Ability:
    """按属性对象或属性键取出属性，非法键抛 :class:`KeyError`。"""
    if isinstance(value, Ability):
        return value
    try:
        return ABILITY_MAP[str(value).lower()]
    except KeyError as e:
        raise KeyError(f"未知属性：{value}") from e


def ability_modifier(score: int) -> int:
    """属性值 -> 调整值。"""
    return (int(score) - 10) // 2


def proficiency_bonus(level: int) -> int:
    """熟练加值：1~4 级 +2，5~8 +3，9~12 +4，13~16 +5，17~20 +6。"""
    level = max(1, min(20, int(level)))
    return 2 + (level - 1) // 4


class AbilitySet:
    """一组属性值的容器，负责加值叠加与调整值查询。"""

    __slots__ = ("_scores",)

    def __init__(self, scores: Mapping[Ability | str, int] | None = None):
        self._scores: dict[str, int] = {a.key: 10 for a in ABILITIES}
        if scores:
            self.update(scores)

    def __getitem__(self, item: Ability | str) -> int:
        return self._scores[get_ability(item).key]

    def __setitem__(self, item: Ability | str, value: int) -> None:
        self._scores[get_ability(item).key] = int(value)

    def __iter__(self) -> Iterator[Ability]:
        return iter(ABILITIES)

    def __len__(self) -> int:
        return len(ABILITIES)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"AbilitySet({self._scores})"

    def update(self, scores: Mapping[Ability | str, int]) -> None:
        """批量写入属性值（覆盖）。"""
        for key, value in scores.items():
            self[key] = value

    def apply_bonus(self, bonus: Mapping[Ability | str, int]) -> None:
        """叠加种族 / 专长等带来的属性加值。"""
        for key, value in bonus.items():
            ability = get_ability(key)
            self._scores[ability.key] += int(value)

    def modifier(self, item: Ability | str) -> int:
        """取某项属性的调整值。"""
        return ability_modifier(self[item])

    def as_dict(self, *, cn: bool = False) -> dict[str, int]:
        """导出为字典；``cn=True`` 时键用中文名。"""
        if cn:
            return {a.cn: self._scores[a.key] for a in ABILITIES}
        return dict(self._scores)

    def validate(self) -> list[str]:
        """检查属性值是否落在合法区间，返回问题列表（空列表即合法）。"""
        problems: list[str] = []
        for ability in ABILITIES:
            score = self[ability]
            if score < ABILITY_SCORE_MIN or score > ABILITY_SCORE_MAX:
                problems.append(
                    f"{ability.cn} {score} 超出允许范围 "
                    f"{ABILITY_SCORE_MIN}~{ABILITY_SCORE_MAX}"
                )
        return problems


def format_ability_block(scores: AbilitySet | Mapping[Ability | str, int]) -> str:
    """排版属性块，例如 ``力量 15(+2)  敏捷 14(+2)  体质 13(+1)``。"""
    ability_set = scores if isinstance(scores, AbilitySet) else AbilitySet(scores)
    cells = [
        f"{a.cn} {ability_set[a]:>2}({ability_set.modifier(a):+d})" for a in ABILITIES
    ]
    lines = ["  ".join(cells[i : i + 3]) for i in range(0, len(cells), 3)]
    return "\n".join(lines)
