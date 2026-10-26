"""DND 5e 语言数据（PHB 2024 第二章 2133-2160）。

每个角色至少懂三门语言：**通用语**（人人都懂）+ 从标准语言表**自选或掷骰两门**。
稀有语言不进自选池，只有特定特性（血系 / 职业 / 专长）才会给。

2024 起背景**不给语言**（第四章 3143-3155：背景组件只有 属性值 / 起源专长 /
技能 / 工具 / 行装），语言走独立的「选择语言」建卡步；种族仍可自带语言
（人类额外多一门自选，见 :mod:`.race`）。

**数值在 ``res/dnd/languages.yaml``**，这里只留类型与查询函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .datafiles import load_table

__all__ = [
    "COMMON",
    "LANGUAGES",
    "LANGUAGE_MAP",
    "RARE_LANGUAGES",
    "STANDARD_LANGUAGES",
    "Language",
    "language_choices",
    "standard_language_names",
]


@dataclass(frozen=True, slots=True)
class Language:
    """一门语言。"""

    key: str
    cn: str
    #: ``standard`` = 标准语言（进自选池）；``rare`` = 稀有语言（只由特性给）
    tier: str
    origin: str = ""


def _language(row: dict[str, Any]) -> Language:
    return Language(
        key=str(row["key"]),
        cn=str(row["cn"]),
        tier=str(row.get("tier", "standard")),
        origin=str(row.get("origin", "")),
    )


#: 语言表：数值在 ``res/dnd/languages.yaml``
LANGUAGES: tuple[Language, ...] = tuple(
    _language(row) for row in load_table("languages")
)

STANDARD_LANGUAGES: tuple[Language, ...] = tuple(
    item for item in LANGUAGES if item.tier == "standard"
)
RARE_LANGUAGES: tuple[Language, ...] = tuple(
    item for item in LANGUAGES if item.tier == "rare"
)

#: 通用语：人人都会，不在自选池里（标准语言表 1d12 里它是「—」）
COMMON = "通用语"

LANGUAGE_MAP: dict[str, Language] = {}
for _item in LANGUAGES:
    LANGUAGE_MAP[_item.key] = _item
    LANGUAGE_MAP[_item.cn] = _item


def standard_language_names() -> tuple[str, ...]:
    """可选的标准语言中文名（不含通用语）。"""
    return tuple(item.cn for item in STANDARD_LANGUAGES)


def language_choices(*, include_rare: bool = False) -> tuple[str, ...]:
    """建卡时可选的语言中文名。"""
    pool = STANDARD_LANGUAGES if not include_rare else (*STANDARD_LANGUAGES, *RARE_LANGUAGES)
    return tuple(item.cn for item in pool)
