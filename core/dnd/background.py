"""DND 5e 背景数据。

背景在建卡时提供：两项技能熟练、若干工具 / 语言熟练、以及一段起始装备，
是「技能选择」（``skill_choice.md``）之外的第二条熟练来源。

**数值在 ``res/dnd/backgrounds.yaml``**，这里只留类型与查询函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    languages: int = 0  # 可自选的语言数量
    tools: tuple[str, ...] = ()  # 工具熟练
    equipment: tuple[str, ...] = ()  # 起始装备
    feature: str = ""  # 背景特性
    desc: str = ""

    @property
    def skill_count(self) -> int:
        return len(self.skills)


def _background(row: dict[str, Any]) -> Background:
    """一项背景（``res/dnd/backgrounds.yaml`` 的一项）。"""
    return Background(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        skills=as_texts(row.get("skills")),
        languages=int(row.get("languages", 0)),
        tools=as_texts(row.get("tools")),
        equipment=as_texts(row.get("equipment")),
        feature=str(row.get("feature", "")),
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
