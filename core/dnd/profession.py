"""DND 5e 职业数据。

对应建卡流程中的 ``profession.md``。每个职业提供：

- 生命骰（决定生命值）与 1 级生命值
- 两项豁免熟练
- 护甲 / 武器熟练项（不会的护甲穿上去有劣势，不会的武器不加熟练加值）
- 可选技能池与可选数量（对应 ``skill_choice.md``）
- 起始装备包（对应 ``equioment.md``）
- 1 级职业特性

**数值在 ``res/dnd/classes.yaml``**，这里只留类型与查询函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .attributes import get_ability
from .datafiles import as_rows, as_texts, load_table
from .skills import SKILL_MAP

__all__ = [
    "CLASSES",
    "CLASS_MAP",
    "CLASS_SKILL_POOLS",
    "DndClass",
    "class_choices",
    "get_class",
    "profession_skill_choices",
]


@dataclass(frozen=True, slots=True)
class EquipmentChoice:
    """起始装备二选一（如「长剑 + 盾牌」或「两把短剑」）。"""

    key: str
    cn: str
    items: tuple[str, ...]

    @property
    def label(self) -> str:
        return "、".join(self.items) if self.items else self.cn


@dataclass(frozen=True, slots=True)
class DndClass:
    """一个职业。"""

    key: str
    cn: str
    en: str
    hit_die: int  # 生命骰面数
    primary: tuple[str, ...]  # 主要属性内部键
    saves: tuple[str, ...]  # 豁免熟练的属性内部键
    skill_pool: tuple[str, ...]  # 可选技能内部键
    skill_count: int  # 可选技能数量
    equipment_packages: tuple[EquipmentChoice, ...]
    fixed_equipment: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    desc: str = ""
    casting: str = ""  # 施法属性内部键（"" = 非施法者）
    caster_kind: str = ""  # full / half / pact（"" = 非施法者）
    cantrips: int = 0  # 建卡时可选戏法数
    spells_known: int = 0  # 建卡时可选法术数（法师 = 法术书收录数）
    #: 护甲 / 盾牌熟练项（类别名：轻甲 / 中甲 / 重甲 / 盾牌）
    armor_proficiencies: tuple[str, ...] = ()
    #: 武器熟练项：``简易武器`` / ``军用武器``（整个类别）或具体武器键 / 中文名
    weapon_proficiencies: tuple[str, ...] = ()

    @property
    def level1_hp(self) -> int:
        """1 级基础生命值（生命骰最大值 + 体质调整值，体质在结算时叠加）。"""
        return self.hit_die

    @property
    def is_caster(self) -> bool:
        """是否施法职业。"""
        return bool(self.casting and self.caster_kind)

    @property
    def casting_text(self) -> str:
        """施法属性的中文名（"" = 非施法者）。"""
        return get_ability(self.casting).cn if self.casting else ""

    @property
    def caster_text(self) -> str:
        """施法类型的中文名（"" = 非施法者）。"""
        return {"full": "全职施法", "half": "半施法", "pact": "契约魔法"}.get(
            self.caster_kind, ""
        )

    @property
    def hit_dice(self) -> str:
        return f"1d{self.hit_die}"

    def skill_names(self) -> tuple[str, ...]:
        """可选技能的中文名列表。"""
        return tuple(SKILL_MAP[k].cn for k in self.skill_pool)

    def format_equipment(self) -> str:
        """装备包的展示文本。"""
        lines: list[str] = []
        for i, pkg in enumerate(self.equipment_packages, 1):
            lines.append(f"{i}. {pkg.label}")
        if self.fixed_equipment:
            lines.append(f"固定：{'、'.join(self.fixed_equipment)}")
        return "\n".join(lines)

    def format_proficiencies(self) -> str:
        """护甲 / 武器熟练项的展示文本（空项省略）。"""
        bits = []
        if self.armor_proficiencies:
            bits.append("护甲 " + "、".join(self.armor_proficiencies))
        if self.weapon_proficiencies:
            bits.append("武器 " + "、".join(self.weapon_proficiencies))
        return "；".join(bits)


def _package(row: dict[str, Any]) -> EquipmentChoice:
    """一个起始装备包（二选一里的那一项）。"""
    return EquipmentChoice(
        key=str(row["key"]),
        cn=str(row["cn"]),
        items=as_texts(row.get("items")),
    )


def _klass(row: dict[str, Any]) -> DndClass:
    """一个职业（``res/dnd/classes.yaml`` 的一项）。"""
    return DndClass(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        hit_die=int(row["hit_die"]),
        primary=as_texts(row.get("primary")),
        saves=as_texts(row.get("saves")),
        skill_pool=as_texts(row.get("skill_pool")),
        skill_count=int(row.get("skill_count", 2)),
        equipment_packages=tuple(
            _package(pkg) for pkg in as_rows(row.get("equipment_packages"))
        ),
        fixed_equipment=as_texts(row.get("fixed_equipment")),
        features=as_texts(row.get("features")),
        desc=str(row.get("desc", "")),
        casting=str(row.get("casting", "")),
        caster_kind=str(row.get("caster_kind", "")),
        cantrips=int(row.get("cantrips", 0)),
        spells_known=int(row.get("spells_known", 0)),
        armor_proficiencies=as_texts(row.get("armor_proficiencies")),
        weapon_proficiencies=as_texts(row.get("weapon_proficiencies")),
    )


#: 职业表：数值在 ``res/dnd/classes.yaml``
CLASSES: tuple[DndClass, ...] = tuple(_klass(row) for row in load_table("classes"))

CLASS_MAP: dict[str, DndClass] = {c.key: c for c in CLASSES}


def get_class(value: DndClass | str) -> DndClass:
    """按职业对象 / 内部键 / 中文名取出职业。"""
    if isinstance(value, DndClass):
        return value
    text = str(value).strip()
    if text in CLASS_MAP:
        return CLASS_MAP[text]
    for cls in CLASSES:
        if cls.cn == text or cls.en.lower() == text.lower():
            return cls
    raise KeyError(f"未知职业：{value}")


def class_choices() -> tuple[str, ...]:
    """全部职业中文名。"""
    return tuple(c.cn for c in CLASSES)


def profession_skill_choices(value: DndClass | str) -> tuple[tuple[str, ...], int]:
    """取某职业的可选技能（中文名）与可选数量，供 ``skill_choice`` 流程使用。"""
    cls = get_class(value)
    return cls.skill_names(), cls.skill_count


#: 兼容命名：职业 -> 可选技能池
CLASS_SKILL_POOLS: dict[str, tuple[str, ...]] = {c.key: c.skill_pool for c in CLASSES}
