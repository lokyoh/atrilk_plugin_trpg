"""DND 5e 专长（PHB 2024 第五章）。

专长是「与职业无关的特殊特性」，分四类：**起源 / 通用 / 战斗风格 / 传奇恩惠**。
本模块目前只收 **起源专长**——它们是建卡时由**背景**自动给的那一枚（3999 / 4099，
恰好 10 项），也是本局唯一会出现在 1 级角色卡上的专长。

其余三类依赖等级（通用 / 传奇恩惠）或由职业给（战斗风格），
等 B5 高等级开卡与 T9 子职一并补；本模块的表与查询函数对它们是同样开放的
（往 ``res/dnd/feats.yaml`` 里加一行即可，不用改代码）。

**数值在 ``res/dnd/feats.yaml``**，这里只留类型与查询函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .datafiles import as_texts, load_table

__all__ = [
    "FEATS",
    "FEAT_MAP",
    "ORIGIN_FEATS",
    "Feat",
    "feat_choices",
    "get_feat",
    "origin_feats",
]


@dataclass(frozen=True, slots=True)
class SpellGrant:
    """专长给的法术（目前只有魔法学徒）。"""

    #: 戏法数（0 环）
    cantrips: int = 0
    #: 一并给的法术环阶（0 = 不给）
    level: int = 0
    #: 该环阶给几道
    count: int = 0


@dataclass(frozen=True, slots=True)
class Feat:
    """一项专长。"""

    key: str
    cn: str
    en: str
    #: 类别：起源 / 通用 / 战斗风格 / 传奇恩惠
    category: str = "起源"
    #: 可判定的效果（一句一条；纯 flavor 不进这里）
    benefits: tuple[str, ...] = ()
    #: 每等级的生命值上限加值（健壮 +2）
    hp_per_level: int = 0
    #: 专长给的法术（魔法学徒：2 戏法 + 1 道一环）
    spell_grant: SpellGrant = field(default_factory=SpellGrant)
    desc: str = ""

    @property
    def is_origin(self) -> bool:
        return self.category == "起源"

    @property
    def benefit_text(self) -> str:
        """效果并成一句（空则空串）。"""
        return "；".join(self.benefits)


def _spell_grant(row: dict[str, Any]) -> SpellGrant:
    data = row.get("spell_list") or {}
    if not isinstance(data, dict):
        return SpellGrant()
    return SpellGrant(
        cantrips=int(data.get("cantrips", 0) or 0),
        level=int(data.get("level", 0) or 0),
        count=int(data.get("count", 0) or 0),
    )


def _feat(row: dict[str, Any]) -> Feat:
    """一项专长（``res/dnd/feats.yaml`` 的一项）。"""
    return Feat(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        category=str(row.get("category", "起源")),
        benefits=as_texts(row.get("benefits")),
        hp_per_level=int(row.get("hp_per_level", 0) or 0),
        spell_grant=_spell_grant(row),
        desc=str(row.get("desc", "")),
    )


#: 专长表：数值在 ``res/dnd/feats.yaml``
FEATS: tuple[Feat, ...] = tuple(_feat(row) for row in load_table("feats"))

FEAT_MAP: dict[str, Feat] = {f.key: f for f in FEATS}

#: 起源专长（背景给的就是这一批）
ORIGIN_FEATS: tuple[Feat, ...] = tuple(f for f in FEATS if f.is_origin)


def origin_feats() -> tuple[Feat, ...]:
    """全部起源专长。"""
    return ORIGIN_FEATS


def _base_name(text: str) -> str:
    """去掉背景里的括号后缀：「魔法学徒（牧师）」->「魔法学徒」。"""
    for opener in ("（", "("):
        if opener in text:
            text = text.split(opener, 1)[0]
    return text.strip()


def get_feat(value: Feat | str) -> Feat:
    """按专长对象 / 内部键 / 中文名取出专长。

    背景的 ``feat`` 带括号（如「魔法学徒（牧师）」表示魔法学徒选牧师法表），
    按括号前的名字匹配。
    """
    if isinstance(value, Feat):
        return value
    text = str(value).strip()
    if text in FEAT_MAP:
        return FEAT_MAP[text]
    base = _base_name(text)
    for feat in FEATS:
        if base in (feat.key, feat.cn) or base.lower() == feat.en.lower():
            return feat
    raise KeyError(f"未知专长：{value}")


def feat_choices() -> tuple[str, ...]:
    """全部起源专长中文名。"""
    return tuple(f.cn for f in ORIGIN_FEATS)
