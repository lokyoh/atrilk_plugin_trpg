"""DND 5e 法术数据表（**0~9 环全覆盖**，PHB 2024）。

表分两批写成，混在同一张 ``res/dnd/spells.yaml`` 里：

- 前 121 条（0~3 环）是手工校对版，带 ``conditions`` / ``terrain`` /
  ``half_on_save`` 这类引擎特化字段；
- 其余 275 条由脚本从 5etools 的 ``spells-xphb.json``（XPHB = 2024）抽取，
  补足 4~9 环。

``classes``（职业归属）另有一套来源：法术条目本身没有这个字段（中文包把英文版的
``classes.fromClassList`` 裁了），但 ``spells/sources.json`` 里有一张「法术 → 职业
列表」表，XPHB 段正好 391 条 —— 全表的归属都从那里回填，按**英文名**匹配（2024
改过一批译名，只按中文名会漏几十条）。命中就覆盖：手工版那批是 2014 口径，
XPHB 才是 2024 口径。剩下 5 条匹配不上的是 2024 删掉的法术（霜噬 / 元素吸收 /
幽影之刃 / 潮涌 / 雷霆步），保留手工版原有的归属。

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
    "merge_dice",
    "parse_single_dice",
    "slot_text",
    "spells_for_class",
    "spells_of_level",
]

#: 法术学派（中文名）
SCHOOLS: tuple[str, ...] = (
    "防护",
    "咒法",
    "预言术",
    "惑控",
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
_LEVEL_LABELS: dict[int, str] = {
    0: "戏法",
    1: "一环",
    2: "二环",
    3: "三环",
    4: "四环",
    5: "五环",
    6: "六环",
    7: "七环",
    8: "八环",
    9: "九环",
}

#: 戏法成长文本「5 级 2d10、11 级 3d10、17 级 4d10」→ (等级, 伤害骰)
_SCALING_RE = re.compile(r"(\d+)\s*级\s*(\d+d\d+)")

#: 升环文本「每升一环 +1d8」「每升一环 +1d4+1」→ (骰数, 面数, 固定加值)
_UPCAST_DICE_RE = re.compile(r"每升一环\s*\+\s*(\d*)d(\d+)\s*([+-]\s*\d+)?")

#: 单一骰式（``3d8`` / ``2d8+3`` / ``1d4-1``）——伤害结算只认这一种
_SINGLE_DICE_RE = re.compile(r"^(\d*)d(\d+)\s*(?:([+-])\s*(\d+))?$")


def parse_single_dice(text: str) -> tuple[int, int, int] | None:
    """``"3d8"`` / ``"2d8+3"`` → ``(骰数, 面数, 固定加值)``；不是单一骰式返回 ``None``。"""
    match = _SINGLE_DICE_RE.match(str(text or "").strip())
    if match is None:
        return None
    sign = -1 if match.group(3) == "-" else 1
    return (
        int(match.group(1) or 1),
        int(match.group(2)),
        sign * int(match.group(4) or 0),
    )


def merge_dice(base: str, extra: str) -> str:
    """把两段骰式合成一个 ``NdM+K``（``roll_damage`` 只认单一骰式）。

    面数不同或任一段不是单一骰式时**原样返回** base——宁可少算这一段，
    也不给一个解析不了、最后按 0 点结算的表达式。
    """
    if not base or not extra:
        return base
    b, e = parse_single_dice(base), parse_single_dice(extra)
    if b is None or e is None or b[1] != e[1]:
        return base
    out = f"{b[0] + e[0]}d{b[1]}"
    flat = b[2] + e[2]
    if flat:
        out += f"{flat:+d}"
    return out


#: 法术位表：职业等级 -> {环阶: 法术位数量}
#: 全职施法者（法师 / 牧师 / 德鲁伊 / 吟游诗人 / 术士）按 PHB 法术位表延伸到 20 级
#:（2014 与 2024 相同）。
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
    13: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    16: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 1, 7: 1, 8: 1, 9: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 1, 8: 1, 9: 1},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
}

#: 半施法者（圣武士 / 游侠）：**PHB 2024 起 1 级就能施法**（2 个一环位），
#: 2 级以后与 2014 表相同 —— 等价于「职业等级的一半向上取整」去查兼职施法者表。
HALF_CASTER_SLOTS: dict[int, dict[int, int]] = {
    1: {1: 2},
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
    13: {1: 4, 2: 3, 3: 3, 4: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 2},
    16: {1: 4, 2: 3, 3: 3, 4: 2},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
}

#: 魔契师契约魔法：法术位少、全部同环、短休恢复；按 PHB 法术位表延伸到 20 级（两版相同）。
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
    13: {6: 3},
    14: {6: 3},
    15: {7: 3},
    16: {7: 3},
    17: {8: 3},
    18: {8: 3},
    19: {9: 3},
    20: {9: 3},
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
    #: 豁免失败时给目标挂的状态（规范状态名，见 ``conditions.CONDITIONS``）。
    #: 5e 的控制法术都是「豁免失败 → 陷入某状态」，以前只能靠 DM 叙述，
    #: 现在由 cast_spell 直接挂到敌人 / 角色卡上，回合推进据此跳过、攻击姿态也自动改。
    conditions: tuple[str, ...] = ()
    #: 状态撑几轮（0 = 不自动到期，交给 DM / 剧情）。到期由回合推进自动清掉。
    condition_rounds: int = 0
    #: 命中 / 豁免失败时把目标脚下变成什么地形（``困难`` / ``危险``……）。
    #: 蛛网、纠缠这类「区域变难走」的法术靠它落到棋盘上——DM 侧已经没有改地形
    #: 的功能，地形变化只能由系统按数据算。
    terrain: str = ""
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

    @property
    def scaling_tiers(self) -> tuple[tuple[int, str], ...]:
        """戏法成长档位，从 ``scaling`` 文本解析：``[(5, "2d10"), (11, "3d10"), ...]``。

        文本形如「5 级 2d10、11 级 3d10、17 级 4d10」——按角色等级取不超过
        它的最高档（PHB 两版相同：戏法的提升看**角色等级**，不是职业等级）。
        """
        return tuple(
            (int(level), dice)
            for level, dice in _SCALING_RE.findall(self.scaling or "")
        )

    def cantrip_damage(self, level: int) -> str:
        """按角色等级取戏法的伤害骰；没配成长档就返回原伤害。"""
        dice = self.damage
        for tier_level, tier_dice in self.scaling_tiers:
            if int(level) >= tier_level:
                dice = tier_dice
        return dice

    def upcast_dice(self, slot_level: int = 0) -> str:
        """用更高环位施放时**追加**的伤害 / 治疗骰（PHB：每升一环 +NdM）。

        三环法术拿五环位放 → 升两级 → ``2d8``（数据是「每升一环 +1d8」）。
        认不出的写法（「多 1 个目标」「多出一枚飞弹」）与不是骰子的（「+5 临时
        生命」）返回空串，由 DM 按 ``upcast`` 原文裁决——宁可少算，不瞎算。

        :param slot_level: 实际使用的法术位环阶；不高于本法术环阶时返回空串。
        """
        upcast = self.upcast or ""
        if self.level <= 0 or int(slot_level or 0) <= self.level or not upcast:
            return ""
        match = _UPCAST_DICE_RE.search(upcast)
        if match is None or "临时" in upcast:
            return ""
        # 括号里带限定（「+1d6（爆炸伤害）」「+1d6（首次伤害）」）的不敢直接加进
        # 主伤害——那是另一段伤害，交给 DM 按原文处理。
        if upcast[match.end() :].lstrip().startswith("（"):
            return ""
        steps = int(slot_level) - self.level
        count = int(match.group(1) or 1)
        sides = int(match.group(2))
        flat = int((match.group(3) or "0").replace(" ", ""))
        expr = f"{count * steps}d{sides}"
        if flat:
            expr += f"{flat * steps:+d}"
        return expr

    def damage_expr(
        self, modifier: int = 0, *, level: int = 0, slot_level: int = 0
    ) -> str:
        """可掷的伤害表达式：把「施法属性调整值」替换成实际加值。

        :param level: 施法者的**角色等级**——戏法按它成长（17 级的火焰箭是 4d10）。
        :param slot_level: 实际使用的法术位环阶——高于本法术环阶时追加升环骰
            （二环雷鸣波是 3d8，PHB 第一章示例就是这个）。
        """
        text = re.sub(r"（[^）]*）", "", self.damage or "").strip()
        if self.is_cantrip and int(level) > 0:
            text = re.sub(
                r"（[^）]*）", "", self.cantrip_damage(int(level)) or ""
            ).strip()
        extra = self.upcast_dice(slot_level)
        if extra and text:
            text = merge_dice(text, extra)
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
        conditions=as_texts(row.get("conditions")),
        condition_rounds=max(0, int(row.get("condition_rounds", 0) or 0)),
        terrain=str(row.get("terrain", "")),
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
