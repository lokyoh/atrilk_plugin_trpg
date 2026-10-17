"""DND 5e 装备数据。

对应建卡流程中的 ``equioment.md``。这里给出护甲 / 盾牌 / 常用武器 / 探索者背包等
基础装备的数值，用于计算 AC 与攻击加值；完整装备表可后续扩充。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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


ARMORS: tuple[Armor, ...] = (
    Armor("padded", "棉甲", ArmorCategory.LIGHT, 11, None, 0, True, 8),
    Armor("leather", "皮甲", ArmorCategory.LIGHT, 11, None, 0, False, 10),
    Armor("studded_leather", "镶钉皮甲", ArmorCategory.LIGHT, 12, None, 0, False, 13),
    Armor("hide", "兽皮甲", ArmorCategory.MEDIUM, 12, 2, 0, False, 12),
    Armor("chain_shirt", "链甲衫", ArmorCategory.MEDIUM, 13, 2, 0, False, 20),
    Armor("scale_mail", "鳞甲", ArmorCategory.MEDIUM, 14, 2, 0, True, 45),
    Armor("breastplate", "胸甲", ArmorCategory.MEDIUM, 14, 2, 0, False, 20),
    Armor("half_plate", "半身板甲", ArmorCategory.MEDIUM, 15, 2, 0, True, 40),
    Armor("ring_mail", "环甲", ArmorCategory.HEAVY, 14, 0, 0, True, 40),
    Armor("chain_mail", "链甲", ArmorCategory.HEAVY, 16, 0, 13, True, 55),
    Armor("splint", "板条甲", ArmorCategory.HEAVY, 17, 0, 15, True, 60),
    Armor("plate", "全身板甲", ArmorCategory.HEAVY, 18, 0, 15, True, 65),
)

#: 别名 -> 护甲（数据里出现的常见写法）
_ARMOR_ALIASES: dict[str, str] = {
    "木盾": "shield_wood",
    "盾牌": "shield_wood",
    "链甲": "chain_mail",
}

SHIELDS: tuple[Armor, ...] = (
    Armor("shield_wood", "木盾", ArmorCategory.SHIELD, 2, None, 0, False, 6),
    Armor("shield_steel", "钢盾", ArmorCategory.SHIELD, 2, None, 0, False, 6),
)

ARMOR_MAP: dict[str, Armor] = {
    **{a.key: a for a in ARMORS},
    **{a.cn: a for a in ARMORS},
    **{a.key: a for a in SHIELDS},
    **{a.cn: a for a in SHIELDS},
}

WEAPONS: tuple[Weapon, ...] = (
    # 简易近战
    Weapon("club", "棍棒", WeaponCategory.SIMPLE_MELEE, "1d4", "钝击", ("轻型",)),
    Weapon(
        "dagger",
        "匕首",
        WeaponCategory.SIMPLE_MELEE,
        "1d4",
        "穿刺",
        ("灵巧", "轻型", "投掷"),
    ),
    Weapon("greatclub", "巨棒", WeaponCategory.SIMPLE_MELEE, "1d8", "钝击", ("双手",)),
    Weapon(
        "handaxe", "手斧", WeaponCategory.SIMPLE_MELEE, "1d6", "挥砍", ("轻型", "投掷")
    ),
    Weapon("javelin", "标枪", WeaponCategory.SIMPLE_MELEE, "1d6", "穿刺", ("投掷",)),
    Weapon("mace", "硬头槌", WeaponCategory.SIMPLE_MELEE, "1d6", "钝击", ()),
    Weapon(
        "quarterstaff", "长棍", WeaponCategory.SIMPLE_MELEE, "1d6", "钝击", ("双手",)
    ),
    Weapon("spear", "矛", WeaponCategory.SIMPLE_MELEE, "1d6", "穿刺", ("投掷", "双手")),
    # 简易远程
    Weapon(
        "light_crossbow",
        "轻弩",
        WeaponCategory.SIMPLE_RANGED,
        "1d8",
        "穿刺",
        ("双手", "装填"),
        80,
        320,
    ),
    Weapon(
        "shortbow",
        "短弓",
        WeaponCategory.SIMPLE_RANGED,
        "1d6",
        "穿刺",
        ("双手",),
        80,
        320,
    ),
    Weapon("sling", "投石索", WeaponCategory.SIMPLE_RANGED, "1d4", "钝击", (), 30, 120),
    # 军用近战
    Weapon("battleaxe", "战斧", WeaponCategory.MARTIAL_MELEE, "1d8", "挥砍", ()),
    Weapon("greataxe", "巨斧", WeaponCategory.MARTIAL_MELEE, "1d12", "挥砍", ("双手",)),
    Weapon(
        "greatsword", "巨剑", WeaponCategory.MARTIAL_MELEE, "2d6", "挥砍", ("双手",)
    ),
    Weapon("longsword", "长剑", WeaponCategory.MARTIAL_MELEE, "1d8", "挥砍", ()),
    Weapon("rapier", "细剑", WeaponCategory.MARTIAL_MELEE, "1d8", "穿刺", ("灵巧",)),
    Weapon(
        "scimitar",
        "弯刀",
        WeaponCategory.MARTIAL_MELEE,
        "1d6",
        "挥砍",
        ("灵巧", "轻型"),
    ),
    Weapon(
        "shortsword",
        "短剑",
        WeaponCategory.MARTIAL_MELEE,
        "1d6",
        "穿刺",
        ("灵巧", "轻型"),
    ),
    Weapon("warhammer", "战锤", WeaponCategory.MARTIAL_MELEE, "1d8", "钝击", ()),
    Weapon("warpick", "战镐", WeaponCategory.MARTIAL_MELEE, "1d8", "穿刺", ()),
    Weapon(
        "halberd", "戟", WeaponCategory.MARTIAL_MELEE, "1d10", "挥砍", ("双手", "触及")
    ),
    # 军用远程
    Weapon(
        "longbow",
        "长弓",
        WeaponCategory.MARTIAL_RANGED,
        "1d8",
        "穿刺",
        ("双手",),
        150,
        600,
    ),
    Weapon(
        "hand_crossbow",
        "手弩",
        WeaponCategory.MARTIAL_RANGED,
        "1d6",
        "穿刺",
        ("轻型", "装填"),
        30,
        120,
    ),
    Weapon(
        "heavy_crossbow",
        "重弩",
        WeaponCategory.MARTIAL_RANGED,
        "1d10",
        "穿刺",
        ("双手", "装填"),
        100,
        400,
    ),
)

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
