"""DND 5e 魔法物品数据表。

对应 ``res/rule.md`` 第二十二节「物品规则」，把规则里只有文字的部分落成可查的数据：

- **稀有度**：普通 / 非常稀有 / 稀有 / 非常稀有 / 传说；
- **调谐**：多数魔法物品要调谐才能用，没调谐照常占着（力量护符除外）；
- **法术位消耗**：消耗型物品（药水、卷轴）用完即失效；
- **附魔**：武器 / 护甲的 +1/+2/+3。

物品本身**不占背包格子数**——背包位是「装备」在管的事（:mod:`.equipment`），
这里只管「这个东西是什么、能做什么」。

数据是**可玩的近似值**，以本表为准；按 key / 中文名取用 :func:`get_item`。
表本身在 ``res/dnd/items.yaml``，加一件物品不用改代码。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .datafiles import as_texts, load_table

__all__ = [
    "ITEMS",
    "ITEM_KINDS",
    "ITEM_RARITIES",
    "Item",
    "format_item_lines",
    "format_item_list",
    "get_item",
    "items_of_kind",
]

#: 物品大类
ITEM_KINDS: tuple[str, ...] = (
    "武器",
    "护甲",
    "药水",
    "卷轴",
    "饰品",
    "工具",
    "奇物",
)

#: 稀有度（由低到高）
ITEM_RARITIES: tuple[str, ...] = (
    "普通",
    "非常稀有",
    "稀有",
    "非常稀有",
    "传说",
)


@dataclass(frozen=True, slots=True)
class Item:
    """一件魔法物品。"""

    key: str
    cn: str  #: 中文名
    en: str  #: 英文名
    kind: str  #: 大类（:data:`ITEM_KINDS` 之一）
    rarity: str = "普通"  #: 稀有度（:data:`ITEM_RARITIES` 之一）
    requires_attunement: bool = False  #: 是否需要调谐
    #: 使用消耗：空 = 永久；"药水一瓶" / "卷轴一张" 这类按次扣
    consumes: str = ""
    #: 附魔值：武器 / 护甲的 +1 / +2 / +3
    bonus: int = 0
    #: 是否为「消耗型」（药水、卷轴）——用完即失效，不能重复用
    consumable: bool = False
    #: 药水效果：治疗量 / 抗性等（药水类才有）
    effect: str = ""
    #: 效果持续时间（药水 / 饰品）
    duration: str = ""
    #: 使用条件（需要某法术位、需要某能力、每天几次）
    requirement: str = ""
    desc: str = ""
    #: 额外别名（除 key / 中英文名外还能被认出来的写法）
    aliases: tuple[str, ...] = ()

    # —— 派生 ——

    @property
    def label(self) -> str:
        """列表里显示的一行。

        附魔值只出现一次：中文名里已经带「+1」时（如「+1 武器」）不再重复。
        """
        name = self.cn
        if self.bonus and not name.startswith("+"):
            name = f"+{self.bonus} {name}"
        return f"{name}（{self.kind}，{self.rarity}）"

    @property
    def usable_text(self) -> str:
        """使用条件一句话。"""
        bits: list[str] = []
        if self.requires_attunement:
            bits.append("需调谐")
        if self.requirement:
            bits.append(self.requirement)
        if self.consumes:
            bits.append(f"消耗 {self.consumes}")
        return "；".join(bits)

    def describe(self) -> str:
        """完整描述（DM 查询用）。"""
        lines = [f"{self.cn}（{self.en}）  {self.kind}，{self.rarity}"]
        if self.bonus:
            lines.append(f"附魔：+{self.bonus}")
        if self.effect:
            lines.append(f"效果：{self.effect}")
        if self.duration:
            lines.append(f"持续：{self.duration}")
        usable = self.usable_text
        if usable:
            lines.append(f"使用：{usable}")
        if self.desc:
            lines.append(self.desc)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 数据表
# --------------------------------------------------------------------------- #

def _item(row: dict[str, Any]) -> Item:
    """一件魔法物品（``res/dnd/items.yaml`` 的一项）。"""
    return Item(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        kind=str(row.get("kind", "奇物")),
        rarity=str(row.get("rarity", "普通")),
        requires_attunement=bool(row.get("requires_attunement", False)),
        consumes=str(row.get("consumes", "")),
        bonus=int(row.get("bonus", 0)),
        consumable=bool(row.get("consumable", False)),
        effect=str(row.get("effect", "")),
        duration=str(row.get("duration", "")),
        requirement=str(row.get("requirement", "")),
        desc=str(row.get("desc", "")),
        aliases=as_texts(row.get("aliases")),
    )


#: 魔法物品表：数值在 ``res/dnd/items.yaml``
ITEMS: tuple[Item, ...] = tuple(_item(row) for row in load_table("items"))


#: 中文名 / 英文名 / key → 物品
ITEM_MAP: dict[str, Item] = {}


def _register(item: Item) -> None:
    for name in (item.key, item.cn, item.en, *item.aliases):
        ITEM_MAP.setdefault(name, item)


for _item in ITEMS:
    _register(_item)


def get_item(value: Item | str) -> Item | None:
    """按 key / 中文名 / 英文名 / 别名取物品；取不到返回 ``None``。"""
    if isinstance(value, Item):
        return value
    return ITEM_MAP.get(value.strip())


def items_of_kind(kind: str) -> list[Item]:
    """按大类取物品（``"药水"`` / ``"武器"`` …）。"""
    return [item for item in ITEMS if item.kind == kind]


def format_item_lines(items: Iterable[Item], *, numbered: bool = True) -> str:
    """把物品列表排成一行一个的文本。"""
    out: list[str] = []
    for index, item in enumerate(items, start=1):
        prefix = f"{index}. " if numbered else "· "
        out.append(prefix + item.label)
    return "\n".join(out)


def format_item_list(items: Iterable[Item]) -> str:
    """按大类分组列出物品。"""
    buckets: dict[str, list[Item]] = {}
    for item in items:
        buckets.setdefault(item.kind, []).append(item)
    out: list[str] = []
    for kind in ITEM_KINDS:
        group = buckets.get(kind)
        if not group:
            continue
        out.append(f"【{kind}】")
        out.append(format_item_lines(group))
    return "\n".join(out)
