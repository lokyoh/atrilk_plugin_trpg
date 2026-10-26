"""DND 5e（2024）职业资源表：狂暴次数、回气、功力、引导神力……

**这里解决的是「职业资源只在纸上、不在引擎里」那个缺口**。职业特性表
（:mod:`~.class_features`）管「会不会」，这张表管「有几发」：野蛮人 12 级有
5 次狂暴、战士 4 级有 3 次回气、武僧 10 级有 10 点功力。数值全在
``res/dnd/class_resources.yaml``（`scripts/extract_class_resources.py` 从
5etools 的 XPHB 职业表列生成，12 职业 × 20 级）。

两类字段要区分开：

- **可消耗资源**（:data:`SPENDABLE`）：有「用掉几发」的概念，记在角色卡
  ``DndCharacter.resources``（已用次数），休息时按 :data:`RECOVERY` 恢复 ——
  狂暴 1 次 / 回气全部 / 功力全部 / 引导神力 1 次 / 荒野形态 1 次。
- **固定数值**（其余）：武艺骰面数、诗人骰面数、偷袭骰个数、无甲移动尺数、
  武器精通可选数、契约法术位环阶 —— 不消耗，只是「这个等级是多少」。

法师没有资源列（它的等级表只有法术数量，归 ``spell_progression.yaml``），
所以 :func:`resources_of` 对法师返回 ``None`` —— 调用方不必特判。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .class_features import action_surge_max
from .datafiles import load_table

__all__ = [
    "CLASS_RESOURCES",
    "DIE_FIELDS",
    "RECOVERY",
    "RESOURCE_CN",
    "SPENDABLE",
    "ClassResources",
    "resources_of",
    "resources_text",
    "spendable_at",
    "value_at",
]

#: 字段 → 中文名（结算文案与查询输出用）
RESOURCE_CN: dict[str, str] = {
    "action_surge": "动作如潮",
    "bardic_die": "诗人骰",
    "channel_divinity": "引导神力",
    "favored_enemy": "宿敌",
    "focus": "功力",
    "martial_arts_die": "武艺骰",
    "pact_slot_level": "契约法术位环阶",
    "pact_slots": "契约法术位",
    "rage_damage": "狂暴伤害",
    "rage_uses": "狂暴",
    "second_wind": "回气",
    "sneak_attack": "偷袭",
    "sorcery": "术法点",
    "superiority": "卓越骰",
    "unarmored_move": "无甲移动",
    "weapon_mastery": "武器精通",
    "wild_shape": "荒野形态",
}

#: 这两类字段是**骰子面数**（展示成 ``d8``，不是 8 点）
DIE_FIELDS: frozenset[str] = frozenset({"martial_arts_die", "bardic_die"})

#: **可消耗资源**：有「用掉几发」，记在卡上、休息恢复
#:
#: ``superiority``（卓越骰）不在职业表里 —— 它是**子职**给的（战斗大师），
#: 上限由 :func:`~.subclass_grants.superiority` 从子职成长表算，这里只登记
#: 名字与恢复口径，好让卡上的通用接口认得它。
SPENDABLE: frozenset[str] = frozenset(
    {
        "action_surge",
        "channel_divinity",
        "favored_enemy",
        "focus",
        "rage_uses",
        "second_wind",
        "sorcery",
        "superiority",
        "wild_shape",
    }
)

#: 休息恢复口径（PHB 2024 各特性的原话都在这三条里）：
#:
#: - ``short_all``：短休或长休**全部**恢复（回气 / 动作如潮 / 功力）；
#: - ``short_one``：短休恢复**1 次**、长休全部恢复（狂暴 / 引导神力 / 荒野形态）；
#: - ``long``：只有长休恢复（术法点 / 宿敌）。
RECOVERY: dict[str, str] = {
    "action_surge": "short_all",
    "channel_divinity": "short_one",
    "favored_enemy": "long",
    "focus": "short_all",
    "rage_uses": "short_one",
    "second_wind": "short_all",
    "sorcery": "long",
    "superiority": "short_all",  # 原文：卓越骰一经使用即消耗，完成短休或长休后恢复
    "wild_shape": "short_one",
}

#: 恢复口径的中文说明（查询 / 回执用）
RECOVERY_CN: dict[str, str] = {
    "short_all": "短休或长休后全满",
    "short_one": "短休回 1 次、长休全满",
    "long": "长休后全满",
}


@dataclass(frozen=True, slots=True)
class ClassResources:
    """一个职业的资源列（每个字段 20 项，第 N 项 = N 级）。"""

    key: str
    name: str
    values: dict[str, tuple[int, ...]]

    def at(self, field: str, level: int) -> int:
        """该等级这个字段的值（没有该字段 / 越界 → 0）。"""
        column = self.values.get(field)
        if not column:
            return 0
        index = max(1, int(level or 1)) - 1
        if index >= len(column):
            index = len(column) - 1
        return int(column[index])

    def fields(self) -> tuple[str, ...]:
        return tuple(self.values)


def _row(row: dict[str, Any]) -> ClassResources:
    raw = row.get("values") or {}
    values = {
        str(field): tuple(int(v or 0) for v in (column or ()))
        for field, column in raw.items()
    }
    return ClassResources(
        key=str(row.get("key", "")),
        name=str(row.get("name", "")),
        values=values,
    )


#: 职业资源表：数值在 ``res/dnd/class_resources.yaml``
CLASS_RESOURCES: tuple[ClassResources, ...] = tuple(
    _row(row) for row in load_table("class_resources")
)

_BY_KEY: dict[str, ClassResources] = {item.key: item for item in CLASS_RESOURCES}


def resources_of(profession: str) -> ClassResources | None:
    """取某职业的资源列；法师这类「没有可消耗资源」的职业返回 ``None``。"""
    return _BY_KEY.get(str(profession or "").strip())


def value_at(profession: str, level: int, field: str) -> int:
    """某职业某等级某字段的数值（查不到 → 0）。"""
    item = resources_of(profession)
    return item.at(field, level) if item is not None else 0


def spendable_at(profession: str, level: int) -> dict[str, int]:
    """该等级**可消耗资源**的上限（只列非 0 的）。

    动作如潮不在数据表里（它是 ``class_features.yaml`` 的 ``grants`` 标记，
    因为 17 级与 2 级同名、需要绕过去重），这里一并取出来，好让卡上的
    通用资源接口对八个职业一视同仁。
    """
    out: dict[str, int] = {}
    item = resources_of(profession)
    if item is not None:
        for field in item.fields():
            if field not in SPENDABLE:
                continue
            value = item.at(field, level)
            if value > 0:
                out[field] = value
    surge = action_surge_max(profession, level)
    if surge > 0:
        out["action_surge"] = surge
    return out


def _format(field: str, value: int) -> str:
    if field in DIE_FIELDS:
        return f"d{value}"
    return str(value)


def resources_text(profession: str, level: int) -> str:
    """该等级的资源概览（查职业时输出）；没有资源返回空串。"""
    item = resources_of(profession)
    parts: list[str] = []
    if item is not None:
        for field in item.fields():
            if field == "pact_slot_level":
                continue  # 和「契约法术位」并成一句写
            value = item.at(field, level)
            if value <= 0:
                continue
            label = RESOURCE_CN.get(field, field)
            if field == "pact_slots":
                ring = item.at("pact_slot_level", level)
                parts.append(f"{label} {value} 个（{ring} 环，短休回满）")
                continue
            text = f"{label} {_format(field, value)}"
            policy = RECOVERY.get(field)
            if policy:
                text += f"（{RECOVERY_CN[policy]}）"
            parts.append(text)
    surge = action_surge_max(profession, level)
    if surge > 0:
        parts.append(f"动作如潮 {surge}（{RECOVERY_CN['short_all']}）")
    return "、".join(parts)
