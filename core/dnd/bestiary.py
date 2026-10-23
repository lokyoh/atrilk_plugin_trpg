"""《怪物图鉴（2025）》名录（``res/dnd/bestiary_index.yaml``）。

**这里没有数据卡。** 那份参考书是 Vault 导出的，每张数据卡在 markdown 里都只剩
``正在加载"某怪"……`` 的占位符——AC、生命值、属性、动作一个数字都没有
（《风骸岛之龙》附录 B 是同一种问题）。能读出来的只有三份附录，于是这份表收的是：

- **名字**（502 张数据卡，一个 ``##`` 小节里可能有好几张）；
- **生物类型**（异怪 / 野兽 / 天族 / 构装 / 龙类 / 元素 / 妖精 / 邪魔 / 巨人 /
  类人 / 怪兽 / 泥怪 / 植物 / 亡灵）与**子类标签**（天使 / 恶魔 / 色彩龙 / 恐龙…）；
- **挑战等级**与**栖息地**。

它能做的事很明确：**按 CR 挑怪、统一译名、回答「图鉴里有没有这只」**。
要开战得用 ``monsters*.yaml`` 里的数据卡（73 条）——两边靠名字对得上。

生成脚本：``scripts/build_bestiary_index.py``（原文不进仓库，产物进仓库）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .datafiles import as_texts, load_table

__all__ = [
    "BESTIARY",
    "BESTIARY_MAP",
    "BestiaryEntry",
    "bestiary_by_cr",
    "bestiary_text",
    "find_bestiary",
    "search_bestiary",
]


def _cr_value(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _cr_text(cr: float) -> str:
    """0.125 → ``1/8``、0.5 → ``1/2``、3.0 → ``3``。"""
    if cr >= 1:
        return str(int(cr)) if float(cr).is_integer() else f"{cr:g}"
    return {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}.get(cr, f"{cr:g}")


@dataclass(frozen=True, slots=True)
class BestiaryEntry:
    """图鉴里的一张数据卡**名录**（只有名字与分类，没有数值）。"""

    #: 中文名（这份导出没有英文名）
    name: str
    #: 生物类型（14 类之一；对上不的为空）
    type: str = ""
    #: 挑战等级
    cr: float = 0.0
    #: 栖息地（任意 / 极地 / 森林…；不居住自然环境的为空）
    habitats: tuple[str, ...] = ()
    #: 子类标签（天使 / 恶魔 / 色彩龙 / 恐龙 / 类地精…）
    tags: tuple[str, ...] = ()

    @property
    def cr_text(self) -> str:
        return _cr_text(self.cr)

    def line(self) -> str:
        """一行摘要：``成年黑龙｜龙类｜CR 17｜山地，沼泽``。"""
        bits = [self.name]
        if self.type:
            bits.append(self.type)
        bits.append(f"CR {self.cr_text}")
        if self.tags:
            bits.append("/".join(self.tags))
        if self.habitats:
            bits.append("、".join(self.habitats))
        return "｜".join(bits)


def _entry(row: dict[str, Any]) -> BestiaryEntry:
    return BestiaryEntry(
        name=str(row["name"]),
        type=str(row.get("type", "")),
        cr=_cr_value(row.get("cr")),
        habitats=as_texts(row.get("habitats")),
        tags=as_texts(row.get("tags")),
    )


#: 图鉴名录：按 CR 升序（与书里附录的顺序一致）
BESTIARY: tuple[BestiaryEntry, ...] = tuple(
    sorted(
        (_entry(row) for row in load_table("bestiary_index")),
        key=lambda entry: (entry.cr, entry.name),
    )
)

BESTIARY_MAP: dict[str, BestiaryEntry] = {entry.name: entry for entry in BESTIARY}


# --------------------------------------------------------------------------- #
# 查
# --------------------------------------------------------------------------- #


def search_bestiary(value: str) -> tuple[BestiaryEntry, ...]:
    """按名字找名录：精确 → 去括号的简写 → 包含。可能有好几条（型号 / 同族）。"""
    text = str(value or "").strip()
    if not text:
        return ()
    if text in BESTIARY_MAP:
        return (BESTIARY_MAP[text],)
    # 「恶咒蛇人（1 型）」这种带型号的，允许只写「恶咒蛇人」——但会有好几条
    bare = text.split("（")[0].split("(")[0].strip()
    if bare in BESTIARY_MAP:
        return (BESTIARY_MAP[bare],)
    return tuple(
        entry for entry in BESTIARY if text in entry.name or entry.name in text
    )


def find_bestiary(value: str) -> BestiaryEntry | None:
    """按名字查名录，**唯一命中**才返回（有歧义返回 ``None``，交给调用方列出来）。"""
    hits = search_bestiary(value)
    return hits[0] if len(hits) == 1 else None


def bestiary_by_cr(cr: float) -> tuple[BestiaryEntry, ...]:
    """某个挑战等级的全部名录（按名字）。"""
    return tuple(entry for entry in BESTIARY if entry.cr == float(cr))


def bestiary_text(name: str = "") -> str:
    """``query_data(table=bestiary)`` 的输出。

    - 留空：给概览（条数、类型分布、怎么查）；
    - ``cr 5`` / ``cr5``：该挑战等级有哪些；
    - 名字：那条名录，并说清「这里没有数值，开战要用 monster 表」。
    """
    text = str(name or "").strip()
    if not text:
        counts: dict[str, int] = {}
        for entry in BESTIARY:
            counts[entry.type or "未分类"] = counts.get(entry.type or "未分类", 0) + 1
        spread = "、".join(f"{key} {value}" for key, value in sorted(counts.items()))
        return (
            f"《怪物图鉴（2025）》名录：{len(BESTIARY)} 张数据卡"
            "（**只有名字 / 类型 / CR / 栖息地——这份导出没有数值卡，不能直接开战**）\n"
            f"类型：{spread}\n"
            "查某只：写名字（如「成年黑龙」）；按挑战等级挑：写「cr 5」。"
        )

    match = text.lower().replace(" ", "")
    if match.startswith("cr") or text.replace("/", "").isdigit():
        cr_text = text.lower().replace("cr", "").replace(" ", "")
        try:
            cr = (
                float(cr_text.split("/")[0]) / float(cr_text.split("/")[1])
                if "/" in cr_text
                else float(cr_text)
            )
        except (ValueError, ZeroDivisionError):
            raise KeyError(f"看不懂这个挑战等级：{text}") from None
        hits = bestiary_by_cr(cr)
        if not hits:
            return f"CR {_cr_text(cr)} 没有登记。"
        lines = [f"CR {_cr_text(cr)} 共有 {len(hits)} 只："]
        lines.extend(f"· {entry.line()}" for entry in hits)
        lines.append("（图鉴名录，AC/生命/动作在 monster 表里查；没有的说明还没有数据卡。）")
        return "\n".join(lines)

    hits = search_bestiary(text)
    if not hits:
        raise KeyError(f"图鉴里没有「{text}」")
    if len(hits) > 1:
        lines = [f"「{text}」在图鉴里有 {len(hits)} 条，写全名再查："]
        lines.extend(f"· {entry.line()}" for entry in hits[:20])
        return "\n".join(lines)
    entry = hits[0]
    return (
        f"{entry.line()}\n"
        "（这是**名录**：图鉴的这份导出没有数值卡，AC / 生命 / 属性 / 动作都不在。"
        "要打就用 query_data（table=monster）查同名数据卡；查不到说明它还没有数值。）"
    )
