"""《风骸岛之龙》用到的生物：**数据在** ``res/dnd/monsters/{dosi,xmm}.yaml``。

原先这里手写了一张数据卡表（数值按模组正文估的）；现在怪物统一由数据文件驱动，
本模块只留「这个模组用到哪些生物」的**名单**——供预设与测试按名取用，
数值一律从 :func:`~.monsters.get_monster` 拿（也就是书上那份）。
"""

from __future__ import annotations

from .monster_types import Monster

__all__ = ["STORMWRECK_MONSTERS", "STORMWRECK_SOURCES"]

#: 附录 B 里点到名的生物（模组自带的 DoSI 那批 + 它引用的怪物图鉴条目）
_NAMES: tuple[str, ...] = (
    "蓝龙雏龙",
    "青铜龙雏龙",
    "火蛇",
    "烟雾龙兽",
    "丧尸",
    "鸟妖",
    "狗头人修补匠",
    "带翼狗头人",
    "蕈人成体",
    "蕈人幼体",
    "塞娜萨",
    "孢子仆从八爪鱼",
    "茹娜拉",
    "紫腐菌",
    "塔拉克",
    "瓦努斯",
)


def _load() -> tuple[Monster, ...]:
    from .monsters import get_monster

    return tuple(
        monster
        for monster in (get_monster(name) for name in _NAMES)
        if monster is not None
    )


STORMWRECK_MONSTERS: tuple[Monster, ...] = _load()

#: 出处：怪物中文名 → 出处缩写（取自数据卡的 ``source``）
STORMWRECK_SOURCES: dict[str, str] = {
    monster.cn: monster.source for monster in STORMWRECK_MONSTERS if monster.source
}
