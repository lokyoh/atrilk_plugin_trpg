"""《城主指南 (2024)》第三章「聚居地」规模表与六张随机表（纯函数层）。

原文 ``参考/城主指南 (2024).md`` L4385-4525；数值唯一真相是
``res/dnd/settlements.yaml``。

## 边界

- **规模表不是随机表**：村庄 / 小镇 / 城市由 DM 按设定选，表只给人口范围与
  「可能出售的最昂贵物品价值」的交易限额；原文 L4387 允许按特殊情况调整。
- 六张随机表由系统掷骰（1d20 / 1d12），酒馆名称按 L4501 脚注**两段各掷一次
  1d20**；掷出来的只是素材，**怎么把它写进世界仍由 DM 裁定**。
- 不碰存档、不写世界库；DM 回执只有 DM 看得到，聚落细节要显式写进叙述。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

from ..trpg.dice import roll
from .datafiles import load_mapping

__all__ = [
    "SettlementRoll",
    "format_settlement_card",
    "format_settlement_query",
    "format_size_table",
    "format_table",
    "get_size",
    "roll_table",
    "roll_tavern_name",
    "size_for_population",
    "table_names",
]


@cache
def _rows(section: str) -> tuple[dict[str, Any], ...]:
    return load_mapping("settlements")[section]


def _meta(text: str) -> dict[str, Any] | None:
    key = str(text or "").strip().lower()
    for row in _rows("meta"):
        names = [str(row["key"]), str(row["cn"])]
        names += [str(a) for a in row.get("aliases") or ()]
        if key in {n.strip().lower() for n in names if n}:
            return row
    return None


def table_names() -> tuple[str, ...]:
    return tuple(str(row["cn"]) for row in _rows("meta"))


def get_size(text: str) -> dict[str, Any] | None:
    key = str(text or "").strip().lower()
    for row in _rows("size"):
        names = [str(row["cn"])] + [str(a) for a in row.get("aliases") or ()]
        if key in {n.strip().lower() for n in names if n}:
            return row
    return None


def size_for_population(population: int) -> dict[str, Any] | None:
    for row in _rows("size"):
        maximum = row.get("population_max")
        if int(population) >= int(row["population_min"]) and (
            maximum is None or int(population) <= int(maximum)
        ):
            return row
    return None


def _pick(section: str, value: int) -> str:
    for row in _rows(section):
        if int(row["low"]) <= value <= int(row["high"]):
            return str(row["text"])
    raise ValueError(f"{section} 表没有覆盖骰值 {value}")


@dataclass(frozen=True)
class SettlementRoll:
    table: str
    value: int
    text: str


def _die(key: str) -> str:
    """酒馆的 ``1d20 × 2`` 是「掷两次」的记法，不是可解析的骰式。"""
    return "1d20" if key == "tavern" else str(_meta(key)["dice"])


def roll_table(text: str, rng: Any = None) -> SettlementRoll | None:
    """按表名 / 别名掷一张表；酒馆走 :func:`roll_tavern_name`。"""
    meta = _meta(text)
    if meta is None:
        return None
    key = str(meta["key"])
    if key == "tavern":
        first_value = roll("1d20", rng=rng).total
        second_value = roll("1d20", rng=rng).total
        first = _pick("tavern_first", first_value)
        second = _pick("tavern_second", second_value)
        return SettlementRoll(
            str(meta["cn"]),
            first_value,
            f"{first}{second}（1d20={first_value}/{second_value}）",
        )
    value = roll(_die(key), rng=rng).total
    return SettlementRoll(str(meta["cn"]), value, _pick(key, value))


def roll_tavern_name(rng: Any = None) -> tuple[str, str, str]:
    """原文 L4501：名称的两个部分**分别**掷 1d20。"""
    first_value = roll("1d20", rng=rng).total
    second_value = roll("1d20", rng=rng).total
    first = _pick("tavern_first", first_value)
    second = _pick("tavern_second", second_value)
    return (first, second, f"{first}{second}")


# --------------------------------------------------------------------------- #
# 格式化
# --------------------------------------------------------------------------- #


def format_size_table() -> str:
    lines = ["【基于规模的聚落】（DMG 2024 L4389-4394）聚居地 | 人口范围 | 交易限额"]
    for row in _rows("size"):
        lines.append(
            f"{row['cn']} | {row['population_text']} | {row['trade_limit_gp']} GP"
        )
    lines.append(
        "※ 规模由 DM 按设定选，不是随机表；特殊情况可调整（村里恰好有炼金师也可能买得到药水）。"
    )
    return "\n".join(lines)


def format_table(text: str) -> str:
    """列一张表的全文；认不出给总览。"""
    meta = _meta(text)
    if meta is None:
        return format_settlement_query("")
    key = str(meta["key"])
    lines = [f"【{meta['cn']}】（DMG 2024 第三章，{meta['dice']}）"]
    if key == "tavern":
        first = _rows("tavern_first")
        second = _rows("tavern_second")
        for left, right in zip(first, second, strict=True):
            lines.append(f"{left['low']} | {left['text']} | {right['text']}")
        lines.append("※ 两部分各掷一次 1d20，拼起来就是店名。")
    else:
        for row in _rows(key):
            span = str(row["low"]) if row["low"] == row["high"] else f"{row['low']}-{row['high']}"
            lines.append(f"{span} | {row['text']}")
    return "\n".join(lines)


def format_settlement_card(size_name: str = "", rng: Any = None) -> str:
    """掷出一整张聚落素材卡：规模（DM 指定）+ 六张随机表。"""
    lines: list[str] = []
    if size_name:
        row = get_size(size_name)
        if row is None:
            return (
                f"没认出聚落规模「{size_name}」。可用："
                + "、".join(str(r["cn"]) for r in _rows("size"))
                + "\n" + format_size_table()
            )
        lines.append(
            f"【聚落卡】{row['cn']}（{row['population_text']} 人，"
            f"交易限额 {row['trade_limit_gp']} GP）"
        )
    else:
        lines.append("【聚落卡】规模未指定（村庄 / 小镇 / 城市由 DM 定，见规模表）")
    for meta in _rows("meta"):
        result = roll_table(str(meta["key"]), rng=rng)
        if result is None:
            continue
        if meta["key"] == "tavern":
            # 两段各掷一次，text 里已经带两个骰值，别只显示主骰值误导 DM
            lines.append(f"· {result.table}（{meta['dice']}）：{result.text}")
        else:
            lines.append(
                f"· {result.table}（{meta['dice']} = {result.value}）：{result.text}"
            )
    lines.append(
        "⚠️ 掷出来的只是素材，聚落叫什么、发生过什么由你裁定；这份回执只有 DM 看得到，"
        "细节要显式写进叙述，玩家才会知道。"
    )
    return "\n".join(lines)


def format_settlement_query(name: str = "") -> str:
    """``query_data(table=聚居地 / 聚落, name=…)`` 的入口。

    留空给总览；「规模」给规模表；「人口 3000」按人口查规模；
    「掷 重要特征」掷单表；「重要特征」列全表；「掷 小镇 / 聚落卡 小镇」给整张卡。
    """
    text = str(name or "").strip()
    if not text:
        return (
            format_size_table()
            + "\n【随机表】"
            + "、".join(f"{row['cn']}（{row['dice']}）" for row in _rows("meta"))
            + "\n※ 写法：name=掷 小镇（整张卡）/ 掷 酒馆 / 掷 灾难 / 重要特征（列全表）/ 人口 3000"
        )
    if "规模" in text or text in ("人口", "交易限额"):
        return format_size_table()
    if text.startswith("人口"):
        digits = "".join(c for c in text if c.isdigit())
        if digits:
            row = size_for_population(int(digits))
            if row is None:
                return f"人口 {digits} 不在规模表里。"
            return (
                f"人口 {digits} → **{row['cn']}**，交易限额 "
                f"{row['trade_limit_gp']} GP（原文 L4389-4394）。\n"
                + format_size_table()
            )
    plain = text
    for prefix in ("掷 ", "掷", "聚落卡 ", "聚落卡", "卡 "):
        if plain.startswith(prefix):
            plain = plain[len(prefix) :].strip()
            break
    is_roll = plain != text
    if is_roll and get_size(plain):
        return format_settlement_card(plain)
    if is_roll and _meta(plain):
        result = roll_table(plain)
        return (
            f"【{result.table}】掷出 {result.value} → **{result.text}**"
            "（素材，怎么用由 DM 裁定；回执只有 DM 看得到）。"
        )
    if _meta(text):
        return format_table(text)
    return (
        f"没认出「{text}」。可用表名："
        + "、".join(table_names())
        + "；规模：村庄 / 小镇 / 城市。\n" + format_size_table()
    )
