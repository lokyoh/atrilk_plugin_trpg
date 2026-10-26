"""施法者每级的法术数量（PHB 2024「准备法术」）。

2024 与 2014 的差别有两处，都在这张表里：

1. **八个施法职业全部是准备制**——旧书的「已知法术型」（吟游诗人 / 术士 /
   魔契师 / 游侠）在 2024 也准备法术，只是「何时能改、一次换几个」不同
   （手册第七章「职业的准备法术」表）。所以别再按「已知 / 准备」分流。
2. **准备数是表里查的固定值**，不是 2014 那套「等级 + 属性调整值」
   （法师 1 级就是 4 个，20 级 25 个，与智力无关）。

另有一条只在法师身上：**法术书**（``spellbook``）是累计条目数（1 级 6 个、
每级 +2），准备时从法术书里挑 ``prepared`` 个。其余职业的「准备」就是
从整个职业法术表里挑。

数据：``res/dnd/spell_progression.yaml``（由 ``scripts/extract_spell_progression.py``
从 5etools XPHB 生成）。查不到某个职业时**回退**到 ``classes.yaml`` 的
``cantrips`` / ``spells_known``，不至于让旧数据把建卡卡死。
"""

from __future__ import annotations

from typing import Any

from .datafiles import load_table

#: 职业键 → 表里的进度；惰性构建一次
_CACHE: dict[str, dict[str, Any]] = {}

LEVEL_MAX = 20


def _rows() -> dict[str, dict[str, Any]]:
    if not _CACHE:
        for row in load_table("spell_progression"):
            key = str(row.get("key", "")).strip()
            if key:
                _CACHE[key] = dict(row)
    return _CACHE


def progression(key: str) -> dict[str, Any] | None:
    """某职业的数量表（没有就是 ``None``——旧数据 / 非施法者）。"""
    return _rows().get(str(key or "").strip())


def _at(key: str, field: str, level: int) -> int:
    """取某等级的那一项；越界按 1 / 20 级钳住，缺字段返回 0。"""
    row = progression(key)
    if not row:
        return 0
    values = row.get(field)
    if not isinstance(values, list) or not values:
        return 0
    lv = max(1, min(int(level or 1), len(values)))
    return int(values[lv - 1] or 0)


def cantrips_at(key: str, level: int) -> int:
    """该等级已知戏法数。"""
    return _at(key, "cantrips", level)


def prepared_at(key: str, level: int) -> int:
    """该等级可准备的法术数（不含戏法）。"""
    return _at(key, "prepared", level)


def spellbook_at(key: str, level: int) -> int:
    """法术书条目数（只有法师有；其余职业返回 0 = 没有法术书）。"""
    return _at(key, "spellbook", level)


def picks_at(key: str, level: int) -> int:
    """建卡时要挑几个法术：法师挑**法术书**，其余职业挑**准备表**。"""
    book = spellbook_at(key, level)
    return book if book else prepared_at(key, level)


def gain_at(key: str, from_level: int, to_level: int) -> tuple[int, int]:
    """从 ``from_level`` 升到 ``to_level`` 新得的 ``(戏法数, 法术数)``。

    法师的「法术数」指**法术书**条目，其余职业指**可准备数**（与 :func:`picks_at`
    口径一致）。连升多级按两端差值算；降级 / 同级的差值为 0。
    """
    low = max(1, int(from_level or 1))
    high = max(low, int(to_level or low))
    cantrips = max(0, cantrips_at(key, high) - cantrips_at(key, low))
    spells = max(0, picks_at(key, high) - picks_at(key, low))
    return cantrips, spells


def change_of(key: str) -> str:
    """何时可以改准备列表：``rest_long``（长休）或 ``level``（升级）。"""
    row = progression(key) or {}
    return str(row.get("change", "") or "")


def swap_of(key: str) -> str:
    """一次能换几个：``任意`` 或 ``一``。"""
    row = progression(key) or {}
    return str(row.get("swap", "") or "")


def change_text(key: str) -> str:
    """给玩家看的「何时可改准备列表」一句话；未知职业返回空串。"""
    when = change_of(key)
    swap = swap_of(key)
    if not when:
        return ""
    label = "完成一次长休时" if when == "rest_long" else "升级时"
    return f"{label}可改，一次换{'任意多个' if swap == '任意' else '一个'}"
