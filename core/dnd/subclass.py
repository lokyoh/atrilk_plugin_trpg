"""DND 5e（2024）子职数据。

**2024 起所有职业都在 3 级**拿到子职 —— 2014 那套「牧师 1 级、法师 2 级」已作废，
这也是高等级开卡必须先算子职的原因（:mod:`.creation` 在 3 级以上才问这一项）。

数值在 ``res/dnd/subclasses.yaml``（12 职业 × 4 = 48 个，来自 5etools 的 XPHB 源），
这里只留类型与查询函数。

子职特性的效果文本是**给 DM 看的规则原文**，引擎不做自动结算（子职能力差异太大，
统一结算会算出大量例外）；战斗里由 DM 按文本裁定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .datafiles import as_rows, load_table

__all__ = [
    "SUBCLASSES",
    "SUBCLASS_LEVEL",
    "SUBCLASS_MAP",
    "SubClass",
    "SubclassFeature",
    "get_subclass",
    "resolve_subclass",
    "subclass_choices",
]


#: 获得子职的等级（PHB 2024：所有职业统一 3 级）
SUBCLASS_LEVEL = 3


@dataclass(frozen=True, slots=True)
class SubclassFeature:
    """子职的一个特性（带解锁等级）。"""

    level: int
    name: str
    text: str = ""

    def label(self) -> str:
        """「L3 狂怒」这样的短标签。"""
        return f"L{self.level} {self.name}"


@dataclass(frozen=True, slots=True)
class SubClass:
    """一个子职。"""

    key: str
    cn: str  # 全名（狂战士道途）
    short: str  # 短名（狂战士）
    en: str
    profession: str  # 所属职业内部键
    desc: str = ""
    features: tuple[SubclassFeature, ...] = ()

    def features_up_to(self, level: int) -> tuple[SubclassFeature, ...]:
        """到 ``level`` 级为止**已解锁**的特性。"""
        return tuple(f for f in self.features if f.level <= max(0, int(level)))

    def format_features(self, level: int | None = None) -> str:
        """特性展示文本；给了等级就只列已解锁的。"""
        pool = self.features if level is None else self.features_up_to(level)
        return "\n".join(f"L{f.level} {f.name}：{f.text}".rstrip("：") for f in pool)


def _feature(row: dict[str, Any]) -> SubclassFeature:
    return SubclassFeature(
        level=int(row.get("level", 0)),
        name=str(row.get("name", "")),
        text=str(row.get("text", "")),
    )


def _subclass(row: dict[str, Any]) -> SubClass:
    return SubClass(
        key=str(row["key"]),
        cn=str(row["cn"]),
        short=str(row.get("short", row["cn"])),
        en=str(row.get("en", "")),
        profession=str(row["profession"]),
        desc=str(row.get("desc", "")),
        features=tuple(_feature(f) for f in as_rows(row.get("features"))),
    )


#: 子职表：数值在 ``res/dnd/subclasses.yaml``
SUBCLASSES: tuple[SubClass, ...] = tuple(
    _subclass(row) for row in load_table("subclasses")
)

SUBCLASS_MAP: dict[str, SubClass] = {s.key: s for s in SUBCLASSES}


def get_subclass(value: SubClass | str) -> SubClass:
    """按子职对象 / 内部键 / 全名 / 短名 / 英文名取出子职。"""
    if isinstance(value, SubClass):
        return value
    text = str(value).strip()
    if text in SUBCLASS_MAP:
        return SUBCLASS_MAP[text]
    for sub in SUBCLASSES:
        if text in (sub.cn, sub.short) or sub.en.lower() == text.lower():
            return sub
    raise KeyError(f"未知子职：{value}")


def resolve_subclass(value: SubClass | str) -> SubClass | None:
    """:func:`get_subclass` 的**认不出给 None**版本（旧存档 / 玩家乱填不能崩）。"""
    try:
        return get_subclass(value)
    except KeyError:
        return None


def subclass_choices(profession: str) -> tuple[SubClass, ...]:
    """某职业可选的子职（中文全名，供建卡菜单用）。"""
    key = str(profession or "").strip()
    return tuple(s for s in SUBCLASSES if s.profession == key)
