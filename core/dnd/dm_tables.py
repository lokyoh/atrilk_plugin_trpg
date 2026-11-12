"""《城主指南 (2024)》DM 工具箱的明文数值表与掷表。

两张数据表：``res/dnd/dm_tables.yaml``（门 / 陷阱 / 即兴伤害 / 宝石 / 宝藏）
与 ``res/dnd/magic_item_tables.yaml``（第七章随机魔法物品 20 张 1d100 表）。
这里负责三件事：

1. **查表**：门 / 锁 / 暗门 / 吊闸门、建造陷阱与危害度、即兴伤害与伤害严重性；
2. **掷表**：个体宝藏、库藏宝藏、任务奖励、宝石、艺术品、随机魔法物品；
3. **给 DM 看**：把上面两类格式化成 ``query_data`` 能直接回给模型的文本。

按项目纪律，这里是**纯函数层**：不碰存档、不调模型、不 import agents。
战斗结算的金币部分（:func:`roll_individual`）由 :mod:`.settlement` 调用；
库藏宝藏按 DMG 明文「约每回游戏一次」，**不**接进每场自动结算，交给 DM 按剧情发放。

:func:`roll_treasure` 没有单独做成 DM 工具：查表 / 掷表与
``query_data(table=treasure, name=掷 魔法物品 …)`` 共用入口，DM 不必多记一个工具名。
（当时清单 4864 / 5000 余量不够；基线抬到 6000 后仍不改这个决定——零提示词增量更值。）
"""

from __future__ import annotations

import re
from typing import Any

from ..trpg.dice import roll
from .datafiles import load_mapping

__all__ = [
    "COINS_PER_POUND",
    "format_hazard",
    "format_improvised",
    "format_magic_items",
    "format_treasure",
    "roll_art",
    "roll_gem",
    "roll_hoard",
    "roll_individual",
    "roll_magic_rarity",
    "roll_magic_theme",
    "roll_quest",
    "roll_treasure",
    "table",
]

#: 每五十枚任意币种的钱币合一磅重（DMG 2024 L9007）
COINS_PER_POUND = 50

_TABLES = load_mapping("dm_tables")
_MAGIC_TABLES = load_mapping("magic_item_tables")

#: GP / PP → 角色卡钱包与战利品文案用的中文币种名
_COIN_CN = {"GP": "金", "PP": "铂"}

#: 第七章四张宝藏主题表（L20780：不属任何主题时掷 1d4）
_MAGIC_THEMES = ("奥秘", "武备", "器具", "圣物")
#: DMG 2024 六档里随机表用到的五档（神器不在随机表里，L9342 已说明）
_MAGIC_RARITIES = ("普通", "非普通", "珍稀", "极珍稀", "传说")
#: 2014 旧译名 → 2024 档位名（只做参数兼容，不是规则档位）
_RARITY_ALIASES = {
    "稀有": "珍稀",
    "非常稀有": "极珍稀",
    "传奇": "传说",
    "通用": "普通",
    "通用语": "普通",
}


def table(name: str) -> tuple[dict[str, Any], ...]:
    """取一张分段表；段名见 ``res/dnd/dm_tables.yaml``。"""
    return _TABLES.get(name, ())


def _in_band(spec: str, value: float) -> bool:
    """``"0-4"`` / ``"17+"`` / ``"5-10"`` 这类区间是否包含 value。"""
    text = str(spec).strip()
    if text.endswith("+"):
        return value >= float(text[:-1])
    if "-" in text:
        low, high = text.split("-", 1)
        return float(low) <= value <= float(high)
    return value == float(text)


def _band_for(section: str, key: str, value: float) -> dict[str, Any]:
    for row in table(section):
        if _in_band(str(row[key]), value):
            return row
    raise KeyError(f"{section} 没有覆盖 {value} 的档位")


def _number(text: str) -> int | None:
    """从「宝石 100」「掷 艺术品 750」里抽出数字。"""
    hit = re.search(r"\d+", str(text or ""))
    return int(hit.group()) if hit else None


# --------------------------------------------------------------------------- #
# 掷表
# --------------------------------------------------------------------------- #


def _roll_amount(row: dict[str, Any], rng: Any = None) -> tuple[str, int]:
    """按 ``dice × multiplier`` 掷一次，返回 ``(中文币种, 数量)``。"""
    amount = roll(str(row["dice"]), rng=rng).total * int(row["multiplier"])
    return _COIN_CN[str(row["unit"])], max(0, amount)


def roll_individual(cr: float, rng: Any = None) -> dict[str, int]:
    """随机个体宝藏表（L6182-6194）：一组相同生物掷一次，再乘以数量。

    :return: ``{"金": n}`` 或 ``{"铂": n}``（与角色卡钱包键名一致）。
    """
    row = _band_for("individual_treasure", "cr", float(cr))
    coin, amount = _roll_amount(row, rng)
    return {coin: amount} if amount else {}


def roll_hoard(cr: float, rng: Any = None) -> dict[str, Any]:
    """随机库藏宝藏表（L6196-6210）：首领 CR / 队伍共用宝山用这张。

    :return: ``{"coins": {...}, "magic_items": n, "text": ...}``。
        魔法物品件数只给数量，具体是什么要按第七章随机魔法物品表决定（尚未落地）。
    """
    row = _band_for("hoard_treasure", "cr", float(cr))
    coin, amount = _roll_amount(row, rng)
    magic = max(0, roll(str(row["magic_dice"]), rng=rng).total)
    coins = {coin: amount} if amount else {}
    text = (
        f"库藏宝藏（CR {row['cr']}）：{row['dice']} × {row['multiplier']} "
        f"{row['unit']}（平均 {row['average']}）→ "
        + ("、".join(f"{k} {v}" for k, v in coins.items()) or "无钱币")
        + f"；魔法物品 {magic} 件"
    )
    return {"coins": coins, "magic_items": magic, "text": text}


def roll_quest(level: int, rng: Any = None) -> dict[str, Any]:
    """任务奖励（L6212-6214）：掷一次库藏表，**用角色等级代替 CR**。"""
    return roll_hoard(level, rng)


def _roll_item(section: str, value: int, label: str, rng: Any = None) -> str:
    for row in table(section):
        if int(row["value"]) == int(value):
            entries = list(row["entries"])
            index = roll(str(row["die"]), rng=rng).total - 1
            return f"{entries[index]}（{label} {value} GP，{row['die']}）"
    raise KeyError(f"{section} 没有 {value} GP 这一档")


def roll_gem(value: int, rng: Any = None) -> str:
    """按价值档掷一颗宝石（L9045-9123）。"""
    return _roll_item("gems", value, "宝石", rng)


def roll_art(value: int, rng: Any = None) -> str:
    """按价值档掷一件艺术品（L9127-9195）。"""
    return _roll_item("art", value, "艺术品", rng)


def _pick(rows: tuple[dict[str, Any], ...], value: int) -> dict[str, Any]:
    """按 ``lo..hi`` 区间取一行；区间无缝覆盖时任何 d100 都能命中。"""
    for row in rows:
        if int(row["lo"]) <= value <= int(row["hi"]):
            return row
    raise KeyError(f"掷出 {value}，这张表没有对应区间")


def roll_magic_theme(rng: Any = None) -> str:
    """L20780：物品不属任何主题时，掷 1d4 选一张主题表。"""
    return str(_pick(_MAGIC_TABLES["magic_item_theme"], roll("1d4", rng=rng).total)["theme"])


def _rarity_band(level: int) -> str:
    if level <= 4:
        return "1-4"
    if level <= 10:
        return "5-10"
    if level <= 16:
        return "11-16"
    return "17-20"


def roll_magic_rarity(level: int, rng: Any = None) -> str:
    """L9363《魔法物品稀有度》：按角色等级段掷 1d100 定稀有度。"""
    band = _rarity_band(int(level))
    rows = tuple(
        row
        for row in _MAGIC_TABLES["magic_item_rarity"]
        if str(row["levels"]) == band
    )
    return str(_pick(rows, roll("1d100", rng=rng).total)["rarity"])


def roll_treasure(
    theme: str = "",
    rarity: str = "",
    level: int | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """掷一件随机魔法物品（DMG 2024 第七章 20 张 1d100 表）。

    - ``theme`` 留空 → 掷 1d4 选主题（L20780）；
    - ``rarity`` 留空 → 给了 ``level`` 就按《魔法物品稀有度》表掷，否则默认「非普通」；
    - 表名 = ``<主题>_<稀有度>``，掷 1d100 落在哪行就出哪件物品。

    :return: ``{"theme", "rarity", "item", "roll", "text"}``。
        表里只有**物品名**，效果详情未逐条转录；``items.yaml`` 里有的可查详情，
        其余按第七章原文由 DM 裁定。
    """
    raw_theme = str(theme or "").strip()
    picked_theme = next((t for t in _MAGIC_THEMES if t in raw_theme), "")
    # 显式写错主题要报错，别悄悄掷 1d4 换一张表——那会让 DM 以为自己查的就是那张
    if raw_theme and not picked_theme:
        raise KeyError(
            f"未知宝藏主题：{raw_theme}（可用 奥秘 / 武备 / 器具 / 圣物）"
        )
    raw_rarity = str(rarity or "").strip()
    picked_rarity = _RARITY_ALIASES.get(raw_rarity, raw_rarity)
    if raw_rarity and picked_rarity not in _MAGIC_RARITIES:
        raise KeyError(
            f"未知魔法物品稀有度：{raw_rarity}（可用 "
            + " / ".join(_MAGIC_RARITIES)
            + "）"
        )
    if not picked_theme:
        picked_theme = roll_magic_theme(rng)
    if not picked_rarity:
        picked_rarity = roll_magic_rarity(level, rng) if level else "非普通"
    rows = _MAGIC_TABLES.get(f"{picked_theme}_{picked_rarity}")
    if not rows:
        raise KeyError(f"没有「{picked_theme}_{picked_rarity}」这张表")
    number = roll("1d100", rng=rng).total
    item = str(_pick(rows, number)["item"])
    text = f"随机魔法物品（{picked_theme}·{picked_rarity}，d100={number}）：{item}"
    # 顺手带一段原文摘要：DM 掷一次就该看到这东西做什么，而不是再查一次表
    from .magic_items import get_magic_item

    official = get_magic_item(item)
    if official is not None:
        brief = official.text.replace("\n", " ")
        clipped = brief[:400] + ("…" if len(brief) > 400 else "")
        text += f"\n详情：{clipped}\n完整条目：query_data(table=item, name={item})"
    else:
        text += (
            "\n（该物品的效果详情未收录：中文包只有名字 / 索引条目，没有可转录的效果正文，"
            "按第七章原文由 DM 裁定）"
        )
    return {
        "theme": picked_theme,
        "rarity": picked_rarity,
        "item": item,
        "roll": number,
        "text": text,
        "has_detail": official is not None,
    }


# --------------------------------------------------------------------------- #
# 查表（给 query_data 用）
# --------------------------------------------------------------------------- #


def format_improvised(name: str = "") -> str:
    """即兴伤害表 + 伤害严重性与等级表（L1036-1062）。"""
    text = str(name or "").strip().lower()
    severity = any(k in text for k in ("严重", "等级", "妨碍", "致命", "危害度"))
    lines: list[str] = []
    if not severity:
        lines.append("【即兴伤害】（DMG 2024 L1036-1048）")
        for row in table("improvised_damage"):
            lines.append(f"· {row['dice']}：{row['examples']}")
    if text and not severity:
        hit = [r for r in table("improvised_damage") if str(r["dice"]) == text]
        if hit:
            return f"{hit[0]['dice']}：{hit[0]['examples']}（即兴伤害表）"
    if not text or severity:
        lines.append("【伤害严重性与等级】（L1050-1062）")
        for row in table("damage_severity"):
            lines.append(
                f"· {row['levels']} 级：妨碍 {row['setback']}／致命 {row['deadly']}"
            )
        lines.append("妨碍型很少致死，致命型对已失血的角色是实打实的威胁。")
    return "\n".join(lines)


_DOOR_NOTES = (
    "大门：HP ×2 或 ×3、开启 DC +5（L2324-2328）。"
    "门是中型物件；破门用一个操作动作，过力量（运动）检定。"
)
_LOCK_NOTES = (
    "撬锁要盗贼工具，按复杂度花时间后过敏捷（巧手）检定；"
    "DC 按锁的质量定（L2342-2357）。"
)
_SECRET_NOTES = (
    "一个搜索动作搜 10 尺方形墙面，过感知（察觉）检定；"
    "靠线索推断改用智力（调查）（L2359-2372）。"
)
_PORTCULLIS_NOTES = (
    "5 尺内可穿过射击 / 施法，对对面有四分之三掩护；"
    "摧毁用金属门（铁）或木门（木）的 AC / HP；升降要操作动作（L2378-2389）。"
)
_TRAP_NOTES = (
    "危害度与等级：妨碍级不太可能重创该等级角色，致命级可能；"
    "对更低等级的角色，妨碍级也可能是致命的（L3115-3119、L5330-5339）。"
    "陷阱同时施加状态时，伤害宜下调（L5594-5600）。"
)


def _lines(section: str, fields: tuple[str, ...], header: str) -> list[str]:
    out = [header]
    for row in table(section):
        out.append("· " + " / ".join(f"{field} {row[field]}" for field in fields))
    return out


def format_hazard(name: str = "") -> str:
    """门 / 锁 / 暗门 / 吊闸门 / 建造陷阱与危害度、第三章范例卡。"""
    text = str(name or "").strip()
    # 范例卡（危害物 / 陷阱 / 毒药 / 疫病 / 攻城装备）优先：写名字或类别词就是查它
    from .hazard_cards import format_hazard_cards, hazard_card_summary

    card_text = format_hazard_cards(text)
    if card_text:
        return card_text
    if "追逐" in text:
        from .chase import format_chase_rules

        key = text.replace("追逐", "", 1).strip()
        if key.startswith("规则"):
            key = key[2:].strip()
        return format_chase_rules(key) or (
            "追逐规则可查：开始 / 疾走 / 攻击 / 结束 / 逃脱 / 意外 / 城市 / 荒野"
        )
    if not text or text in ("全部", "所有"):
        blocks = [
            _lines("doors", ("kind", "ac", "hp", "dc"), "【门】AC / HP / 开启DC"),
            [_DOOR_NOTES],
            _lines("lock_complexity", ("kind", "time"), "【锁的复杂程度】"),
            _lines("lock_quality", ("kind", "dc"), "【锁的质量】"),
            [_LOCK_NOTES],
            _lines("secret_doors", ("kind", "dc"), "【暗门】察觉DC"),
            [_SECRET_NOTES],
            _lines("portcullis", ("size", "iron_dc", "wood_dc"), "【吊闸门】"),
            [_PORTCULLIS_NOTES],
            _lines(
                "trap_build",
                (
                    "levels",
                    "setback_attack",
                    "setback_dc",
                    "setback_damage",
                    "deadly_attack",
                    "deadly_dc",
                    "deadly_damage",
                ),
                "【建造陷阱】妨碍：攻击/DC/伤害；致命：攻击/DC/伤害",
            ),
            [_TRAP_NOTES],
        ]
        if not text:
            blocks.insert(
                0,
                ["可细查：门、锁、暗门、吊闸门、陷阱、追逐、全部（默认给全表）"],
            )
        blocks.append([hazard_card_summary()])
        return "\n".join(line for block in blocks for line in block)
    if "吊闸" in text:
        block = _lines("portcullis", ("size", "iron_dc", "wood_dc"), "【吊闸门】")
        return "\n".join([*block, _PORTCULLIS_NOTES])
    if "暗门" in text:
        block = _lines("secret_doors", ("kind", "dc"), "【暗门】察觉DC")
        return "\n".join([*block, _SECRET_NOTES])
    if "锁" in text:
        block = [
            "【锁】",
            *_lines("lock_complexity", ("kind", "time"), "复杂度 / 时间"),
            *_lines("lock_quality", ("kind", "dc"), "质量 / 开锁DC"),
            _LOCK_NOTES,
        ]
        return "\n".join(block)
    if "陷阱" in text or "危害" in text:
        block = _lines(
            "trap_build",
            (
                "levels",
                "setback_attack",
                "setback_dc",
                "setback_damage",
                "deadly_attack",
                "deadly_dc",
                "deadly_damage",
            ),
            "【建造陷阱】等级 / 妨碍攻击·DC·伤害 / 致命攻击·DC·伤害",
        )
        return "\n".join([*block, _TRAP_NOTES, hazard_card_summary()])
    if "门" in text:
        block = _lines("doors", ("kind", "ac", "hp", "dc"), "【门】AC / HP / 开启DC")
        return "\n".join([*block, _DOOR_NOTES])
    return "认不出条目。可查：门、锁、暗门、吊闸门、陷阱、全部。"


def _treasure_rows(section: str, fields: tuple[str, ...], header: str) -> list[str]:
    return _lines(section, fields, header)


def _gem_or_art(text: str, want_gem: bool) -> str:
    section = "gems" if want_gem else "art"
    label = "宝石" if want_gem else "艺术品"
    rows = table(section)
    value = _number(text)
    if value is None:
        lines = [f"【{label}】按价值分档（DMG 2024 第七章）"]
        lines += [
            f"· {row['value']} GP：{row['die']}，共 {len(row['entries'])} 项"
            for row in rows
        ]
        lines.append(
            f"写「{label} 100」列这一档，写「掷 {label} 100」随机出一件。"
        )
        return "\n".join(lines)
    row = next((r for r in rows if int(r["value"]) == value), None)
    if row is None:
        return f"没有 {value} GP 这档{label}。可选：" + "、".join(
            str(r["value"]) for r in rows
        )
    if "掷" in text or "随机" in text:
        return f"掷出：{roll_gem(value) if want_gem else roll_art(value)}"
    lines = [f"【{value} GP {label}】{row['die']}"]
    lines += [f"{i}. {entry}" for i, entry in enumerate(row["entries"], 1)]
    return "\n".join(lines)


def format_magic_items(name: str = "") -> str:
    """随机魔法物品 20 张表 / 按等级稀有度 / 投放基准（第七章）。"""
    text = str(name or "").strip()
    if "稀有度" in text and "掷" not in text:
        lines = ["【按等级随机魔法物品稀有度】（L9363，掷 1d100）"]
        for row in _MAGIC_TABLES["magic_item_rarity"]:
            lines.append(
                f"· {row['levels']} 级：{int(row['lo']):02d}-{int(row['hi']):02d}"
                f" → {row['rarity']}"
            )
        return "\n".join(lines)
    if "投放" in text or "奖励" in text or "基准" in text:
        lines = ["【根据等级奖励的魔法物品】（L9342，20 级共 100 件）"]
        for row in _MAGIC_TABLES["magic_item_awards"]:
            lines.append(
                f"· {row['levels']} 级：普通 {row['common']} / 非普通 {row['uncommon']} / "
                f"珍稀 {row['rare']} / 极珍稀 {row['very_rare']} / 传说 {row['legendary']}"
                f" = {row['total']} 件"
            )
        lines.append("过量准备：可多放 25%（向上取整），1-4 级阶段 11 → 14 件。")
        return "\n".join(lines)
    if "掷" in text or "随机" in text:
        level_hit = re.search(r"(\d+)\s*级", text)
        level = int(level_hit.group(1)) if level_hit else None
        theme = next((t for t in _MAGIC_THEMES if t in text), "")
        # 「普通」是「非普通」的子串，长词优先才不会选错表
        rarity = next(
            (
                r
                for r in sorted(_MAGIC_RARITIES, key=len, reverse=True)
                if r in text
            ),
            "",
        )
        result = roll_treasure(theme=theme, rarity=rarity, level=level)
        return (
            result["text"]
            + "\n写「掷 魔法物品 奥秘 珍稀」指定主题与稀有度；"
            "「掷 魔法物品 5级」按等级掷稀有度；留空则 1d4 选主题 + 非普通。"
        )
    return "\n".join(
        [
            "【随机魔法物品】20 张 1d100 表（DMG 2024 第七章 L20778-21410）",
            "· 主题：奥秘 / 武备 / 器具 / 圣物；不属任何主题时掷 1d4 选表（L20780）。",
            "· 稀有度：普通 / 非普通 / 珍稀 / 极珍稀 / 传说（神器不在随机表内）。",
            "· 「掷 魔法物品」直接出一件；「掷 魔法物品 奥秘 珍稀」指定表；"
            "「掷 魔法物品 5级」按等级掷稀有度。",
            "· 「魔法物品 稀有度」看按等级稀有度表；「魔法物品 投放」看 100 件基准。",
            "· 掷出的结果会附一段 DMG 原文摘要；完整条目用 `query_data(table=item, name=物品名)` 查。",
            "⚠️ 约 56 个名字在中文包里只有名字 / 页码、没有效果正文，掷到时会如实提示「详情未收录」。",
        ]
    )


def format_treasure(name: str = "") -> str:
    """贸易金属条 / 宝石 / 艺术品 / 宝藏 / 随机魔法物品（第四、七章）。"""
    text = str(name or "").strip()
    if "魔法物品" in text or "magic" in text.lower():
        return format_magic_items(text)
    if "宝石" in text:
        return _gem_or_art(text, want_gem=True)
    if "艺术" in text:
        return _gem_or_art(text, want_gem=False)
    if "金属条" in text or "贸易条" in text:
        return "\n".join(
            _treasure_rows("trade_bars", ("name", "value", "size"), "【贸易金属条】")
        )
    if "库藏" in text or "任务" in text or "掷 宝藏" in text:
        lines = _treasure_rows(
            "hoard_treasure",
            ("cr", "dice", "multiplier", "unit", "average", "magic_dice"),
            "【随机库藏宝藏表】CR / 骰式 / 倍率 / 币种 / 平均值 / 魔法物品",
        )
        lines.append(
            "任务奖励：掷一次库藏表，用**角色等级**代替 CR（L6212-6214）；"
            "库藏约每回游戏一次，群体用首领 CR，囤积怪可掷双倍或数量翻倍（L6198-6200）。"
        )
        if "掷" in text:
            level = _number(text.replace("库藏", "").replace("任务", "")) or 1
            lines.append(roll_hoard(level)["text"])
        return "\n".join(lines)
    # 默认 / 个体宝藏
    lines = _treasure_rows(
        "individual_treasure",
        ("cr", "dice", "multiplier", "unit", "average"),
        "【随机个体宝藏表】CR / 骰式 / 倍率 / 币种 / 平均值",
    )
    lines.append(
        "一组相同生物掷一次再乘数量（L6186）；括号平均值可直接取用。"
        f"每 {COINS_PER_POUND} 枚任意钱币合一磅。"
    )
    lines.append(
        "还可查：金属条、宝石、艺术品、库藏、任务、魔法物品"
        "（写「掷 宝石 100」「掷 魔法物品 奥秘 珍稀」直接出结果）。"
    )
    return "\n".join(lines)
