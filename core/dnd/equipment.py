"""DND 5e 装备数据。

对应建卡流程中的 ``equioment.md``。这里给出护甲 / 盾牌 / 常用武器 / 探索者背包等
基础装备的数值，用于计算 AC 与攻击加值。

**数值在 ``res/dnd/equipment.yaml``**（护甲 / 盾牌 / 武器三段），
这里只留类型、类别枚举与查询函数——加一件武器不用改代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .datafiles import as_texts, load_mapping

__all__ = [
    "ARMORS",
    "ARMOR_MAP",
    "SHIELDS",
    "WEAPONS",
    "WEAPON_MAP",
    "Armor",
    "ArmorCategory",
    "ItemStack",
    "Weapon",
    "WeaponCategory",
    "compute_armor_class",
    "get_armor",
    "get_weapon",
    "parse_item_stack",
]


class ArmorCategory(Enum):
    """护甲类别。"""

    LIGHT = "轻甲"
    MEDIUM = "中甲"
    HEAVY = "重甲"
    SHIELD = "盾牌"


class WeaponCategory(Enum):
    """武器类别。"""

    SIMPLE_MELEE = "简易近战"
    SIMPLE_RANGED = "简易远程"
    MARTIAL_MELEE = "军用近战"
    MARTIAL_RANGED = "军用远程"


@dataclass(frozen=True, slots=True)
class Armor:
    """一件护甲或盾牌。"""

    key: str
    cn: str
    category: ArmorCategory
    base_ac: int
    max_dex_bonus: int | None = None  # 中甲 +2，重甲 0，轻甲 None 表示不限
    strength_required: int = 0
    stealth_disadvantage: bool = False
    weight: float = 0.0

    @property
    def is_shield(self) -> bool:
        return self.category is ArmorCategory.SHIELD


@dataclass(frozen=True, slots=True)
class Weapon:
    """一件武器。"""

    key: str
    cn: str
    category: WeaponCategory
    damage: str  # 伤害骰表达式，如 ``1d8``
    damage_type: str  # 伤害类型，如 ``挥砍``
    properties: tuple[str, ...] = ()  # 如 ``灵巧`` ``双手`` ``轻型``
    ranged_normal: int = 0  # 远程常规射程（近战为 0）
    ranged_max: int = 0

    @property
    def is_ranged(self) -> bool:
        return self.ranged_normal > 0

    @property
    def is_finesse(self) -> bool:
        return "灵巧" in self.properties

    @property
    def is_two_handed(self) -> bool:
        return "双手" in self.properties

    @property
    def is_light(self) -> bool:
        return "轻型" in self.properties

    def damage_with_bonus(self, bonus: int) -> str:
        """把加值并入伤害表达式，如 ``1d8+3``。"""
        return f"{self.damage}{bonus:+d}" if bonus else self.damage


@dataclass(slots=True)
class ItemStack:
    """一件带数量的物品（``长剑×1``、``箭×20``）。"""

    name: str
    count: int = 1

    def __str__(self) -> str:
        return f"{self.name}×{self.count}" if self.count > 1 else self.name

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "count": self.count}


def _armor(row: dict[str, Any]) -> Armor:
    """一件护甲或盾牌（``res/dnd/equipment.yaml`` 的 armors / shields 段）。"""
    max_dex = row.get("max_dex_bonus")
    return Armor(
        key=str(row["key"]),
        cn=str(row["cn"]),
        category=ArmorCategory(str(row["category"])),
        base_ac=int(row["base_ac"]),
        max_dex_bonus=None if max_dex is None else int(max_dex),
        strength_required=int(row.get("strength_required", 0)),
        stealth_disadvantage=bool(row.get("stealth_disadvantage", False)),
        weight=float(row.get("weight", 0.0)),
    )


def _weapon(row: dict[str, Any]) -> Weapon:
    """一件武器（``res/dnd/equipment.yaml`` 的 weapons 段）。"""
    return Weapon(
        key=str(row["key"]),
        cn=str(row["cn"]),
        category=WeaponCategory(str(row["category"])),
        damage=str(row["damage"]),
        damage_type=str(row["damage_type"]),
        properties=as_texts(row.get("properties")),
        ranged_normal=int(row.get("ranged_normal", 0)),
        ranged_max=int(row.get("ranged_max", 0)),
    )


_TABLES = load_mapping("equipment")

#: 护甲表：数值在 ``res/dnd/equipment.yaml`` 的 ``armors`` 段
ARMORS: tuple[Armor, ...] = tuple(_armor(row) for row in _TABLES["armors"])

#: 别名 -> 护甲（数据里出现的常见写法）
_ARMOR_ALIASES: dict[str, str] = {
    "木盾": "shield_wood",
    "盾牌": "shield_wood",
    "链甲": "chain_mail",
}

#: 盾牌表：数值在 ``res/dnd/equipment.yaml`` 的 ``shields`` 段
SHIELDS: tuple[Armor, ...] = tuple(_armor(row) for row in _TABLES["shields"])

ARMOR_MAP: dict[str, Armor] = {
    **{a.key: a for a in ARMORS},
    **{a.cn: a for a in ARMORS},
    **{a.key: a for a in SHIELDS},
    **{a.cn: a for a in SHIELDS},
}

#: 武器表：数值在 ``res/dnd/equipment.yaml`` 的 ``weapons`` 段
WEAPONS: tuple[Weapon, ...] = tuple(_weapon(row) for row in _TABLES["weapons"])

WEAPON_MAP: dict[str, Weapon] = {
    **{w.key: w for w in WEAPONS},
    **{w.cn: w for w in WEAPONS},
}

#: 常见别名 -> 武器键（职业装备包里的写法更口语化）
_WEAPON_ALIASES: dict[str, str] = {
    "法杖": "quarterstaff",
    "长弓": "longbow",
    "短弓": "shortbow",
    "两把短剑": "shortsword",
    "短剑×2": "shortsword",
    "手斧×2": "handaxe",
    "匕首×2": "dagger",
}


def get_armor(value: Armor | str) -> Armor | None:
    """按对象 / 键 / 中文名取护甲，取不到返回 ``None``。"""
    if isinstance(value, Armor):
        return value
    text = str(value).strip()
    if text in ARMOR_MAP:
        return ARMOR_MAP[text]
    alias = _ARMOR_ALIASES.get(text)
    return ARMOR_MAP.get(alias) if alias else None


def get_weapon(value: Weapon | str) -> Weapon | None:
    """按对象 / 键 / 中文名取武器，取不到返回 ``None``。"""
    if isinstance(value, Weapon):
        return value
    text = str(value).strip()
    if text in WEAPON_MAP:
        return WEAPON_MAP[text]
    alias = _WEAPON_ALIASES.get(text)
    return WEAPON_MAP.get(alias) if alias else None


def parse_item_stack(text: str, default_count: int = 1) -> ItemStack:
    """把 ``箭×20`` / ``短剑 x2`` / ``皮甲`` 解析成 :class:`ItemStack`。"""
    raw = str(text).strip()
    for sep in ("×", "x", "X", "*"):
        if sep in raw:
            head, _, tail = raw.rpartition(sep)
            tail = tail.strip()
            if head.strip() and tail.isdigit():
                return ItemStack(head.strip(), int(tail))
    return ItemStack(raw, default_count)


def compute_armor_class(
    dex_modifier: int,
    *,
    armors: tuple[str, ...] = (),
    has_shield: bool = False,
    unarmored_defense: int | None = None,
) -> tuple[int, str]:
    """计算 AC。

    :param dex_modifier: 敏捷调整值
    :param armors: 身上穿的护甲名（取其中基础 AC 最高的一件）
    :param has_shield: 是否持盾（额外 +2）
    :param unarmored_defense: 无甲防御公式的基础值（野蛮人 10、武僧 10），
        为 ``None`` 时表示不启用
    :return: ``(AC, 说明文本)``
    """
    worn = [armor for name in armors if (armor := get_armor(name))]
    body = [a for a in worn if not a.is_shield]
    shield = has_shield or any(a.is_shield for a in worn)

    if body:
        armor = max(body, key=lambda a: a.base_ac)
        if armor.max_dex_bonus is None:
            dex_part = dex_modifier
            dex_note = f"{dex_modifier:+d}"
        else:
            applied = min(dex_modifier, armor.max_dex_bonus)
            dex_part = applied
            dex_note = f"{applied:+d}" if applied else "0"
        ac = armor.base_ac + dex_part + (2 if shield else 0)
        note = f"{armor.cn} {armor.base_ac} {dex_note}" + (" +盾牌 2" if shield else "")
        return ac, note

    base = unarmored_defense if unarmored_defense is not None else 10
    ac = base + dex_modifier + (2 if shield else 0)
    note = f"无甲 {base} {dex_modifier:+d}" + (" +盾牌 2" if shield else "")
    return ac, note
