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
    "ARMOR_CATEGORIES",
    "ARMOR_MAP",
    "FOOD_LODGING",
    "GEAR",
    "HARNESS",
    "LIFESTYLES",
    "MOUNTS",
    "PACKS",
    "SHIELDS",
    "SUPPLY_TABLES",
    "TOOLS",
    "TRADE_GOODS",
    "VEHICLES",
    "WEAPONS",
    "WEAPON_CATEGORIES",
    "WEAPON_MAP",
    "Armor",
    "ArmorCategory",
    "ItemStack",
    "Supply",
    "Weapon",
    "WeaponCategory",
    "all_supply_names",
    "armor_proficient",
    "compute_armor_class",
    "expand_packs",
    "get_armor",
    "get_supply",
    "get_weapon",
    "pack_contents",
    "parse_item_stack",
    "supply_names",
    "weapon_options_for",
    "weapon_proficient",
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


#: 护甲类别名（熟练项里写这些字）
ARMOR_CATEGORIES: tuple[str, ...] = ("轻甲", "中甲", "重甲", "盾牌")

#: 武器大类名（熟练项里写这些字 = 整个类别）
WEAPON_CATEGORIES: tuple[str, ...] = ("简易武器", "军用武器")


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
    #: 两用武器**双手**持用时的伤害骰（``1d10``）；空 = 不两用
    two_handed_damage: str = ""
    #: 投掷射程（``投掷（射程20/60）``）；都是 0 = 不能投掷
    thrown_normal: int = 0
    thrown_max: int = 0

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

    @property
    def is_heavy(self) -> bool:
        """重型：小型生物用它攻击有劣势。"""
        return "重型" in self.properties

    @property
    def is_versatile(self) -> bool:
        """两用：可单手也可双手持用，双手用 :attr:`two_handed_damage`。"""
        return bool(self.two_handed_damage)

    @property
    def is_thrown(self) -> bool:
        """可以投掷出去当远程攻击用。"""
        return "投掷" in self.properties and self.thrown_normal > 0

    @property
    def is_ammunition(self) -> bool:
        """弹药武器：每次攻击要花一枚弹药，战后回收一半。"""
        return "弹药" in self.properties

    @property
    def is_reach(self) -> bool:
        return "触及" in self.properties

    @property
    def ammo_names(self) -> tuple[str, ...]:
        """这件武器吃的弹药名（装备栏里认这些写法）。"""
        if not self.is_ammunition:
            return ()
        if self.key == "blowgun":
            return ("吹箭", "吹矢")
        if self.key == "sling":
            return ("弹丸", "投石索子弹")
        return ("弩矢", "箭")


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
        two_handed_damage=str(row.get("two_handed_damage", "") or ""),
        thrown_normal=int(row.get("thrown_normal", 0)),
        thrown_max=int(row.get("thrown_max", 0)),
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

#: 起始「包」-> 内容物：数值在 ``res/dnd/equipment.yaml`` 的 ``packs`` 段。
#: 只收录**容器包**（探索者背包 / 艺人包 / 学者包 / 祭司包 / 窃贼包）；
#: 「材料包」「德鲁伊法器和材料包」是施法材料袋，不在此列（不展开）。
PACKS: dict[str, tuple[str, ...]] = {}
for _row in _TABLES.get("packs", []):
    _items = as_texts(_row.get("items"))
    PACKS[str(_row["cn"])] = _items
    PACKS.setdefault(str(_row["key"]), _items)


def pack_contents(name: str) -> tuple[str, ...] | None:
    """某个「包」的内容物；不是可展开的容器包返回 ``None``。"""
    return PACKS.get(str(name or "").strip())


def expand_packs(items: Any) -> list[str]:
    """把容器包展开成「包本身 + 内容物」。

    ``材料包`` 这类不在 :data:`PACKS` 里，原样保留。展开后**仍保留包本身**
    （包是容器，东西装在包里），重复项由调用方 ``dict.fromkeys`` 去重。
    """
    out: list[str] = []
    for raw in items or []:
        text = str(raw)
        out.append(text)
        contents = pack_contents(parse_item_stack(text).name)
        if contents:
            out.extend(contents)
    return out


def weapon_options_for(generic: str) -> list[str]:
    """把「任意军用近战武器」这类泛称换成可选的**具体武器中文名**列表。"""
    text = str(generic or "")
    if "军用近战" in text:
        want = "军用近战"
    elif "军用远程" in text:
        want = "军用远程"
    elif "军用" in text:
        want = "军用"
    elif "简易" in text:
        want = "简易"
    else:
        want = ""
    options: list[str] = []
    for weapon in WEAPONS:
        category = weapon.category.value
        if (
            not want
            or category == want
            or (want == "军用" and category.startswith("军用"))
            or (want == "简易" and category.startswith("简易"))
        ):
            options.append(weapon.cn)
    return options


@dataclass(frozen=True, slots=True)
class Supply:
    """一件非战斗用品（冒险用品 / 工具 / 鞍具 / 坐骑 / 载具 / 货物 / 饮食住宿）。

    ``cost`` 保留**原文**（如 ``"2 gp"`` / ``"1,000 gp"`` / ``"至少10 gp"``），
    要算钱时用 :func:`coins.parse_cost` 折成铜币——采购 / 出售那套程序还没做，
    这里只提供数据与查询。
    """

    key: str
    cn: str
    cost: str = ""
    weight: float = 0.0
    speed: str = ""  # 坐骑 / 载具的速度（如 "40 尺" / "1 mph"）
    carry: str = ""  # 坐骑载重（如 "480 磅"）


def _supply(row: dict[str, Any]) -> Supply:
    """一行非战斗用品（``res/dnd/equipment.yaml`` 的 gear / tools / … 段）。"""
    return Supply(
        key=str(row["key"]),
        cn=str(row["cn"]),
        cost=str(row.get("cost", "") or ""),
        weight=float(row.get("weight", 0.0) or 0.0),
        speed=str(row.get("speed", "") or ""),
        carry=str(row.get("carry", "") or ""),
    )


def _supplies(section: str) -> tuple[Supply, ...]:
    return tuple(_supply(row) for row in _TABLES.get(section, []))


#: 冒险用品（数据转录自 2014 快速参考；2024 第六章有增删，尚未逐条对齐——
#: 见 res/rule.md「规则口径」节的已知未对齐项）
GEAR: tuple[Supply, ...] = _supplies("gear")
#: 工具表（工匠 / 赌具 / 乐器 / 盗贼工具 …）
TOOLS: tuple[Supply, ...] = _supplies("tools")
#: 坐骑与其他动物
MOUNTS: tuple[Supply, ...] = _supplies("mounts")
#: 鞍具、挽具及陆运载具
HARNESS: tuple[Supply, ...] = _supplies("harness")
#: 水运载具
VEHICLES: tuple[Supply, ...] = _supplies("vehicles")
#: 商业货物
TRADE_GOODS: tuple[Supply, ...] = _supplies("trade_goods")
#: 生活开支七档
LIFESTYLES: tuple[Supply, ...] = _supplies("lifestyle")
#: 饮食与住宿
FOOD_LODGING: tuple[Supply, ...] = _supplies("food_lodging")

#: 表名 -> 数据（查询入口）
SUPPLY_TABLES: dict[str, tuple[Supply, ...]] = {
    "gear": GEAR,
    "tools": TOOLS,
    "mounts": MOUNTS,
    "harness": HARNESS,
    "vehicles": VEHICLES,
    "trade_goods": TRADE_GOODS,
    "lifestyle": LIFESTYLES,
    "food_lodging": FOOD_LODGING,
}


def get_supply(value: Supply | str, table: str = "gear") -> Supply | None:
    """按对象 / 键 / 中文名查一件非战斗用品；查不到返回 ``None``。"""
    rows = SUPPLY_TABLES.get(str(table), ())
    if isinstance(value, Supply):
        return value if value in rows else None
    text = str(value).strip()
    for row in rows:
        if row.key == text or row.cn == text:
            return row
    return None


def supply_names(table: str = "gear") -> tuple[str, ...]:
    """某张表的全部中文名。"""
    return tuple(row.cn for row in SUPPLY_TABLES.get(str(table), ()))


def all_supply_names() -> tuple[str, ...]:
    """所有非战斗用品的中文名（给 :func:`card.equipment_vocabulary` 用）。"""
    names: list[str] = []
    for rows in SUPPLY_TABLES.values():
        names.extend(row.cn for row in rows)
    return tuple(names)


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


# --------------------------------------------------------------------------- #
# 熟练项
# --------------------------------------------------------------------------- #


def weapon_proficient(weapon: Weapon | str, entries) -> bool:
    """角色是否有这件武器的熟练项。

    熟练项条目（``entries``）可以是：

    - **类别名**：``简易武器`` / ``军用武器``（覆盖该大类下的每一件）；
    - **带属性的军用子集**：``轻型军用武器`` / ``灵巧军用武器``
      （PHB 2024 武僧「具有轻型属性的军用武器」、游荡者「具有灵巧或轻型属性的军用武器」）；
    - **具体武器**：武器内部键或中文名（``长剑`` / ``longsword``）；
    - **空列表** = 什么都不熟练。

    5e：不具有相应武器的熟练项时，攻击检定**不能**加熟练加值。
    """
    weapon_obj = weapon if isinstance(weapon, Weapon) else get_weapon(weapon)
    if weapon_obj is None:
        # 认不出来的武器（临时拾取 / DM 口头给的）：按不熟练处理，
        # 调用方会给 DM 提示，由 DM 裁决
        return False
    category = weapon_obj.category
    is_martial = category in (
        WeaponCategory.MARTIAL_MELEE,
        WeaponCategory.MARTIAL_RANGED,
    )
    for entry in entries or ():
        text = str(entry).strip()
        if not text:
            continue
        if text == "简易武器" and category in (
            WeaponCategory.SIMPLE_MELEE,
            WeaponCategory.SIMPLE_RANGED,
        ):
            return True
        if text == "军用武器" and is_martial:
            return True
        if text == "轻型军用武器" and is_martial and weapon_obj.is_light:
            return True
        if text == "灵巧军用武器" and is_martial and weapon_obj.is_finesse:
            return True
        if text in (weapon_obj.key, weapon_obj.cn):
            return True
    return False


def armor_proficient(armor: Armor | str, entries) -> bool:
    """角色是否有这件护甲 / 盾牌的熟练项（条目写类别名：轻甲 / 中甲 / 重甲 / 盾牌）。

    5e：着装不具有熟练项的护甲或盾牌时，属性检定、豁免检定、以力量或敏捷进行的
    攻击检定都具有劣势，且不能在该着装下施展法术。
    """
    armor_obj = armor if isinstance(armor, Armor) else get_armor(armor)
    if armor_obj is None:
        return False
    category = armor_obj.category
    for entry in entries or ():
        text = str(entry).strip()
        if not text:
            continue
        if text == category.value or text == ("盾牌" if armor_obj.is_shield else ""):
            return True
    return False
