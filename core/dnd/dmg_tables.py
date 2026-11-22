"""DMG 2024 的纯数据表：维护费用 / 冒险随机表 / 位面 / 价目 / 词条 / 赠礼。

数值在 ``res/dnd/dmg_tables.yaml`` 与 ``res/dnd/supernatural_gifts.yaml``，由
``参考/5etools-cn-cn2.0/data/book/book-dmg.json`` 与 ``rewards.json`` 机械转录。
本模块只做查表、掷骰与排版：**不碰存档、不改角色卡、不挂状态**。

表的中文名、骰型与列名放在这里而不是 yaml：yaml 只存数值，改措辞/加别名不用动数据。
"""

from __future__ import annotations

from typing import Any

from ..trpg.dice import roll
from .datafiles import load_mapping

__all__ = [
    "DMG_TABLES",
    "format_dmg_query",
    "format_gift",
    "format_table",
    "format_table_index",
    "get_gift",
    "get_table",
    "roll_table_row",
]

#: key -> (中文名, 骰型, 列名)。骰型为空表示不是随机表。
DMG_TABLES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "upkeep": ("维护费用", "", ("词条", "每日总花费", "熟练雇工", "新手雇工")),
    "training_levels": ("受训升级", "", ("达到等级", "训练时间", "训练花费")),
    "adventure_openings": ("冒险开场", "d12", ("简介",)),
    "adventure_climaxes": ("冒险高潮", "d12", ("高潮",)),
    "adventure_villains": ("冒险反派", "d20", ("反派",)),
    "adventure_allies": ("冒险盟友", "d12", ("盟友",)),
    "adventure_patrons": ("冒险赞助者", "d20", ("雇主",)),
    "outer_planes": ("外层位面", "", ("外层位面", "阵营")),
    "astral_pools": ("星界彩池", "d20", ("位面", "池的颜色")),
    "ethereal_curtains": ("以太帷幕", "d8", ("位面", "帷幕颜色")),
    "ethereal_storms": ("以太飓风", "d20", ("效应",)),
    "outlands_gates": ("外域门镇", "", ("城镇", "门扉目的地")),
    "magic_item_prices": ("魔法物品价目", "", ("稀有度", "角色等级", "价值")),
    "special_creator": ("特殊特性·制作者", "d20", ("制作者或预设使用者",)),
    "special_history": ("特殊特性·历史", "d8", ("历史",)),
    "special_minor": ("特殊特性·次级词条", "d20", ("次级词条",)),
    "special_quirks": ("特殊特性·怪癖", "d12", ("怪癖",)),
    "artifact_benefit_minor": ("神器·次要增益", "d100", ("词条",)),
    "artifact_benefit_major": ("神器·主要增益", "d100", ("词条",)),
    "artifact_detriment_minor": ("神器·次要减益", "d100", ("词条",)),
    "artifact_detriment_major": ("神器·主要减益", "d100", ("词条",)),
    "sentient_communication": ("智能物品·沟通方式", "d100", ("沟通方式",)),
    "sentient_senses": ("智能物品·感官", "d4", ("感官",)),
    "sentient_alignment": ("智能物品·阵营", "d100", ("阵营",)),
    "sentient_goal": ("智能物品·目标", "d10", ("目的",)),
}

#: 查询用词 -> 表 key。长的在前，避免「次要增益」被「增益」抢先。
_TABLE_ALIASES: tuple[tuple[str, str], ...] = (
    ("维护费用", "upkeep"),
    ("周期开支", "upkeep"),
    ("要塞维护", "upkeep"),
    ("维护", "upkeep"),
    ("受训升级", "training_levels"),
    ("升级表", "training_levels"),
    ("训练", "training_levels"),
    ("冒险开场", "adventure_openings"),
    ("开场", "adventure_openings"),
    ("冒险高潮", "adventure_climaxes"),
    ("高潮", "adventure_climaxes"),
    ("冒险反派", "adventure_villains"),
    ("反派", "adventure_villains"),
    ("冒险盟友", "adventure_allies"),
    ("盟友", "adventure_allies"),
    ("冒险赞助者", "adventure_patrons"),
    ("赞助者", "adventure_patrons"),
    ("雇主", "adventure_patrons"),
    ("外层位面", "outer_planes"),
    ("星界彩池", "astral_pools"),
    ("彩池", "astral_pools"),
    ("以太帷幕", "ethereal_curtains"),
    ("以太飓风", "ethereal_storms"),
    ("外域门镇", "outlands_gates"),
    ("门镇", "outlands_gates"),
    ("魔法物品价目", "magic_item_prices"),
    ("物品价目", "magic_item_prices"),
    ("价目", "magic_item_prices"),
    ("稀有度价值", "magic_item_prices"),
    ("制作者", "special_creator"),
    ("特殊特性历史", "special_history"),
    ("特殊特性·历史", "special_history"),
    ("次级词条", "special_minor"),
    ("特殊特性", "special_minor"),
    ("怪癖", "special_quirks"),
    ("神器次要增益", "artifact_benefit_minor"),
    ("次要增益", "artifact_benefit_minor"),
    ("神器主要增益", "artifact_benefit_major"),
    ("主要增益", "artifact_benefit_major"),
    ("神器次要减益", "artifact_detriment_minor"),
    ("次要减益", "artifact_detriment_minor"),
    ("神器主要减益", "artifact_detriment_major"),
    ("主要减益", "artifact_detriment_major"),
    ("沟通方式", "sentient_communication"),
    ("智能物品感官", "sentient_senses"),
    ("感官", "sentient_senses"),
    ("智能物品阵营", "sentient_alignment"),
    ("智能物品目标", "sentient_goal"),
)

#: 总称 -> 一组表。命中时只列清单（全文太长，写具体表名再看）。
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("神器词条", ("artifact_benefit_minor", "artifact_benefit_major",
                "artifact_detriment_minor", "artifact_detriment_major")),
    ("智能物品", ("sentient_communication", "sentient_senses",
                "sentient_alignment", "sentient_goal")),
    ("特殊特性", ("special_creator", "special_history",
                "special_minor", "special_quirks")),
    ("冒险表", ("adventure_openings", "adventure_climaxes", "adventure_villains",
              "adventure_allies", "adventure_patrons")),
)

_RULE_WORDS = ("恐惧", "惊恐", "心慌", "受训")
_GIFT_WORDS = ("赠礼", "祝福", "护咒")


def _rows(key: str) -> list[dict[str, Any]]:
    return list(load_mapping("dmg_tables").get(key, []))


def _gift_rows() -> list[dict[str, Any]]:
    return list(load_mapping("supernatural_gifts").get("gifts", []))


def _rule_rows() -> list[dict[str, Any]]:
    return list(load_mapping("supernatural_gifts").get("rules", []))


def get_table(name: str) -> str | None:
    """按中文名 / 别名找表 key；找不到返回 ``None``。"""
    text = str(name or "").strip()
    if not text:
        return None
    for key, (cn, _dice, _cols) in DMG_TABLES.items():
        if text in (cn, key):
            return key
    for word, key in _TABLE_ALIASES:
        if word in text:
            return key
    return None


def format_table_index() -> str:
    """表格目录：按用途分组，DM 一眼看到能查什么。"""
    groups = (
        ("冒险与休整", ("upkeep", "training_levels", "adventure_openings", "adventure_climaxes",
                    "adventure_villains", "adventure_allies", "adventure_patrons")),
        ("位面", ("outer_planes", "astral_pools", "ethereal_curtains",
                 "ethereal_storms", "outlands_gates")),
        ("宝藏", ("magic_item_prices", "special_creator", "special_history",
                 "special_minor", "special_quirks", "artifact_benefit_minor",
                 "artifact_benefit_major", "artifact_detriment_minor",
                 "artifact_detriment_major", "sentient_communication",
                 "sentient_senses", "sentient_alignment", "sentient_goal")),
    )
    lines = ["《城主指南 (2024)》明文表（`query_data(table=dmg)`）："]
    for title, keys in groups:
        lines.append(f"· {title}：")
        for key in keys:
            cn, dice, cols = DMG_TABLES[key]
            shape = f"{dice} · {'/'.join(cols)}" if dice else "/".join(cols)
            lines.append(f"  - {cn}（{shape}）")
    lines.append("· 超自然赠礼：7 条祝福 + 12 条护咒（写「赠礼」「祝福」「护咒」）")
    lines.append("· 心慌与惊恐：「恐惧」「惊恐」（2024 版 DC 由你按情况设定）")
    return "\n".join(lines)


def roll_table_row(key: str, rng: Any = None) -> tuple[int, list[str]]:
    """掷一次随机表，返回（骰值, 该行列）。非随机表返回 (0, 首行)。"""
    _cn, dice, _cols = DMG_TABLES[key]
    rows = _rows(key)
    if not rows:
        raise ValueError(f"表里没有数据：{key}")
    if not dice:
        return 0, list(rows[0].get("cols") or [])
    value = roll(dice, rng=rng).total
    for row in rows:
        lo, hi = int(row.get("lo", 0)), int(row.get("hi", 0))
        if lo <= value <= hi:
            return value, list(row.get("cols") or [])
    raise ValueError(f"{DMG_TABLES[key][0]} 没有覆盖骰值 {value}")


def format_table(name: str, *, roll_it: bool = False, rng: Any = None) -> str:
    """把一张表排成 DM 可读文本；``roll_it`` 为真时只掷一行。"""
    key = get_table(name)
    if key is None:
        return format_table_index()
    cn, dice, cols = DMG_TABLES[key]
    rows = _rows(key)
    if not rows:
        return f"{cn}：数据表为空。"
    if roll_it and dice:
        value, cells = roll_table_row(key, rng)
        return f"{cn}（{dice} = {value}）：" + " | ".join(cells)
    lines = [f"{cn}（{dice or '非随机表'}，{len(rows)} 行）"]
    lines.append(" | ".join(cols))
    for row in rows:
        cells = list(row.get("cols") or [])
        if dice:
            lo, hi = int(row.get("lo", 0)), int(row.get("hi", 0))
            head = str(lo) if lo == hi else f"{lo}-{hi}"
            lines.append(" | ".join([head, *cells]))
        else:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def format_group(name: str) -> str | None:
    """总称（神器词条 / 智能物品 / 特殊特性 / 冒险表）→ 分组清单。"""
    text = str(name or "").strip()
    for word, keys in _GROUPS:
        if word in text:
            lines = [f"{word}（{len(keys)} 张表）："]
            for key in keys:
                cn, dice, _cols = DMG_TABLES[key]
                lines.append(f"· {cn}（{dice or '非随机表'}，{len(_rows(key))} 行）")
            lines.append("写具体表名看全文；随机表可写「掷 <表名>」。")
            return "\n".join(lines)
    return None


def format_gift(gift: dict[str, Any]) -> str:
    return f"{gift.get('cn', '')}（{gift.get('kind', '')}）\n{gift.get('text', '')}"


def get_gift(name: str) -> dict[str, Any] | None:
    text = str(name or "").strip()
    if not text:
        return None
    for gift in _gift_rows():
        cn = str(gift.get("cn", ""))
        if text == cn or (cn and cn in text) or (text in cn and len(text) >= 2):
            return gift
    return None


def format_gift_list(kind: str = "") -> str:
    rows = _gift_rows()
    if kind:
        rows = [g for g in rows if g.get("kind") == kind]
    lines = [f"超自然赠礼（{kind or '全部'}，{len(rows)} 条）："]
    for gift in rows:
        lines.append(f"· {gift['cn']}：{str(gift.get('text', ''))[:60]}…")
    lines.append("写具体名字看全文。赠礼与护咒都是 DM 发放的奖励，系统不自动挂效果。")
    return "\n".join(lines)


def format_dmg_query(name: str = "", *, rng: Any = None) -> str:
    """``query_data(table=dmg)`` 的统一入口。"""
    text = str(name or "").strip()
    if not text or text in ("目录", "表", "清单", "index"):
        return format_table_index()

    group = format_group(text)
    if group is not None:
        return group

    if any(word in text for word in _GIFT_WORDS):
        gift = get_gift(text)
        if gift is not None and text not in ("赠礼", "祝福", "护咒"):
            return format_gift(gift)
        return format_gift_list("祝福" if "祝福" in text else "护咒" if "护咒" in text else "")

    if any(word in text for word in _RULE_WORDS):
        rules = _rule_rows()
        hit = next((r for r in rules if str(r.get("cn", "")) in text), None)
        if hit is not None:
            body = f"{hit['cn']}\n{hit.get('text', '')}"
            if hit["cn"] == "受训升级":
                body += "\n\n" + format_table("training_levels")
            return body
        return "\n\n".join(f"{r['cn']}\n{r.get('text', '')}" for r in rules)

    gift = get_gift(text)
    if gift is not None:
        return format_gift(gift)

    roll_it = any(word in text for word in ("掷", "随机", "骰"))
    return format_table(text, roll_it=roll_it, rng=rng)
