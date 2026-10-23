"""《凡戴尔的失落矿坑》用到的生物：**数据在** ``res/dnd/monsters/{lmop,xmm}.yaml``。

与 :mod:`.stormwreck` 同一个处理：原先手写的数值表下线，这里只留名单，
数值从数据卡里取。
"""

from __future__ import annotations

from .monster_types import Monster

__all__ = ["LOST_MINE_MONSTERS", "LOST_MINE_SOURCES"]

#: 模组里出现过的生物（模组自带的 LMoP 那批 + 它引用的怪物图鉴条目）
_NAMES: tuple[str, ...] = (
    "巨蜘蛛",
    "穴居攫怪",
    "灰烬丧尸",
    "变形怪",
    "赭果冻",
    "枯枝怪",
    "燃焰之颅",
    "缚灵·墨梅斯克",
    "诺斯怪",
    "观察者眼魔",
    "青年绿龙",
    "毒牙",
    "平民",
    "狂信者",
    "邪恶法师",
    "红标帮恶霸",
    "刚铎·寻岩者",
    "修达·霍温特",
    "南卓·寻岩者",
    "黑蜘蛛那兹纳尔",
)


def _load() -> tuple[Monster, ...]:
    from .monsters import get_monster

    return tuple(
        monster
        for monster in (get_monster(name) for name in _NAMES)
        if monster is not None
    )


LOST_MINE_MONSTERS: tuple[Monster, ...] = _load()

#: 出处：怪物中文名 → 出处缩写（取自数据卡的 ``source``）
LOST_MINE_SOURCES: dict[str, str] = {
    monster.cn: monster.source for monster in LOST_MINE_MONSTERS if monster.source
}
