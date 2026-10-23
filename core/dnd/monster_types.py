"""怪物数据卡的**类型定义**（类，不含数据）。

为什么单独拆一个文件：``monsters.py`` 要引用别的模组的生物表
（:mod:`.stormwreck`），而那些表又要用这里的 ``Monster`` / ``MonsterAttack``。
两者都在 ``core/dnd`` 下时是**循环导入**——Python 只能等一个模块初始化完，
谁先被 import 谁就拿到半成品。

所以把类挪到没有依赖的底层模块：两边都从这儿导入，环就断了。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .attributes import ability_modifier, format_ability_block, get_ability
from .datafiles import as_rows, as_texts
from .skills import get_skill

__all__ = [
    "KIND_RESISTANCES",
    "NPC_KEY_PREFIX",
    "Monster",
    "MonsterAttack",
    "_cr_text",
    "attack_from_row",
    "monster_from_row",
    "proficiency_for_cr",
]

#: 档位 NPC 的 key 前缀（见 :mod:`.npcs`；这里只做前缀判断，不反向导入）
NPC_KEY_PREFIX = "npc"

#: 生物类型级的默认伤害抗性（与条目自带的 :attr:`Monster.resistances` 叠加）。
#: 5e 里不死生物与构装生物普遍对毒素免疫；元素按元素类型另有抗性。
KIND_RESISTANCES: dict[str, tuple[str, ...]] = {
    "亡灵": ("毒素:免疫",),
    "构装": ("毒素:免疫",),
}


def proficiency_for_cr(cr: float) -> int:
    """按挑战等级（CR）取熟练加值：CR 0~4 → +2，5~8 → +3，9~12 → +4 ……"""
    value = float(cr)
    if value >= 17:
        return 6
    if value >= 13:
        return 5
    if value >= 9:
        return 4
    if value >= 5:
        return 3
    return 2


def _cr_text(cr: float) -> str:
    """把 CR 数值排成习惯写法：0.125 → ``1/8``、0.5 → ``1/2``。"""
    if cr >= 1:
        return str(int(cr)) if float(cr).is_integer() else f"{cr:g}"
    if cr == 0.125:
        return "1/8"
    if cr == 0.25:
        return "1/4"
    if cr == 0.5:
        return "1/2"
    return f"{cr:g}"


@dataclass(frozen=True, slots=True)
class MonsterAttack:
    """怪物的一项攻击。"""

    name: str  #: 攻击名，如 ``"弯刀"`` ``"爪击"``
    ability: str  #: 用来结算攻击加值的属性键（str / dex / con / int / wis / cha）
    damage: str  #: 伤害表达式，如 ``"1d6+2"``；纯数字表示固定伤害
    damage_type: str  #: 伤害类型，如 ``"挥砍"``
    reach: int = 5  #: 触及（尺）；远程攻击为 0
    ranged_normal: int = 0  #: 远程常规射程（尺）
    ranged_max: int = 0  #: 远程最大射程（尺）
    notes: str = ""  #: 补充说明（附赠动作、需要豁免等）

    @property
    def is_ranged(self) -> bool:
        return self.ranged_normal > 0

    @property
    def range_text(self) -> str:
        if self.is_ranged:
            return f"射程 {self.ranged_normal}/{self.ranged_max} 尺"
        return f"触及 {self.reach} 尺"

    def label(self) -> str:
        return f"{self.name}（{self.damage} {self.damage_type}，{self.range_text}）"


@dataclass(frozen=True, slots=True)
class Monster:
    """一只怪物（模板，不含具体个体的当前生命值）。"""

    key: str
    cn: str
    en: str
    cr: float  #: 挑战等级
    size: str  #: 体型
    kind: str  #: 生物类型，如 ``"类人"`` ``"亡灵"``
    ac: int
    hp: int  #: 平均生命值（骰出的结果用 hit_dice）
    hit_dice: str  #: 生命骰表达式，如 ``"2d6"``
    speed: int
    abilities: Mapping[str, int]  #: 六大属性值
    attacks: tuple[MonsterAttack, ...] = ()
    saves: tuple[str, ...] = ()  #: 有豁免熟练的属性键
    skills: tuple[str, ...] = ()  #: 有技能熟练的技能键
    traits: tuple[str, ...] = ()  #: 特性
    senses: str = "被动察觉 10"
    languages: str = ""
    desc: str = ""
    #: 额外别名（除 key / 中文名 / 英文名外还能被认出来的写法）
    aliases: tuple[str, ...] = field(default=())
    #: 数据出处：``""`` = 系统自带；``"MM"`` = 官方怪物图鉴；
    #: ``"DoSI"`` = 模组自带（风骸岛之龙等）。DM 查卡时看得见出处。
    source: str = ""
    #: 显式熟练加值；``None`` 表示按 CR 换算。档位 NPC 用它（CR 记 0）
    proficiency_bonus: int | None = None
    #: 伤害抗性条目，形如 ``"火焰"``（抗性）、``"毒素:免疫"``、``"钝击:易伤"``、
    #: ``"挥砍:抗性(非魔法)"``；结算见 :func:`~.conditions.resolve_damage`。
    #: 生物类型级默认抗性（亡灵抗毒等）见 :data:`KIND_RESISTANCES`，与此处叠加。
    resistances: tuple[str, ...] = ()
    #: 栖息地（图鉴给的环境，如「沼泽、幽暗地域」）；空串 = 该条目没写
    environment: str = ""
    #: 宝藏（图鉴给的 treasure，如「圣物」）；空串 = 没有
    treasure: str = ""
    #: 阵营（图鉴给的 alignment，如「混乱邪恶」）；空串 = 没有
    alignment: str = ""
    #: 图鉴附录「按群组」的族群标签（色彩龙 / 金属龙 / 类地精 / 恶魔 / 恐龙…）。
    #: 一只怪可以属于好几个群组；挑怪用 :mod:`.monster_index`（四套分类之一）。
    groups: tuple[str, ...] = ()
    #: **能自动结算**的那部分规则（``res/dnd/monsters/*.yaml`` 的 ``rules`` 块）：
    #: 状态免疫、魔法抗性、传奇抗性、集群战术、再生、自爆……由生成脚本从图鉴的
    #: 特性英文名挑出来，运行时不做文本匹配。读法见 :mod:`.monster_rules`——
    #: 那里每一项都有对应的结算分支，没有的不要往里塞。
    rules: Mapping[str, Any] = field(default_factory=dict)

    # —— 派生数值 ——

    @property
    def all_resistances(self) -> tuple[str, ...]:
        """生物类型默认抗性 + 本条目的抗性（去重保序）。"""
        merged = list(KIND_RESISTANCES.get(self.kind, ()))
        for item in self.resistances:
            if item not in merged:
                merged.append(item)
        return tuple(merged)

    def resistances_text(self) -> str:
        """抗性表的人类可读文本；没有抗性返回空串。"""
        from .conditions import resistances_text

        return resistances_text(self.all_resistances)

    # —— 派生数值 ——

    @property
    def proficiency(self) -> int:
        """熟练加值。

        显式写了 ``proficiency_bonus`` 就用它（**档位 NPC** 走这条：它们的 CR 记 0，
        但数值按队伍等级现算，该有的熟练加值不能少）；否则按 CR 换算。
        """
        if self.proficiency_bonus is not None:
            return int(self.proficiency_bonus)
        return proficiency_for_cr(self.cr)

    @property
    def initiative(self) -> int:
        """先攻加值。"""
        return self.modifier("dex")

    def modifier(self, ability: str) -> int:
        """某属性的调整值。"""
        return ability_modifier(self.abilities.get(get_ability(ability).key, 10))

    @property
    def cr_text(self) -> str:
        return _cr_text(float(self.cr))

    def attack_bonus(self, attack: MonsterAttack | str) -> int:
        """攻击加值 = 熟练加值 + 属性调整值。"""
        item = self.get_attack(attack)
        if item is None:
            return self.proficiency
        return self.proficiency + self.modifier(item.ability)

    def get_attack(self, attack: MonsterAttack | str) -> MonsterAttack | None:
        """按对象 / 名字取一项攻击；没写名字时取第一项。"""
        if isinstance(attack, MonsterAttack):
            return attack
        text = str(attack or "").strip()
        if not text:
            return self.attacks[0] if self.attacks else None
        for item in self.attacks:
            if text == item.name:
                return item
        for item in self.attacks:
            if text in item.name or item.name in text:
                return item
        return None

    def has_save(self, ability: str) -> bool:
        return get_ability(ability).key in self.saves

    def save_bonus(self, ability: str) -> int:
        """豁免加值。"""
        key = get_ability(ability).key
        return self.modifier(key) + (self.proficiency if key in self.saves else 0)

    def skill_bonus(self, skill: str) -> int:
        """技能加值。"""
        skill_obj = get_skill(skill)
        bonus = self.modifier(skill_obj.ability_obj.key)
        return bonus + (self.proficiency if skill_obj.key in self.skills else 0)

    # —— 展示 ——

    def attack_lines(self) -> list[str]:
        lines = []
        for attack in self.attacks:
            lines.append(
                f"{attack.name}：{self.attack_bonus(attack):+d}，"
                f"{attack.damage} {attack.damage_type}，{attack.range_text}"
                + (f"（{attack.notes}）" if attack.notes else "")
            )
        return lines

    def summary(self) -> str:
        """一行摘要，用于战斗开始时列出敌人。"""
        # cr=0 通常意味着「档位 NPC」——数值现算、非图鉴条目，写 CR0 会让 DM 误解
        cr_part = (
            "档位（无正式 CR）"
            if self.cr <= 0 and self.key.startswith(f"{NPC_KEY_PREFIX}/")
            else f"CR{self.cr_text}"
        )
        text = (
            f"【{self.cn}】{self.size}{self.kind} {cr_part}  "
            f"AC {self.ac}  HP {self.hp}({self.hit_dice})  先攻 {self.initiative:+d}  "
            f"速度 {self.speed} 尺"
        )
        if self.attacks:
            first = self.attacks[0]
            text += f"  攻击 {first.name} {self.attack_bonus(first):+d}/{first.damage}"
        return text

    def stat_block(self) -> str:
        """完整数据块（DM 查询用）。"""
        cr_part = (
            "档位（无正式 CR）"
            if self.cr <= 0 and self.key.startswith(f"{NPC_KEY_PREFIX}/")
            else f"CR {self.cr_text}"
        )
        source = f"  出处 {self.source}" if self.source else ""
        lines = [
            f"{self.cn}（{self.en}）  {cr_part}  {self.size}{self.kind}{source}",
            f"AC {self.ac}  HP {self.hp}（{self.hit_dice}）  速度 {self.speed} 尺  "
            f"先攻 {self.initiative:+d}  熟练 {self.proficiency:+d}",
            format_ability_block(self.abilities),
        ]
        if self.saves:
            lines.append(
                "豁免："
                + "、".join(
                    f"{get_ability(k).cn}{self.save_bonus(k):+d}" for k in self.saves
                )
            )
        if self.skills:
            lines.append(
                "技能："
                + "、".join(
                    f"{get_skill(k).cn}{self.skill_bonus(k):+d}" for k in self.skills
                )
            )
        if self.attacks:
            lines.append("攻击：" + "；".join(self.attack_lines()))
        resist = self.resistances_text()
        if resist:
            lines.append(f"伤害抗性 / 免疫：{resist}")
        # 「哪些特性是系统自己管的」——DM 看见就不用自己记（再生几点、有没有状态免疫）
        if self.rules:
            from .monster_rules import rules_of, rules_text

            automatic = rules_text(rules_of(self))
            if automatic:
                lines.append(f"自动结算：{automatic}")
        if self.traits:
            lines.append("特性：" + "；".join(self.traits))
        extras = [
            f"{label}：{text}"
            for label, text in (
                ("群组", "、".join(self.groups)),
                ("栖息地", self.environment),
                ("宝藏", self.treasure),
                ("阵营", self.alignment),
            )
            if text
        ]
        if extras:
            lines.append("　".join(extras))
        if self.senses:
            lines.append(f"感官：{self.senses}")
        if self.languages:
            lines.append(f"语言：{self.languages}")
        if self.desc:
            lines.append(self.desc)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 从数据表读（``res/dnd/monsters*.yaml`` 的一条 → 数据卡）
# --------------------------------------------------------------------------- #
#
# 三张怪物表（系统自带 / 风骸岛 / 失落矿坑）用同一套字段，读法只写一遍。
# 放在这个模块是因为它**没有依赖**：三张表都从这儿取，不会形成环。


def _int(value: object, default: int = 0) -> int:
    """YAML 里同一个数字可能是 int / float / 字符串，这里统一成 int。"""
    if value is None or value == "":
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _num(value: object, default: float = 0.0) -> float:
    """挑战等级这类**可以有小数**的数值（0.125 / 0.5），不能走 int。"""
    if value is None or value == "":
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def attack_from_row(row: Mapping[str, object]) -> MonsterAttack:
    """一条攻击（``attacks`` 里的一项）。"""
    return MonsterAttack(
        name=str(row["name"]),
        ability=str(row["ability"]),
        damage=str(row["damage"]),
        damage_type=str(row["damage_type"]),
        reach=_int(row.get("reach"), 5),
        ranged_normal=_int(row.get("ranged_normal")),
        ranged_max=_int(row.get("ranged_max")),
        notes=str(row.get("notes", "")),
    )


def monster_from_row(row: Mapping[str, object]) -> Monster:
    """一只怪物（``res/dnd/monsters*.yaml`` 的一项）。"""
    raw_abilities = row.get("abilities")
    abilities = raw_abilities if isinstance(raw_abilities, Mapping) else {}
    bonus = row.get("proficiency_bonus")
    attacks = as_rows(row.get("attacks"))
    return Monster(
        key=str(row["key"]),
        cn=str(row["cn"]),
        en=str(row.get("en", "")),
        cr=_num(row["cr"]),
        size=str(row.get("size", "中型")),
        kind=str(row.get("kind", "类人")),
        ac=_int(row["ac"]),
        hp=_int(row["hp"]),
        hit_dice=str(row.get("hit_dice", "")),
        speed=_int(row.get("speed"), 30),
        abilities={str(k): _int(v) for k, v in abilities.items()},
        attacks=tuple(attack_from_row(a) for a in attacks),
        saves=as_texts(row.get("saves")),
        skills=as_texts(row.get("skills")),
        traits=as_texts(row.get("traits")),
        senses=str(row.get("senses", "被动察觉 10")),
        languages=str(row.get("languages", "")),
        desc=str(row.get("desc", "")),
        aliases=as_texts(row.get("aliases")),
        source=str(row.get("source", "")),
        proficiency_bonus=None if bonus is None else _int(bonus),
        resistances=as_texts(row.get("resistances")),
        environment=str(row.get("environment", "")),
        treasure=str(row.get("treasure", "")),
        alignment=str(row.get("alignment", "")),
        groups=as_texts(row.get("groups")),
        rules=dict(row.get("rules") or {}),
    )
