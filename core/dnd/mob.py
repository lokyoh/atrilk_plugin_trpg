"""《城主指南 (2024)》第三章「怪群」：免掷结算表与区域目标表（纯函数层）。

**口径是 2024，不是 2014**：2014 的「怪群免掷」是「每 X 次攻击命中一次」，
2024 给的是「一组怪里成功几只」的分数表（4/5/6/8/10 个一组），
三列分别是普通 / 具有优势 / 具有劣势下的**所需骰值**。别把两代的表混着用。

## 数值的唯一真相

``res/dnd/mob.yaml``：结算表 20 行、区域目标表 12 行、表头组大小与原文说明。
优势 / 劣势列里的「1-4」是「所需骰值落在这一档就查这一行」，
不是把骰值改成范围；原文没有对应的格子记 ``null``（劣势列最高 16-17）。

## 边界

- 一律不掷 d20：命中 / 豁免成功几只**由表算出**，只有区域密度修正掷 1d3。
- 不碰存档、不写战场、不自动扣血：伤害仍由 DM 用 ``attack`` / ``heal`` 结算，
  且回执只有 DM 看得到，结果必须显式写进叙述。
- 原文只给五种组大小；其它数量按表上分数**比例外推**（原文 L3654 的
  「12 × 1/6 = 2」就是这个用法），返回值里标 ``extrapolated`` 让上层说清楚。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from ..trpg.dice import roll
from .datafiles import load_mapping

__all__ = [
    "AreaResult",
    "MobResult",
    "area_targets",
    "format_area_query",
    "format_area_result",
    "format_area_table",
    "format_mob_notes",
    "format_mob_query",
    "format_mob_result",
    "format_mob_table",
    "group_sizes",
    "mob_hits",
    "needed_roll",
    "normalize_mode",
    "parse_mob_query",
    "row_for",
]

_MODE_CN = {"normal": "普通", "advantage": "具有优势", "disadvantage": "具有劣势"}
_SHAPE_KEYS = {
    "锥状": "cone",
    "cone": "cone",
    "立方": "cube",
    "cube": "cube",
    "圆形": "sphere",
    "球状": "sphere",
    "柱状": "sphere",
    "光环": "sphere",
    "sphere": "sphere",
    "线状": "line",
    "line": "line",
}
_SHAPE_CN = {"cone": "锥状", "cube": "立方", "sphere": "圆形", "line": "线状"}


@cache
def _rows(section: str) -> tuple[dict[str, Any], ...]:
    """分段读表；缺段直接抛 KeyError（打包问题不静默降级）。"""
    return load_mapping("mob")[section]


def group_sizes() -> tuple[int, ...]:
    """结算表表头的五个组大小，从数据表取。"""
    return tuple(int(row["size"]) for row in _rows("groups"))


def normalize_mode(text: str = "") -> str:
    """把「优势 / 劣势」等写法归一成表里的列名，其余一律当普通。"""
    key = str(text or "").strip().lower()
    if key in ("优势", "具有优势", "advantage", "adv", "优"):
        return "advantage"
    if key in ("劣势", "具有劣势", "disadvantage", "dis", "劣"):
        return "disadvantage"
    return "normal"


def _span(value: Any) -> tuple[int, int] | None:
    """表里的「9」/「1-4」/「—」→ 闭区间；没有对应值返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return (value, value)
    text = str(value).strip().replace("–", "-").replace("—", "-")
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    if text.isdigit():
        return (int(text), int(text))
    return None


def row_for(needed: int, mode: str = "normal") -> dict[str, Any] | None:
    """按所需骰值找行。**列选择不改变骰值本身**，这是 2024 表的读法。"""
    column = normalize_mode(mode)
    for row in _rows("resolution"):
        span = _span(row.get(column))
        if span and span[0] <= int(needed) <= span[1]:
            return row
    return None


def needed_roll(target: int, bonus: int = 0) -> int:
    """原文 L3590：所需骰值 = 目标数值 − 怪物加值。

    d20 只有 1..20，公式结果越界时钳到 1 / 20 —— **这是工程钳制，不是原文规则**；
    天然 1 必失 / 天然 20 必中仍由 DM 裁定，表里 1 与 20 两行是平均值近似。
    """
    return max(1, min(20, int(target) - int(bonus)))


@dataclass(frozen=True)
class MobResult:
    """一次免掷结算：几只成功，以及这个数是怎么来的。"""

    count: int
    needed: int
    mode: str
    hits: int
    group: int
    fraction: str
    extrapolated: bool


def mob_hits(count: int, needed: int, mode: str = "normal") -> MobResult | None:
    """给定怪物数量与所需骰值，算出成功几只；表里没有对应行返回 None。

    数量正好是表头组大小时直接读格；否则优先取「能整除它的最大组」
    （12 → 6 只一组，正是原文 L3654 的做法），不行才按最接近组比例外推。
    """
    if count < 1:
        raise ValueError("怪物数量至少 1 只")
    mode = normalize_mode(mode)
    row = row_for(needed, mode)
    if row is None:
        return None
    groups = group_sizes()
    exact = count in groups
    if exact:
        group = count
    else:
        divisors = [g for g in groups if count >= g and count % g == 0]
        group = max(divisors) if divisors else min(groups, key=lambda g: abs(g - count))
    numerator = int(row["hits"][group])
    hits = max(0, min(count, math.floor(count * numerator / group + 0.5)))
    return MobResult(
        count=count,
        needed=int(needed),
        mode=mode,
        hits=hits,
        group=group,
        fraction=f"{numerator}/{group}",
        extrapolated=not exact,
    )


@dataclass(frozen=True)
class AreaResult:
    """效应区域内目标数：基础值 + 密度修正（1d3），以及有没有被生物数封顶。"""

    shape: str
    size: str
    base: int
    modifier: int
    total: int
    capped: bool


def _size_match(cell: Any, size: str) -> bool:
    if not cell:
        return False
    text = str(cell).replace(" ", "")
    wanted = str(size).replace(" ", "").strip()
    if not wanted:
        return False
    if wanted in text:
        return True
    nums = set(re.findall(r"\d+", wanted))
    if not nums or not nums & set(re.findall(r"\d+", text)):
        return False
    # 写了「半径」就必须落在半径那一档；反过来「20尺」允许命中「半径20尺」
    return "半径" not in wanted or "半径" in text


def area_targets(
    shape: str,
    size: str,
    density: str = "",
    creatures: int = 0,
    rng: Any = None,
) -> AreaResult | None:
    """查区域目标表；拥挤 +1d3 / 分散 -1d3，且不能超过区域内实际生物数。"""
    column = _SHAPE_KEYS.get(str(shape or "").strip().lower())
    if column is None:
        return None
    base = 0
    label = ""
    for row in _rows("areas"):
        cell = row.get(column)
        if _size_match(cell, size):
            base = int(row["targets"])
            label = str(cell)
            break
    if not base:
        return None
    text = str(density or "")
    modifier = 0
    if any(word in text for word in ("拥挤", "密集", "挤在", "crowded")):
        modifier = roll("1d3", rng=rng).total
    elif any(word in text for word in ("分散", "稀疏", "spread")):
        modifier = -roll("1d3", rng=rng).total
    total = max(0, base + modifier)
    capped = bool(creatures and total > creatures)
    if capped:
        total = int(creatures)
    return AreaResult(column, label or str(size), base, modifier, total, capped)


# --------------------------------------------------------------------------- #
# 格式化：query_data 的结果（DM 侧 2500 字符截断，全表要留得住）
# --------------------------------------------------------------------------- #


def format_mob_table() -> str:
    """结算表 20 行全表，含优势 / 劣势列。"""
    groups = group_sizes()
    lines = [
        "【怪群结算】（DMG 2024 L3595-3618）"
        "所需骰值：普通 | 具有优势 | 具有劣势 → 成功数量 / 组大小",
        "所需骰值(普通) | 优势 | 劣势 | " + " | ".join(f"{g}个一组" for g in groups),
    ]
    for row in _rows("resolution"):
        cells = [
            str(row["normal"]),
            _span_text(row.get("advantage")),
            _span_text(row.get("disadvantage")),
        ]
        lines.append(
            " | ".join([*cells, *(f"{int(row['hits'][g])}/{g}" for g in groups)])
        )
    lines.append(
        "※ 「—」= 该列没有对应骰值；优势 / 劣势不是改骰值，是换一列找行。"
        "第一步：所需骰值 = 目标数值 − 怪物加值。"
    )
    lines.append(
        "※ 写法：query_data(table=怪群, name=8 15) 或 name=8 AC18 +3；"
        "加「优势 / 劣势」换列，加「伤害 4」给均值伤害合计。"
    )
    return "\n".join(lines)


def _span_text(value: Any) -> str:
    span = _span(value)
    if span is None:
        return "—"
    return str(value) if span[0] != span[1] else str(span[0])


def format_area_table() -> str:
    """区域目标表 12 行全表。"""
    lines = [
        "【效应区域内的目标】（DMG 2024 L3628-3644）"
        "区域形状与尺寸 → 目标数量",
        "锥状 | 立方 | 圆形 | 线状 | 目标数",
    ]
    for row in _rows("areas"):
        lines.append(
            " | ".join(
                [
                    str(row.get(key) or "—")
                    for key in ("cone", "cube", "sphere", "line")
                ]
                + [str(row["targets"])]
            )
        )
    lines.append(
        "※ 圆形列同时用于柱状、光环与球状；分散 -1d3、拥挤 +1d3，"
        "不能超过区域内生物数。DM 判断优先，倾向影响更多目标完全允许。"
    )
    lines.append(
        "※ 写法：query_data(table=效应区域, name=圆形 半径20尺 拥挤 12只)"
    )
    return "\n".join(lines)


def format_mob_notes(name: str = "") -> str:
    """原文说明段：留空列全部，给关键词出对应段。"""
    rows = _rows("notes")
    text = str(name or "").strip().lower()
    if text:
        for row in rows:
            if text == str(row["key"]).lower() or text in str(row["title"]):
                return f"【{row['title']}】{row['text']}"
    return "\n".join(f"【{row['title']}】{row['text']}" for row in rows)


def format_mob_result(result: MobResult, damage: int = 0) -> str:
    """把一次结算讲清楚：表上读到什么、算出几只、怎么结算。"""
    lines = [
        f"【怪群结算】{result.count} 只 × 所需骰值 {result.needed}"
        f"（{_MODE_CN[result.mode]}列）",
        f"表上 {result.group} 个一组 = {result.fraction}；"
        f"按 {result.count} 只算 → **{result.hits} 只成功**，"
        f"{result.count - result.hits} 只失败。",
    ]
    if result.extrapolated:
        lines.append(
            f"※ {result.count} 不是表头组大小，按 {result.fraction} 比例外推"
            "（原文 L3654「12 × 1/6 = 2」即此用法）；也可拆成 4/5/6/8/10 一组分别查。"
        )
    if damage > 0:
        lines.append(
            f"每次成功按均值 {damage} 点算 → 合计 {result.hits * damage} 点"
            "（原文小贴士：用怪物数据卡的平均伤害）。"
        )
    lines.append(
        "⚠️ 系统不自动扣血：命中 / 豁免与伤害请用 attack / heal 结算，"
        "并把数字显式写进叙述——DM 回执只有 DM 看得到。"
    )
    return "\n".join(lines)


def format_area_result(result: AreaResult) -> str:
    lines = [
        f"【效应区域内的目标】{_SHAPE_CN[result.shape]} {result.size}"
        f" → 表上 {result.base} 个目标",
    ]
    if result.modifier:
        word = "拥挤" if result.modifier > 0 else "分散"
        lines.append(f"1d3 = {abs(result.modifier)}（{word}）→ {result.total} 个目标")
    else:
        lines.append(f"未指定分散 / 拥挤 → {result.total} 个目标")
    if result.capped:
        lines.append("※ 已按区域内实际生物数封顶。")
    lines.append(
        "⚠️ DM 判断优先，倾向影响更多目标完全允许；系统不自动扣血，"
        "把结果显式写进叙述（DM 回执只有 DM 看得到）。"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 查询文本解析
# --------------------------------------------------------------------------- #


_TARGET_RE = re.compile(r"(?:ac|dc|目标(?:数值)?|豁免dc)\s*(\d+)", re.I)
_BONUS_RE = re.compile(r"(?:加值|调整值?|修正)\s*([+-]?\s*\d+)|([+-]\d+)")
_DAMAGE_RE = re.compile(r"(?:伤害|均值)\s*(\d+)")
_NUMBER_RE = re.compile(r"-?\d+")
_SHAPE_RE = re.compile(
    r"(锥状|立方|圆形|球状|柱状|光环|线状|cone|cube|sphere|line)", re.I
)
_SIZE_RE = re.compile(r"(半径\s*\d+\s*尺|\d+\s*[-/]\s*\d+\s*尺|\d+\s*尺)")
_CREATURES_RE = re.compile(r"(\d+)\s*只")


def parse_mob_query(text: str) -> tuple[int, int, str, int] | None:
    """解析「8 15」「8 AC18 +3 优势」「12 18 伤害 4」→ (数量, 所需骰值, 列, 均值伤害)。"""
    raw = str(text or "").strip()
    match = re.match(r"^\s*(\d+)\s*(?:只|个|名)?", raw)
    if not match:
        return None
    count = int(match.group(1))
    mode = "advantage" if "优势" in raw and "劣势" not in raw else "normal"
    if "劣势" in raw:
        mode = "disadvantage"
    damage_match = _DAMAGE_RE.search(raw)
    damage = int(damage_match.group(1)) if damage_match else 0
    target_match = _TARGET_RE.search(raw)
    bonus_match = _BONUS_RE.search(raw)
    if target_match:
        bonus = 0
        if bonus_match:
            bonus = int((bonus_match.group(1) or bonus_match.group(2)).replace(" ", ""))
        return (count, needed_roll(int(target_match.group(1)), bonus), mode, damage)
    numbers = [int(n) for n in _NUMBER_RE.findall(raw)]
    # 第 1 个数是数量；伤害 / 1d3 之类尾数不当骰值，只取紧随其后的那个
    if len(numbers) < 2:
        return None
    return (count, max(1, min(20, numbers[1])), mode, damage)


def format_area_query(text: str) -> str:
    """``query_data(table=效应区域, name=…)`` 的入口。"""
    raw = str(text or "").strip()
    shape_match = _SHAPE_RE.search(raw)
    size_match = _SIZE_RE.search(raw)
    shape = shape_match.group(1) if shape_match else ""
    if not shape and ("球" in raw or "圆" in raw or "柱状" in raw):
        shape = "sphere"
    if not (shape and size_match):
        return format_area_table()
    creatures_match = _CREATURES_RE.search(raw)
    creatures = int(creatures_match.group(1)) if creatures_match else 0
    result = area_targets(shape, size_match.group(1), raw, creatures=creatures)
    if result is None:
        return f"表里没有「{shape} {size_match.group(1)}」这一档。\n" + format_area_table()
    return format_area_result(result)


def format_mob_query(name: str = "") -> str:
    """``query_data(table=怪群 / 效应区域 / mob, name=…)`` 的统一入口。

    留空 / 「结算」→ 结算全表；「区域 / 效应」→ 区域表或一次区域计算；
    「规则 / 小贴士 / 范例 / 边界」→ 原文说明；带数量的写法 → 免掷结算。
    """
    text = str(name or "").strip()
    if not text or text in ("结算", "怪群", "怪群结算", "平均结果", "全表"):
        return format_mob_table()
    if any(word in text for word in ("区域", "效应", "目标数", "aoe")) or "尺" in text:
        return format_area_query(text)
    if any(word in text for word in ("规则", "步骤", "小贴士", "范例", "边界", "说明")):
        return format_mob_notes(text)
    parsed = parse_mob_query(text)
    if parsed is None:
        return (
            "没认出写法。可用：name=8 15（8 只、所需骰值 15）、"
            "name=8 AC18 +3、name=12 18 伤害 4 劣势、"
            "name=圆形 半径20尺 拥挤 12只、name=范例。\n" + format_mob_table()
        )
    count, needed, mode, damage = parsed
    result = mob_hits(count, needed, mode)
    if result is None:
        return (
            f"所需骰值 {needed} 在「{_MODE_CN[mode]}」列没有对应行"
            "（劣势列最高只到 16-17）。请逐只掷骰，或改查普通列；"
            "原文没有这一档，系统不替你补。\n" + format_mob_table()
        )
    return format_mob_result(result, damage)
