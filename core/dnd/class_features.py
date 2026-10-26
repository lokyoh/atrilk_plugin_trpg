"""DND 5e（2024）职业等级特性表。

**这里解决的是「等级只改数值、不改能力」那个缺口**：建一张 8 级战士的卡，
引擎原先只知道 HP / 熟练 / 法术位按等级算，不知道 5 级该有「额外攻击」、
2 级该有「动作如潮」。现在 12 职业 × 1~20 级的特性都进了
``res/dnd/class_features.yaml``（283 条，来自 5etools 的 XPHB 源）。

分两类用途：

- **进卡与展示**：:func:`features_for` 按等级给出已解锁的特性，
  ``card.features`` 与角色卡摘要据此显示「这个角色会什么」。
- **进战斗结算**：只有两条真的改数值，数据里用 ``grants`` 打了标记 ——
  ``extra_attack``（每次攻击动作能攻击几次）与 ``action_surge``（动作如潮几次）。
  其余特性（狂暴、凶蛮打击、法术恢复……）差异太大，统一结算会算出大量例外，
  仍由 DM 按 ``text`` 裁定（``query_data class_feature`` 可查原文）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .datafiles import load_table

__all__ = [
    "CLASS_FEATURES",
    "ClassFeature",
    "action_surge_max",
    "attacks_per_action",
    "features_for",
    "grants_at",
]

#: 没有额外攻击时的基础值：一个攻击动作打一次
BASE_ATTACKS = 1


@dataclass(frozen=True, slots=True)
class ClassFeature:
    """职业表里的一条特性。"""

    profession: str
    level: int
    name: str
    text: str = ""
    #: 每次攻击动作能攻击几次（0 = 这条不给额外攻击）
    extra_attack: int = 0
    #: 动作如潮可用几次（0 = 这条不给）
    action_surge: int = 0

    def label(self) -> str:
        return f"L{self.level} {self.name}"


def _feature(row: dict[str, Any]) -> ClassFeature:
    return ClassFeature(
        profession=str(row.get("profession", "")),
        level=int(row.get("level", 0) or 0),
        name=str(row.get("name", "")),
        text=str(row.get("text", "")),
        extra_attack=int(row.get("extra_attack", 0) or 0),
        action_surge=int(row.get("action_surge", 0) or 0),
    )


#: 职业特性表：数值在 ``res/dnd/class_features.yaml``
CLASS_FEATURES: tuple[ClassFeature, ...] = tuple(
    _feature(row) for row in load_table("class_features")
)


def features_for(profession: str, level: int) -> tuple[ClassFeature, ...]:
    """某职业到 ``level`` 级为止**已解锁**的特性（按等级升序）。

    同名条目（如各等级的「属性值提升」）只保留**最早**的那一条 —— 展示时
    连着列四遍「属性值提升」没有意义，具体加几次由 ASI 步负责。
    """
    key = str(profession or "").strip()
    if not key:
        return ()
    top = max(0, int(level or 0))
    seen: set[str] = set()
    out: list[ClassFeature] = []
    for feat in sorted(CLASS_FEATURES, key=lambda f: (f.level, f.name)):
        if feat.profession != key or feat.level > top:
            continue
        if feat.name in seen:
            continue
        seen.add(feat.name)
        out.append(feat)
    return tuple(out)


def grants_at(profession: str, level: int, field: str) -> int:
    """取某职业到 ``level`` 级为止该 ``grants`` 标记的**最大**值（没有则 0）。

    战士的额外攻击在 5 / 11 / 20 级各升级一次，取最大值就是当前该有的次数。
    ⚠️ 直接遍历全表而不是 :func:`features_for`——后者为了展示把同名条目去重
    （「动作如潮」17 级那条与 2 级同名，会被去重掉，但它的 grants 是 2）。
    """
    key = str(profession or "").strip()
    if not key:
        return 0
    top = max(0, int(level or 0))
    best = 0
    for feat in CLASS_FEATURES:
        if feat.profession != key or feat.level > top:
            continue
        best = max(best, int(getattr(feat, field, 0) or 0))
    return best


def attacks_per_action(profession: str, level: int) -> int:
    """一个攻击动作能攻击几次（战士 5/11/20 级为 2/3/4，其余职业 5 级为 2）。"""
    return max(BASE_ATTACKS, grants_at(profession, level, "extra_attack"))


def action_surge_max(profession: str, level: int) -> int:
    """动作如潮每次休息之间能用几次（战士 2 级 1 次、17 级 2 次，其余职业 0）。"""
    return grants_at(profession, level, "action_surge")
