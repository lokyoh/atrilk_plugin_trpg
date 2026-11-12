"""《城主指南 (2024)》第三章范例卡（只读规则原文）。

参考 md 里的「范例危害物 / 范例陷阱 / 范例毒药 / 魔法疫病 / 攻城装备」全是 5etools
嵌入占位 HTML，没有可读正文；数据在 ``res/dnd/hazard_cards.yaml``，由
``参考/5etools-cn-cn2.0/data/`` 的四份 XDMG JSON 转来（60 张卡）。

这里只负责**查给 DM 看**，不参与任何结算：危害的 DC / 伤害 / 状态、毒药的豁免、
攻城装备的 AC / HP 都要 DM 按原文落到具体场景里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .datafiles import load_table

__all__ = [
    "HAZARD_CARDS",
    "HazardCard",
    "format_hazard_cards",
    "get_hazard_card",
    "hazard_card_summary",
]


@dataclass(frozen=True, slots=True)
class HazardCard:
    """一张第三章范例卡。"""

    cn: str
    en: str
    kind: str
    page: int
    rating: str
    trigger: str
    poison_types: str
    dc: int
    dcs: tuple[int, ...]
    damage: tuple[str, ...]
    ac: int
    hp: int
    price_gp: float
    text: str

    def describe(self) -> str:
        """给 DM 看的完整条目。"""
        lines = [f"{self.cn}（{self.en}）  {self.kind} / XDMG p.{self.page}"]
        if self.rating:
            lines.append(f"危害度：{self.rating}")
        if self.trigger:
            lines.append(f"触发：{self.trigger}")
        if self.poison_types:
            lines.append(f"毒药类型：{self.poison_types}")
        if self.price_gp:
            lines.append(f"单剂价格：{self.price_gp:g} GP")
        if self.dcs:
            lines.append("全文 DC：" + "、".join(str(x) for x in self.dcs))
        if self.damage:
            lines.append("伤害骰：" + "、".join(self.damage))
        if self.ac or self.hp:
            lines.append(f"物件：AC {self.ac} / HP {self.hp}")
        lines.append(f"效果：\n{self.text}" if self.text else "效果：（原文缺失）")
        return "\n".join(lines)


def _row(data: dict) -> HazardCard:
    return HazardCard(
        cn=str(data["cn"]),
        en=str(data.get("en") or ""),
        kind=str(data.get("kind") or ""),
        page=int(data.get("page") or 0),
        rating=str(data.get("rating") or ""),
        trigger=str(data.get("trigger") or ""),
        poison_types=str(data.get("poison_types") or ""),
        dc=int(data.get("dc") or 0),
        dcs=tuple(int(x) for x in (data.get("dcs") or [])),
        damage=tuple(str(x) for x in (data.get("damage") or [])),
        ac=int(data.get("ac") or 0),
        hp=int(data.get("hp") or 0),
        price_gp=float(data.get("price_gp") or 0),
        text=str(data.get("text") or ""),
    )


HAZARD_CARDS: tuple[HazardCard, ...] = tuple(
    _row(row) for row in load_table("hazard_cards")
)

_MAP: dict[str, HazardCard] = {}
for _card in HAZARD_CARDS:
    for _name in (_card.cn, _card.en):
        _MAP.setdefault(_name.strip(), _card)
    # 「棕霉菌（火）」这类写法也认
    _bare = re.sub(r"（.*?）|\(.*?\)", "", _card.cn).strip()
    if _bare:
        _MAP.setdefault(_bare, _card)


def get_hazard_card(name: str) -> HazardCard | None:
    """按中文名 / 英文名 / 去括号名取范例卡；取不到返回 ``None``。"""
    raw = str(name or "").strip()
    found = _MAP.get(raw)
    if found is not None:
        return found
    return _MAP.get(re.sub(r"（.*?）|\(.*?\)", "", raw).strip())


#: 类别清单请求 → kind。裸「陷阱 / 危害」不接管，交给 dm_tables 的建造表分支。
_KIND_WORDS: dict[str, str] = {
    "范例陷阱": "陷阱",
    "范例卡": "",
    "范例": "",
    "危害物": "危害",
    "环境效应": "危害",
    "环境": "危害",
    "毒药": "毒药",
    "毒素": "毒药",
    "疫病": "疫病",
    "疾病": "疫病",
    "魔法疫病": "疫病",
    "攻城装备": "攻城装备",
    "攻城器械": "攻城装备",
    "攻城": "攻城装备",
}


def hazard_card_summary() -> str:
    """一行统计，挂在 ``query_data(table=hazard)`` 的目录末尾。"""
    counts: dict[str, int] = {}
    for card in HAZARD_CARDS:
        counts[card.kind] = counts.get(card.kind, 0) + 1
    detail = " / ".join(f"{k} {v}" for k, v in counts.items())
    return (
        f"另有第三章范例卡共 {len(HAZARD_CARDS)} 张（{detail}）："
        "写名字查（棕霉菌 / 塌方 / 刺客之血 / 弩炮 / 疯笑病），"
        "或写「范例陷阱」「危害物」「毒药」「疫病」「攻城装备」列清单。"
    )


def format_hazard_cards(name: str) -> str | None:
    """查范例卡；不是范例卡请求时返回 ``None``，让 :mod:`.dm_tables` 走原路由。"""
    text = str(name or "").strip()
    if not text:
        return None
    card = get_hazard_card(text)
    if card is not None:
        return card.describe()
    kind = next((v for k, v in _KIND_WORDS.items() if k in text), None)
    if kind is None:
        return None
    cards = [c for c in HAZARD_CARDS if not kind or c.kind == kind]
    if not cards:
        return "这一类没有收录范例卡。"
    lines = [f"【第三章范例卡】{kind or '全部'}（{len(cards)} 张，写名字查详情）"]
    for group in dict.fromkeys(c.kind for c in cards):
        names = "、".join(c.cn for c in cards if c.kind == group)
        lines.append(f"· {group}：{names}")
    return "\n".join(lines)
