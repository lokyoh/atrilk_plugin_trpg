"""《城主指南 (2024)》魔法物品详情表（A-Z）。

数据在 ``res/dnd/magic_items.yaml``，由 5etools 中文包的 XDMG 条目转来。
参考 md 的 A-Z 正文是嵌入占位 HTML、没有可读文本，所以**不用 md 作数据源**。

和 :mod:`.items` 的分工：

- :mod:`.items` 是插件自建 / 战役自带的 28 件「可玩近似值」，会结算、能进装备栏；
- 本模块是**只读的规则原文**，给 DM 查「这件东西到底做什么」，不参与任何结算。

查询时先查前者再回退到这里（见 ``tools._query_item``）。中文包另有
``search/index-item.json``（只有名字/页码）与 ``foundry-items.json``（结构化机械数据）；
后者独有的 18 条已并入本表并在 text 里标明来源。2026-11-20 复核：20 张随机表的
393 个唯一名字里仍有 60 个查不到对应条目（8 个是 +1/+2/+3 泛称组名，其余为译名
差异或中文包缺条目），本表 538 条则每条都有正文。查不到时如实回 ``None``，不伪造。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .datafiles import as_texts, load_table

__all__ = [
    "MAGIC_ITEMS",
    "MagicItem",
    "get_magic_item",
    "magic_item_count",
    "normalize_item_name",
]


@dataclass(frozen=True, slots=True)
class MagicItem:
    """一件 DMG 2024 魔法物品的原文条目。"""

    cn: str
    en: str
    rarity: str
    kind: str
    attunement: str
    page: int
    text: str
    aliases: tuple[str, ...] = ()
    loot_tables: tuple[str, ...] = ()

    def describe(self) -> str:
        """给 DM 看的完整条目（原文已剥 5etools 标签）。"""
        lines = [f"{self.cn}（{self.en}）  {self.rarity} / {self.kind}"]
        if self.attunement:
            lines.append(f"同调：{self.attunement}")
        lines.append(f"出处：XDMG p.{self.page}" if self.page else "出处：XDMG")
        if self.text:
            lines.append(self.text)
        return "\n".join(lines)


def _row(data: dict) -> MagicItem:
    return MagicItem(
        cn=str(data["cn"]),
        en=str(data.get("en") or ""),
        rarity=str(data.get("rarity") or "未标注"),
        kind=str(data.get("kind") or "其他"),
        attunement=str(data.get("attunement") or ""),
        page=int(data.get("page") or 0),
        text=str(data.get("text") or ""),
        aliases=as_texts(data.get("aliases")),
        loot_tables=as_texts(data.get("loot_tables")),
    )


MAGIC_ITEMS: tuple[MagicItem, ...] = tuple(
    _row(row) for row in load_table("magic_items")
)

#: 中文名 / 英文名 / 别名 → 条目。先到先得（组条目排在具体变体前面）
_MAP: dict[str, MagicItem] = {}
for _entry in MAGIC_ITEMS:
    for _name in (_entry.cn, _entry.en, *_entry.aliases):
        _MAP.setdefault(_name.strip(), _entry)


def normalize_item_name(name: str) -> tuple[str, ...]:
    """一个名字的各种写法，用于回查随机表掷出的名字。

    随机表里的写法和 A-Z 条目常有差别：括号里的环阶 / 子类型、「，+1」与
    「+1 」前缀互换。这里只做**无损**的写法归一，不做模糊匹配——查不到就
    如实说查不到，别拿同组别的变体描述糊弄 DM。
    """
    raw = str(name or "").strip()
    out = {raw}
    bare = re.sub(r"（.*?）|\(.*?\)", "", raw).strip()
    if bare:
        out.add(bare)
    tail = re.match(r"^(.+?)，\+(\d+)$", bare or raw)
    if tail:
        out.add(f"+{tail.group(2)} {tail.group(1)}")
        out.add(tail.group(1))
    head = re.match(r"^\+(\d+) (.+)$", bare or raw)
    if head:
        out.add(f"{head.group(2)}，+{head.group(1)}")
        out.add(head.group(2))
    return tuple(n for n in out if n)


def get_magic_item(name: str) -> MagicItem | None:
    """按中文名 / 英文名 / 别名取官方条目；取不到返回 ``None``。"""
    for candidate in normalize_item_name(name):
        found = _MAP.get(candidate)
        if found is not None:
            return found
    return None


def magic_item_count() -> int:
    """官方条目数（给查询清单的提示用）。"""
    return len(MAGIC_ITEMS)
