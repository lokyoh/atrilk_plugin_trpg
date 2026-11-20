"""交易记账（PHB 2024「装备」节的基础部分）——纯函数层。

**只做有表可查的记账**：报价、扣钱 / 加钱、库存增删。商店有没有货、价格能不能谈、
这件东西稀有到什么程度该不该卖给他，都是 DM 的事（原文 L6455：「DM 能够决定一家商店里
有哪些物品出售，以及这些物品是否会按本章列出的价格贩卖」）。DM 判完可以用 ``unit_cp``
把价格改掉再走同一套记账。

出售价的依据是 L6472：「装备通常只能以半价出售，而交易货物或贵重物品时则与之不同——
像是宝石或艺术品等物，在市场上能保持原价卖出」。所以：

- 武器 / 护甲 / 盾牌 / 冒险用品 / 工具 / 坐骑 / 载具 → **半价**（向下取整）；
- 商业货物（``trade_goods``）→ **原价**；
- 魔法物品 → 表里没有价格（DMG 才有），必须由 DM 指定，否则拒绝。

不做：制作系统（L7594+ 要工具熟练、原料、工时，属独立功能）、议价判定、
负重与容量校验（负重规则见 ``rule.md``，由 DM 判）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .card import DndCharacter
from .coins import add_coins, parse_cost, spend_coins, to_cp
from .equipment import (
    SUPPLY_TABLES,
    get_armor,
    get_supply,
    get_weapon,
    parse_item_stack,
)

__all__ = [
    "TradeResult",
    "add_items",
    "buy",
    "money_text",
    "parse_wallet",
    "price_of",
    "sale_price_of",
    "sell",
    "take_items",
]

#: 钱包文本里的币种写法 → 统一键
_WALLET_WORDS: dict[str, str] = {
    "金币": "金",
    "银币": "银",
    "铜币": "铜",
    "铂金币": "铂",
    "铂金": "铂",
    "白金": "铂",
    "金": "金",
    "银": "银",
    "铜": "铜",
    "铂": "铂",
    "gp": "金",
    "sp": "银",
    "cp": "铜",
    "pp": "铂",
}

#: 「15 金 10 银」/「50gp」这类钱包写法
_WALLET_RE = re.compile(
    r"(\d+)\s*(金币|银币|铜币|铂金币|铂金|白金|gp|sp|cp|pp|金|银|铜|铂)", re.IGNORECASE
)

#: 表名 -> 给人看的名字（回执里说明价格出处）
_TABLE_CN: dict[str, str] = {
    "gear": "冒险用品",
    "tools": "工具",
    "mounts": "坐骑",
    "harness": "鞍具",
    "vehicles": "载具",
    "trade_goods": "商业货物",
    "lifestyle": "生活开支",
    "food_lodging": "饮食住宿",
}


def _norm(text: str) -> str:
    """物品名归一：去空格、全角括号转半角，便于「箭矢 (20)」与「箭矢(20)」对上。"""
    return (
        str(text or "")
        .strip()
        .replace(" ", "")
        .replace("　", "")
        .replace("（", "(")
        .replace("）", ")")
    )


def _find(items: list[str], name: str) -> tuple[int, str, int] | None:
    """在装备栏里找一条，返回 ``(下标, 基础名, 份数)``。"""
    key = _norm(name)
    if not key:
        return None
    stacks = [(i, parse_item_stack(raw)) for i, raw in enumerate(items)]
    # 先精确（「匕首」对「匕首×2」的 base）
    for index, stack in stacks:
        if _norm(stack.name) == key:
            return index, stack.name, stack.count
    # 再前缀（「箭矢」对「箭矢 (20)」）；至少两个字，免得「剑」吃掉「短剑」
    if len(key) >= 2:
        for index, stack in stacks:
            other = _norm(stack.name)
            if other.startswith(key) or key.startswith(other):
                return index, stack.name, stack.count
    return None


def add_items(equipment: list[str] | tuple[str, ...], name: str, qty: int = 1) -> list[str]:
    """把 ``name`` 收进装备栏 ``qty`` 份；同名合并数量，不重复占行。"""
    items = [str(x) for x in equipment]
    text = str(name or "").strip()
    count = max(1, int(qty or 1))
    if not text:
        return items
    found = _find(items, text)
    if found is None:
        items.append(f"{text}×{count}" if count > 1 else text)
        return items
    index, base, have = found
    total = have + count
    items[index] = f"{base}×{total}" if total > 1 else base
    return items


def take_items(
    equipment: list[str] | tuple[str, ...], name: str, qty: int = 1
) -> tuple[list[str], int]:
    """从装备栏扣 ``qty`` 份，返回 ``(新列表, 实际扣掉的份数)``。

    不够就只扣现有的那些（返回实际数量，由调用方决定收不收这笔钱）。
    """
    items = [str(x) for x in equipment]
    count = max(1, int(qty or 1))
    found = _find(items, name)
    if found is None:
        return items, 0
    index, base, have = found
    if have <= count:
        items.pop(index)
        return items, have
    left = have - count
    items[index] = f"{base}×{left}" if left > 1 else base
    return items, count


def price_of(name: str) -> tuple[str, int, str] | None:
    """查表价，返回 ``(规范名, 单价铜, 表名)``；查不到（含表价为空）返回 ``None``。"""
    text = str(name or "").strip()
    if not text:
        return None
    weapon = get_weapon(text)
    if weapon is not None and weapon.cost:
        return weapon.cn, parse_cost(weapon.cost), "武器"
    armor = get_armor(text)
    if armor is not None and armor.cost:
        return armor.cn, parse_cost(armor.cost), "护甲"
    for table in SUPPLY_TABLES:
        row = get_supply(text, table)
        if row is not None and row.cost:
            return row.cn, parse_cost(row.cost), table
    # 归一化兜底：「箭矢」对数据里的「箭矢 (20)」
    key = _norm(text)
    if len(key) >= 2:
        for table in SUPPLY_TABLES:
            for row in SUPPLY_TABLES[table]:
                if row.cost and _norm(row.cn).startswith(key):
                    return row.cn, parse_cost(row.cost), table
    return None


def sale_price_of(name: str) -> tuple[str, int, str] | None:
    """出售单价，返回 ``(规范名, 单价铜, 价格口径)``。

    装备半价、商业货物原价（PHB 2024 L6472）；查不到表价返回 ``None``。
    """
    found = price_of(name)
    if found is None:
        return None
    canon, unit, table = found
    if table == "trade_goods":
        return canon, unit, "商业货物按原价"
    return canon, unit // 2, "装备按半价"


def money_text(cp: int) -> str:
    """铜币数 → 可读文本（``1500`` → ``金 15``）；0 显示「0」。

    **不主动升铂**：价格写成「金 30」玩家一眼认得出，写成「铂 3」反而要心算。
    钱包本身仍按 :func:`~.coins.format_coins` 如实显示（里面的铂是真的）。
    """
    total = int(cp or 0)
    if total <= 0:
        return "0"
    gold, left = divmod(total, 100)
    silver, copper = divmod(left, 10)
    parts: list[str] = []
    if gold:
        parts.append(f"金 {gold}")
    if silver:
        parts.append(f"银 {silver}")
    if copper:
        parts.append(f"铜 {copper}")
    return " · ".join(parts)


def parse_wallet(text: str) -> dict[str, int] | None:
    """解析「15 金 10 银」/「50gp」→ ``{"金": 15, "银": 10}``；认不出返回 ``None``。

    只读数字，**不管正负号**——增减还是直接设由调用方按前导 ``+`` / ``-`` 决定。
    """
    found: dict[str, int] = {}
    for amount, word in _WALLET_RE.findall(str(text or "")):
        name = _WALLET_WORDS.get(word) or _WALLET_WORDS.get(word.lower())
        if not name:
            continue
        found[name] = found.get(name, 0) + int(amount)
    return found or None


@dataclass(frozen=True, slots=True)
class TradeResult:
    """一次买卖的结果。``ok`` 为假时 ``reason`` 说明为什么没成。"""

    ok: bool
    name: str = ""
    qty: int = 0
    unit_cp: int = 0
    total_cp: int = 0
    source: str = ""
    reason: str = ""

    @property
    def unit_text(self) -> str:
        return money_text(self.unit_cp)

    @property
    def total_text(self) -> str:
        return money_text(self.total_cp)


def buy(
    card: DndCharacter,
    name: str,
    qty: int = 1,
    unit_cp: int | None = None,
) -> TradeResult:
    """买 ``qty`` 份 ``name``：查价 / 扣钱 / 入库。钱不够或没价都不动卡。"""
    count = max(1, int(qty or 1))
    text = str(name or "").strip()
    if not text:
        return TradeResult(False, reason="没说买什么")

    if unit_cp is not None and int(unit_cp) >= 0:
        canon, unit, source = text, int(unit_cp), "DM 定价"
    else:
        found = price_of(text)
        if found is None:
            return TradeResult(
                False,
                name=text,
                qty=count,
                reason="没有表价（魔法物品 / 自制物品的价格由 DM 定）",
            )
        canon, unit, table = found
        source = f"表价（{_TABLE_CN.get(table, table)}）"

    total = unit * count
    wallet, paid = spend_coins(card.money, total)
    if not paid:
        return TradeResult(
            False,
            name=canon,
            qty=count,
            unit_cp=unit,
            total_cp=total,
            source=source,
            reason=f"钱不够（还差 {money_text(total - to_cp(card.money))}）",
        )
    card.money = wallet
    card.equipment = add_items(card.equipment, canon, count)
    card.has_shield = any("盾" in str(x) for x in card.equipment)
    return TradeResult(
        True, name=canon, qty=count, unit_cp=unit, total_cp=total, source=source
    )


def sell(
    card: DndCharacter,
    name: str,
    qty: int = 1,
    unit_cp: int | None = None,
) -> TradeResult:
    """卖 ``qty`` 份 ``name``：出库 / 按半价（商业货物原价）加钱。

    身上没有这件东西就直接拒绝；不够数就按实际有的那些成交。
    """
    count = max(1, int(qty or 1))
    text = str(name or "").strip()
    if not text:
        return TradeResult(False, reason="没说卖什么")

    items, removed = take_items(card.equipment, text, count)
    if removed == 0:
        return TradeResult(False, name=text, qty=count, reason="身上没有这件东西")

    if unit_cp is not None and int(unit_cp) >= 0:
        canon, unit, source = text, int(unit_cp), "DM 定价"
    else:
        found = sale_price_of(text)
        if found is None:
            return TradeResult(
                False,
                name=text,
                qty=removed,
                reason="没有表价（魔法物品等由 DM 定价）",
            )
        canon, unit, source = found

    total = unit * removed
    card.equipment = items
    card.money = add_coins(card.money, {"铜": total})
    card.has_shield = any("盾" in str(x) for x in card.equipment)
    return TradeResult(
        True, name=canon, qty=removed, unit_cp=unit, total_cp=total, source=source
    )
