"""DND 5e 种族数据。

对应流程中的 ``race.md``（建卡第 2 步）。每个种族给出属性加值、体型 / 速度、
特性描述与可选的语言、亚种；亚种的属性加值在种族加值之上再叠加。

**数值在 ``res/dnd/races.yaml``**，这里只留类型与查询函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .datafiles import as_rows, as_texts, load_table

__all__ = [
    "RACES",
    "RACE_MAP",
    "Race",
    "SubRace",
    "default_subrace",
    "get_race",
    "race_choices",
]


@dataclass(frozen=True, slots=True)
class SubRace:
    """亚种（如山地矮人、高等精灵）。"""

    key: str
    cn: str
    bonus: dict[str, int] = field(default_factory=dict)
    traits: tuple[str, ...] = ()
    desc: str = ""
    #: 亚种给的武器熟练项（具体武器键 / 中文名）
    weapons: tuple[str, ...] = ()
    #: 亚种给的护甲熟练项（类别名：轻甲 / 中甲 / 重甲 / 盾牌）
    armor: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.cn


@dataclass(frozen=True, slots=True)
class Race:
    """种族。"""

    key: str
    cn: str
    en: str
    bonus: dict[str, int]
    speed: int
    size: str
    traits: tuple[str, ...]
    languages: tuple[str, ...] = ()
    subraces: tuple[SubRace, ...] = ()
    desc: str = ""
    #: 种族给的武器熟练项（具体武器键 / 中文名）
    weapons: tuple[str, ...] = ()
    #: 种族给的护甲熟练项（类别名）
    armor: tuple[str, ...] = ()

    def bonus_of(self, subrace: SubRace | str | None = None) -> dict[str, int]:
        """叠加亚种后的属性加值。"""
        bonus = dict(self.bonus)
        sub = self.resolve_subrace(subrace) if subrace else None
        if sub:
            for key, value in sub.bonus.items():
                bonus[key] = bonus.get(key, 0) + value
        return bonus

    def weapons_of(self, subrace: SubRace | str | None = None) -> tuple[str, ...]:
        """种族 + 亚种的武器熟练项（去重）。"""
        sub = self.resolve_subrace(subrace) if subrace else None
        return tuple(dict.fromkeys((*self.weapons, *(sub.weapons if sub else ()))))

    def armor_of(self, subrace: SubRace | str | None = None) -> tuple[str, ...]:
        """种族 + 亚种的护甲熟练项（去重）。"""
        sub = self.resolve_subrace(subrace) if subrace else None
        return tuple(dict.fromkeys((*self.armor, *(sub.armor if sub else ()))))

    def resolve_subrace(self, value: SubRace | str | None) -> SubRace | None:
        """把亚种名 / 键解析为亚种对象；无亚种时返回 ``None``。"""
        if value is None or isinstance(value, SubRace):
            return value
        text = str(value).strip()
        for sub in self.subraces:
            if text in (sub.key, sub.cn):
                return sub
        raise KeyError(f"{self.cn}没有名为「{value}」的亚种")

    def traits_of(self, subrace: SubRace | str | None = None) -> tuple[str, ...]:
        """种族特性 + 亚种特性。"""
        traits = list(self.traits)
        sub = self.resolve_subrace(subrace) if subrace else None
        if sub:
            traits.extend(sub.traits)
        return tuple(traits)

    def format_bonus(self, subrace: SubRace | str | None = None) -> str:
        """属性加值的展示文本。"""
        from .attributes import get_ability

        bonus = self.bonus_of(subrace)
        if not bonus:
            return "无属性加值"
        return "、".join(f"{get_ability(k).cn}{v:+d}" for k, v in bonus.items() if v)


def _subrace(row: dict[str, Any]) -> SubRace:
    """一条亚种（山地矮人、高等精灵这类）。"""
    return SubRace(
        key=str(row["key"]),
        cn=str(row["cn"]),
        bonus={str(k): int(v) for k, v in (row.get("bonus") or {}).items()},
        traits=as_texts(row.get("traits")),
        desc=str(row.get("desc", "")),
        weapons=as_texts(row.get("weapons")),
        armor=as_texts(row.get("armor")),
    )


def _race(row: dict[str, Any]) -> Race:
    """一条种族（``res/dnd/races.yaml`` 的一项）。"""
    return Race(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        bonus={str(k): int(v) for k, v in (row.get("bonus") or {}).items()},
        speed=int(row.get("speed", 30)),
        size=str(row.get("size", "中型")),
        traits=as_texts(row.get("traits")),
        languages=as_texts(row.get("languages")),
        subraces=tuple(_subrace(sub) for sub in as_rows(row.get("subraces"))),
        desc=str(row.get("desc", "")),
        weapons=as_texts(row.get("weapons")),
        armor=as_texts(row.get("armor")),
    )


#: 种族表：数值在 ``res/dnd/races.yaml``（加一个种族不用改代码）
RACES: tuple[Race, ...] = tuple(_race(row) for row in load_table("races"))

RACE_MAP: dict[str, Race] = {r.key: r for r in RACES}


def get_race(value: Race | str) -> Race:
    """按种族对象 / 内部键 / 中文名取出种族。"""
    if isinstance(value, Race):
        return value
    text = str(value).strip()
    if text in RACE_MAP:
        return RACE_MAP[text]
    for race in RACES:
        if race.cn == text or race.en.lower() == text.lower():
            return race
    raise KeyError(f"未知种族：{value}")


def race_choices() -> tuple[str, ...]:
    """全部种族中文名，用于生成给玩家的选项列表。"""
    return tuple(r.cn for r in RACES)


def default_subrace(race: Race) -> SubRace | None:
    """取默认亚种（第一个）；无亚种返回 ``None``。"""
    return race.subraces[0] if race.subraces else None
