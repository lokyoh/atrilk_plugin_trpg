"""DND 5e 法术数据表（戏法 ~ 3 环）。

对应 ``res/rule.md`` 第十节「法术规则」，把规则里只有文字的部分落成可结算的数据：

- **法术攻击**：``1d20 + 施法属性调整值 + 熟练加值``，对抗目标 AC（``attack=True`` 的法术）；
- **法术豁免**：目标对抗 ``豁免 DC = 8 + 熟练加值 + 施法属性调整值``（``save`` 标明用哪项豁免）；
- **法术位**：1 环以上消耗法术位（:func:`max_slots` 给出各职业各级的法术位表），
  戏法（``level=0``）不消耗；升环施放按 ``upcast`` 描述增强；
- **专注**：``concentration=True`` 的法术同时只能维持一个。

数据是**可玩的近似值**，以本表为准；组合成角色卡时通过 :func:`get_spell` 按 key /
中文名取。战斗结算在 :mod:`..agents.dnd.tools` 的 ``cast_spell``。

**表在 ``res/dnd/spells.yaml``**，这里只留类型、法术位表与查询函数——
补一条法术不用改代码。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .attributes import get_ability
from .datafiles import as_texts, load_table

__all__ = [
    "CASTER_KINDS",
    "FULL_CASTER_SLOTS",
    "HALF_CASTER_SLOTS",
    "PACT_SLOTS",
    "SCHOOLS",
    "SPELLS",
    "SPELL_MAP",
    "Spell",
    "format_spell_lines",
    "format_spell_list",
    "get_spell",
    "max_slots",
    "slot_text",
    "spells_for_class",
    "spells_of_level",
]

#: 法术学派（中文名）
SCHOOLS: tuple[str, ...] = (
    "防护",
    "咒法",
    "预言",
    "附魔",
    "塑能",
    "幻术",
    "死灵",
    "变化",
)

#: 施法类型 -> 中文名
CASTER_KINDS: dict[str, str] = {
    "full": "全职施法",
    "half": "半施法",
    "pact": "契约魔法",
}

#: 环阶显示名
_LEVEL_LABELS: dict[int, str] = {0: "戏法", 1: "一环", 2: "二环", 3: "三环", 4: "四环"}

#: 法术位表：职业等级 -> {环阶: 法术位数量}
FULL_CASTER_SLOTS: dict[int, dict[int, int]] = {
    1: {1: 2},
    2: {1: 3},
    3: {1: 4, 2: 2},
    4: {1: 4, 2: 3},
    5: {1: 4, 2: 3, 3: 2},
    6: {1: 4, 2: 3, 3: 3},
    7: {1: 4, 2: 3, 3: 3, 4: 1},
    8: {1: 4, 2: 3, 3: 3, 4: 2},
    9: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    11: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    12: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
}

#: 半施法者（圣武士 / 游侠）：1 级还没有法术
HALF_CASTER_SLOTS: dict[int, dict[int, int]] = {
    1: {},
    2: {1: 2},
    3: {1: 3},
    4: {1: 3},
    5: {1: 4, 2: 2},
    6: {1: 4, 2: 2},
    7: {1: 4, 2: 3},
    8: {1: 4, 2: 3},
    9: {1: 4, 2: 3, 3: 2},
    10: {1: 4, 2: 3, 3: 2},
    11: {1: 4, 2: 3, 3: 3},
    12: {1: 4, 2: 3, 3: 3},
}

#: 邪术师契约魔法：法术位少、全部同环、短休恢复
PACT_SLOTS: dict[int, dict[int, int]] = {
    1: {1: 1},
    2: {1: 2},
    3: {2: 2},
    4: {2: 2},
    5: {3: 2},
    6: {3: 2},
    7: {4: 2},
    8: {4: 2},
    9: {5: 2},
    10: {5: 2},
    11: {5: 3},
    12: {5: 3},
}

_SLOT_TABLES: dict[str, dict[int, dict[int, int]]] = {
    "full": FULL_CASTER_SLOTS,
    "half": HALF_CASTER_SLOTS,
    "pact": PACT_SLOTS,
}


def max_slots(caster_kind: str, level: int) -> dict[int, int]:
    """某施法类型在某个等级的法术位上限 ``{环阶: 数量}``；非法输入返回空表。"""
    table = _SLOT_TABLES.get(str(caster_kind or ""))
    if not table:
        return {}
    lv = max(1, min(int(level or 1), max(table)))
    return dict(table.get(lv, {}))


def slot_text(slots: Mapping[int, int], used: Mapping[int, int] | None = None) -> str:
    """把法术位排成 ``1环 2/3、2环 1/2`` 这样的文本。"""
    if not slots:
        return "（无法术位）"
    used = used or {}
    parts = []
    for level in sorted(slots):
        total = int(slots[level])
        spent = min(int(used.get(level, used.get(str(level), 0)) or 0), total)
        parts.append(
            f"{_LEVEL_LABELS.get(level, f'{level} 环')} {total - spent}/{total}"
        )
    return "、".join(parts)


@dataclass(frozen=True, slots=True)
class Spell:
    """一个法术。"""

    key: str
    cn: str
    en: str
    level: int  #: 0 = 戏法
    school: str  #: 学派（:data:`SCHOOLS` 之一）
    classes: tuple[str, ...]  #: 可用职业键（:mod:`..profession` 的键）
    casting_time: str = "1 动作"
    range_text: str = "自身"
    components: str = "语言、姿势"
    duration: str = "即时"
    concentration: bool = False
    ritual: bool = False
    attack: bool = False  #: 是否法术攻击（掷 d20 对抗 AC）
    save: str = ""  #: 需要豁免时用哪项属性（键）
    damage: str = ""  #: 伤害表达式
    damage_type: str = ""
    heal: str = ""  #: 治疗表达式
    half_on_save: bool = True  #: 需豁免的伤害法术：豁免成功是否减半
    auto_damage: bool = False  #: 无需攻击 / 豁免便直接造成伤害（如魔法飞弹）
    upcast: str = ""  #: 升环施放的效果
    scaling: str = ""  #: 戏法随等级成长
    desc: str = ""

    # —— 派生 ——

    @property
    def is_cantrip(self) -> bool:
        return self.level == 0

    @property
    def level_text(self) -> str:
        return _LEVEL_LABELS.get(self.level, f"{self.level} 环")

    @property
    def save_text(self) -> str:
        return get_ability(self.save).cn if self.save else ""

    @property
    def resolution(self) -> str:
        """结算方式一句话：法术攻击 / 某属性豁免 / 自动生效。"""
        if self.attack:
            return "法术攻击（对抗 AC）"
        if self.save:
            half = "，成功减半" if self.half_on_save and self.damage else ""
            return f"{self.save_text}豁免{half}"
        return "自动生效"

    # —— 供战斗结算用的解析 ——

    @property
    def damage_type_text(self) -> str:
        """去掉括号备注后的伤害类型（如「（酸 / 寒冷任选）」→ 空串）。"""
        return re.sub(r"（[^）]*）", "", self.damage_type or "").strip()

    def damage_expr(self, modifier: int = 0) -> str:
        """可掷的伤害表达式：把「施法属性调整值」替换成实际加值。"""
        text = re.sub(r"（[^）]*）", "", self.damage or "").strip()
        if "施法属性调整值" in text:
            text = text.replace("施法属性调整值", f"{int(modifier):+d}")
        return text

    def heal_info(self) -> tuple[str, bool, bool]:
        """解析治疗字段：``(骰子表达式, 是否临时生命, 是否加施法属性调整值)``。

        认不出的写法（「伤害的一半」「每颗回复 1 点」「生命上限 +5」）返回空表达式，
        由 DM 自行裁决。
        """
        text = self.heal or ""
        if not text or "每颗" in text:
            return "", False, False
        temp = "临时" in text
        add_mod = "施法属性调整值" in text
        bare = re.sub(r"（[^）]*）", "", text)
        match = re.match(r"\s*(\d*d\d+(?:[+-]\d+)?|\d+)", bare)
        if not match:
            return "", temp, add_mod
        return match.group(1), temp, add_mod

    def label(self) -> str:
        return f"{self.cn}（{self.level_text}·{self.school}）"

    def summary(self) -> str:
        """一行摘要（列表用）。"""
        text = f"{self.cn}（{self.level_text}）{self.resolution}"
        if self.damage:
            text += f" {self.damage} {self.damage_type}"
        if self.heal:
            text += f" 回复 {self.heal}"
        if self.concentration:
            text += " 专注"
        return text

    def full_text(self) -> str:
        """完整说明（DM / 玩家查询用）。"""
        lines = [
            f"{self.cn}（{self.en}）　{self.level_text}·{self.school}"
            + ("　专注" if self.concentration else "")
            + ("　仪式" if self.ritual else ""),
            f"施法时间 {self.casting_time}　距离 {self.range_text}　成分 {self.components}　"
            f"持续 {self.duration}",
            f"结算：{self.resolution}"
            + (f"　伤害 {self.damage} {self.damage_type}" if self.damage else "")
            + (f"　回复 {self.heal}" if self.heal else ""),
        ]
        if self.upcast:
            lines.append(f"升环：{self.upcast}")
        if self.scaling:
            lines.append(f"成长：{self.scaling}")
        if self.desc:
            lines.append(self.desc)
        return "\n".join(lines)


def _spell(row: dict[str, Any]) -> Spell:
    """一个法术（``res/dnd/spells.yaml`` 的一项）。

    YAML 里省掉的都是默认值（施法时间 1 动作、持续即时、不需要仪式……），
    所以读起来就是「这条法术特殊在哪儿」。
    """
    return Spell(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row["en"]),
        level=int(row["level"]),
        school=str(row["school"]),
        classes=as_texts(row.get("classes")),
        casting_time=str(row.get("casting_time", "1 动作")),
        range_text=str(row.get("range_text", "自身")),
        components=str(row.get("components", "语言、姿势")),
        duration=str(row.get("duration", "即时")),
        concentration=bool(row.get("concentration", False)),
        ritual=bool(row.get("ritual", False)),
        attack=bool(row.get("attack", False)),
        save=str(row.get("save", "")),
        damage=str(row.get("damage", "")),
        damage_type=str(row.get("damage_type", "")),
        heal=str(row.get("heal", "")),
        half_on_save=bool(row.get("half_on_save", True)),
        auto_damage=bool(row.get("auto_damage", False)),
        upcast=str(row.get("upcast", "")),
        scaling=str(row.get("scaling", "")),
        desc=str(row.get("desc", "")),
    )


#: 法术表：数值在 ``res/dnd/spells.yaml``
SPELLS: tuple[Spell, ...] = tuple(_spell(row) for row in load_table("spells"))


SPELL_MAP: dict[str, Spell] = {}
for _spell in SPELLS:
    SPELL_MAP[_spell.key] = _spell
    SPELL_MAP[_spell.cn] = _spell
    SPELL_MAP[_spell.en.lower()] = _spell


def get_spell(value: Spell | str) -> Spell | None:
    """按对象 / key / 中文名 / 英文名取法术，取不到返回 ``None``。"""
    if isinstance(value, Spell):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text in SPELL_MAP:
        return SPELL_MAP[text]
    lowered = text.lower()
    if lowered in SPELL_MAP:
        return SPELL_MAP[lowered]
    for name, spell in SPELL_MAP.items():
        if name in text or text in name:
            return spell
    return None


def spells_of_level(level: int, classes: Iterable[str] | None = None) -> list[Spell]:
    """取某个环阶的法术；``classes`` 非空时只取这些职业可用的。"""
    wanted = {str(c) for c in classes} if classes else None
    return [
        spell
        for spell in SPELLS
        if spell.level == int(level)
        and (wanted is None or bool(wanted & set(spell.classes)))
    ]


def spells_for_class(cls: str, *, levels: Iterable[int] = (0, 1, 2, 3)) -> list[Spell]:
    """某个职业在指定环阶范围内可用的法术（按环阶、名字排序）。"""
    wanted = {int(lv) for lv in levels}
    return sorted(
        (s for s in SPELLS if cls in s.classes and s.level in wanted),
        key=lambda s: (s.level, s.cn),
    )


def format_spell_lines(spells: Iterable[Spell], *, numbered: bool = True) -> str:
    """把法术列表排成编号清单（建卡选法术 / 查询用）。"""
    lines = []
    for index, spell in enumerate(spells, 1):
        prefix = f"{index}. " if numbered else "· "
        lines.append(prefix + spell.summary())
    return "\n".join(lines)


def format_spell_list(spells: Iterable[Spell]) -> str:
    """把法术列表排成一行（按环阶分组）。"""
    groups: dict[int, list[str]] = {}
    for spell in spells:
        groups.setdefault(spell.level, []).append(spell.cn)
    parts = []
    for level in sorted(groups):
        label = _LEVEL_LABELS.get(level, f"{level} 环")
        parts.append(f"{label}：" + "、".join(groups[level]))
    return "；".join(parts)
