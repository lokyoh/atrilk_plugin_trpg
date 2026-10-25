"""1 级职业特性的「选择项」（PHB 2024）。

2024 有 6 处 1 级特性要玩家挑，其中 4 处是本模块的数据：

- 牧师·**圣职**（守护者 / 施法者）
- 德鲁伊·**原初职能**（术士 / 守护者）
- 战士·**战斗风格**（10 项战斗风格专长）
- 魔契师·**魔能祈唤**（1 级无先决的 5 条）

另外两处走既有表：游荡者·**专精**选技能（`:mod:`.skills`）、武器精通选武器（`:mod:`.equipment`）。

**数值在 ``res/dnd/options.yaml``**（分段表，见 :func:`~.datafiles.load_mapping`）。
带结算字段的选项会被引擎真算（`ac_bonus` / `ranged_attack_bonus`），
其余只落卡、由 DM 按 `desc` 裁定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .datafiles import as_texts, load_mapping

__all__ = [
    "DIVINE_ORDERS",
    "ELDRITCH_INVOCATIONS",
    "FIGHTING_STYLES",
    "OPTION_GROUPS",
    "PRIMAL_ORDERS",
    "FeatureOption",
    "feature_option",
    "options_of",
]


@dataclass(frozen=True, slots=True)
class FeatureOption:
    """一个可选的特性选项。"""

    key: str
    cn: str  #: 手册中文名
    desc: str = ""  #: 一句话效果
    armor: tuple[str, ...] = ()  #: 额外护甲受训
    weapons: tuple[str, ...] = ()  #: 额外武器受训（"军用武器" 这类整类词）
    ac_bonus: int = 0  #: 护甲等级加值（防御）
    ranged_attack_bonus: int = 0  #: 远程攻击加值（箭术）
    extra_cantrip: int = 0  #: 额外戏法数（施法者 / 术士原初职能）


def _option(row: dict[str, Any]) -> FeatureOption:
    return FeatureOption(
        key=str(row["key"]),
        cn=str(row["cn"]),
        desc=str(row.get("desc", "")),
        armor=as_texts(row.get("armor")),
        weapons=as_texts(row.get("weapons")),
        ac_bonus=int(row.get("ac_bonus", 0) or 0),
        ranged_attack_bonus=int(row.get("ranged_attack_bonus", 0) or 0),
        extra_cantrip=int(row.get("extra_cantrip", 0) or 0),
    )


_GROUPS: dict[str, tuple[FeatureOption, ...]] = {
    group: tuple(_option(row) for row in rows)
    for group, rows in load_mapping("options").items()
}

#: 四个分组（顺序即建卡提问顺序）
OPTION_GROUPS: tuple[str, ...] = (
    "divine_orders",
    "primal_orders",
    "fighting_styles",
    "eldritch_invocations",
)

DIVINE_ORDERS: tuple[FeatureOption, ...] = _GROUPS.get("divine_orders", ())
PRIMAL_ORDERS: tuple[FeatureOption, ...] = _GROUPS.get("primal_orders", ())
FIGHTING_STYLES: tuple[FeatureOption, ...] = _GROUPS.get("fighting_styles", ())
ELDRITCH_INVOCATIONS: tuple[FeatureOption, ...] = _GROUPS.get(
    "eldritch_invocations", ()
)


def options_of(group: str) -> tuple[FeatureOption, ...]:
    """某个分组的所有选项（未知分组返回空）。"""
    return _GROUPS.get(str(group), ())


def feature_option(group: str, value: str | FeatureOption | None) -> FeatureOption | None:
    """按选项键 / 中文名取出选项；找不到返回 ``None``。"""
    if isinstance(value, FeatureOption):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for option in _GROUPS.get(str(group), ()):
        if text in (option.key, option.cn):
            return option
    return None
