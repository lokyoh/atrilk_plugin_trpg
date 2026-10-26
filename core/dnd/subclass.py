"""DND 5e（2024）子职数据。

**2024 起所有职业都在 3 级**拿到子职 —— 2014 那套「牧师 1 级、法师 2 级」已作废，
这也是高等级开卡必须先算子职的原因（:mod:`.creation` 在 3 级以上才问这一项）。

数值在 ``res/dnd/subclasses.yaml``（12 职业 × 4 = 48 个，来自 5etools 的 XPHB 源），
这里只留类型与查询函数。

子职特性的效果文本是**给 DM 看的规则原文**；少数几条有明确数值、战斗里非记账不可的，
数据里打了结算标记（``crit_at`` / ``superiority_die`` / ``raging_dice``），
由 :mod:`.subclass_grants` 读取 —— 48 个子职里只有 3 个动了这几条，
其余仍由 DM 按 ``text`` 裁定（统一结算会算出大量例外）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """子职的一个特性（带解锁等级）。

    后两个字段是**结算标记**（数据里可选，没有就按 0 / False）：

    - ``crit_at``：攻击检定掷到几点即暴击（勇士 19 / 18；0 = 只在天然 20）；
    - ``raging_dice``：狂怒 —— 狂暴中鲁莽攻击首次命中额外掷狂暴伤害个 d6。
    """

    level: int
    name: str
    text: str = ""
    crit_at: int = 0
    raging_dice: bool = False

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
    #: **卓越骰成长表**：``{等级: (个数, 骰面)}``（战斗大师专用；其余子职为空）。
    #: 2024 版不是「固定 4 个」——3 级 4d8、7 级 5 枚、10 级 d10、15 级 6 枚、18 级 d12。
    superiority: dict[int, tuple[int, int]] = field(default_factory=dict)

    def features_up_to(self, level: int) -> tuple[SubclassFeature, ...]:
        """到 ``level`` 级为止**已解锁**的特性。"""
        return tuple(f for f in self.features if f.level <= max(0, int(level)))

    def superiority_at(self, level: int) -> tuple[int, int]:
        """该等级卓越骰 ``(个数, 骰面)``；没有这个子职特性返回 ``(0, 0)``。"""
        top = max(0, int(level or 0))
        best: tuple[int, tuple[int, int]] | None = None
        for need, value in self.superiority.items():
            if int(need) > top:
                continue
            if best is None or int(need) > best[0]:
                best = (int(need), value)
        return best[1] if best else (0, 0)

    def format_features(self, level: int | None = None) -> str:
        """特性展示文本；给了等级就只列已解锁的。"""
        pool = self.features if level is None else self.features_up_to(level)
        return "\n".join(f"L{f.level} {f.name}：{f.text}".rstrip("：") for f in pool)


def _feature(row: dict[str, Any]) -> SubclassFeature:
    return SubclassFeature(
        level=int(row.get("level", 0)),
        name=str(row.get("name", "")),
        text=str(row.get("text", "")),
        crit_at=int(row.get("crit_at", 0) or 0),
        raging_dice=bool(row.get("raging_dice", False)),
    )


def _superiority_table(raw: Any) -> dict[int, tuple[int, int]]:
    """``{3: [4, 8], 10: [5, 10]}`` → ``{3: (4, 8), 10: (5, 10)}``。"""
    table: dict[int, tuple[int, int]] = {}
    for level, value in (raw or {}).items():
        try:
            count, die = (int(v) for v in value)
        except (TypeError, ValueError):
            continue
        table[int(level)] = (max(0, count), max(0, die))
    return table


def _subclass(row: dict[str, Any]) -> SubClass:
    return SubClass(
        key=str(row["key"]),
        cn=str(row["cn"]),
        short=str(row.get("short", row["cn"])),
        en=str(row.get("en", "")),
        profession=str(row["profession"]),
        desc=str(row.get("desc", "")),
        features=tuple(_feature(f) for f in as_rows(row.get("features"))),
        superiority=_superiority_table(row.get("superiority")),
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
