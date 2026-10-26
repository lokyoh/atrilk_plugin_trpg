"""DND 5e 阵营（PHB 2024 建卡第四步）。

阵营由「道德倾向（善良 / 邪恶 / 中立）」×「对秩序的态度（守序 / 混乱 / 中立）」
界定，共九种；另有「无阵营」给缺乏理性思维的生物。

⚠️ **阵营没有任何机械效果**（手册 2312-2347）——它只是写在角色卡上的扮演标记。
所以这里不做校验、不做结算，只给建卡一份选项与一个宽容的归一化函数：
玩家写「混乱善良」「CG」「守序善良」都要认得出来。

**数值在 ``res/dnd/alignments.yaml``**，这里只留类型与查询函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .datafiles import load_table

__all__ = [
    "ALIGNMENTS",
    "ALIGNMENT_MAP",
    "Alignment",
    "alignment_choices",
    "alignment_options",
    "normalize_alignment",
]


@dataclass(frozen=True, slots=True)
class Alignment:
    """一个阵营。"""

    key: str
    cn: str
    en: str
    desc: str = ""


def _alignment(row: dict[str, Any]) -> Alignment:
    return Alignment(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        desc=str(row.get("desc", "")),
    )


#: 阵营表：数值在 ``res/dnd/alignments.yaml``
ALIGNMENTS: tuple[Alignment, ...] = tuple(
    _alignment(row) for row in load_table("alignments")
)

ALIGNMENT_MAP: dict[str, Alignment] = {a.key: a for a in ALIGNMENTS}


def alignment_choices() -> tuple[str, ...]:
    """全部阵营中文名（建卡选项用）。"""
    return tuple(a.cn for a in ALIGNMENTS)


def alignment_options() -> str:
    """带序号的阵营清单（建卡提示用）。"""
    return "；".join(f"{index}.{a.cn}" for index, a in enumerate(ALIGNMENTS, 1))


def normalize_alignment(text: str) -> str:
    """把玩家写的阵营词归一到中文名；认不出就原样返回（**不报错**）。

    阵营不参与任何结算，硬拒只会把「玩家写了个别名」变成一次建卡失败。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    for item in ALIGNMENTS:
        # key 本身就是两字母缩写（lg / ng / …），写成「CG」「cg」都认
        if raw == item.cn or lowered == item.key or lowered == item.en.lower():
            return item.cn
    for item in ALIGNMENTS:
        if item.cn in raw or raw in item.cn:
            return item.cn
    return raw
