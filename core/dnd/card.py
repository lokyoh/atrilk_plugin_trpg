"""DND 5e 角色卡。

继承 :class:`~..trpg.character.TrpgCharacter`，把属性、种族、职业、技能、装备
组装成一份可参与检定的角色数据对象，并负责：

- 属性加值的结算（基础值 → 种族 / 亚种加值）
- 派生数值（调整值、熟练加值、AC、生命值、先攻）
- 检定 / 豁免（统一走 ``trpg.check.roll_check``）
- 序列化（枚举、装备栈转成普通字典，便于落库与 JSON 传输）
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from functools import lru_cache
from random import Random
from typing import Any

from ..trpg.character import TrpgCharacter
from ..trpg.check import CheckResult, roll_check
from ..trpg.dice import Advantage, roll_d20
from .attributes import (
    ABILITIES,
    Ability,
    AbilitySet,
    format_ability_block,
    get_ability,
    proficiency_bonus,
)
from .background import Background, get_background
from .conditions import (
    ConditionEffect,
    combine_advantage,
    merge_effects,
    normalize_conditions,
    resistances_text,
    resolve_damage,
    save_auto_fail,
    speed_scale,
    split_text,
)
from .equipment import (
    Armor,
    Weapon,
    armor_proficient,
    compute_armor_class,
    get_armor,
    get_weapon,
    parse_item_stack,
    weapon_proficient,
)
from .items import get_item
from .profession import DndClass, get_class
from .race import Race, SubRace, get_race
from .skills import Skill, get_skill
from .spells import Spell, get_spell, max_slots, slot_text

__all__ = [
    "DEATH_CONDITIONS",
    "DeathSaveOutcome",
    "DndCharacter",
    "ability_roll_pool",
    "equipment_vocabulary",
    "split_glued_items",
]

#: 系统维护的倒地相关状态文本（``conditions`` 里这几项不是 DM 的自由文本）
DEATH_CONDITIONS: tuple[str, ...] = ("倒地", "稳定", "死亡")


def _join_reason(reason: str, extra: str) -> str:
    """把两条原因文本并进同一对括号里。"""
    if not reason:
        return f"（{extra}）"
    return reason[:-1] + f"；{extra}）"


#: 「武器」「服装」这类**泛称**：起始包上写的是「任意军用武器」，
#: 玩家照着抄的时候常只抄后半截。切粘连的时候得把它们也认成一个个东西。
_GENERIC_EQUIP_WORDS: tuple[str, ...] = (
    "武器",
    "军用武器",
    "简易武器",
    "护甲",
    "铠甲",
    "服装",
    "衣物",
    "背包",
    "盾牌",
    "戒指",
    "卷轴",
    "圣徽",
    "药水",
    "绳索",
    "火把",
    "干粮",
    "口粮",
    "箭",
    "弩矢",
)

#: 起始包里带数量的写法：「5 支香」只留「支香」，「箭×20」只留「箭」
_GLUE_COUNT_RE = re.compile(r"^\d+\s*")
_GLUE_STACK_RE = re.compile(r"[×xX]\d+$")


@lru_cache(maxsize=1)
def equipment_vocabulary() -> frozenset[str]:
    """所有**认得出的装备名**：装备表 + 魔法物品 + 各职业与背景的起始包。

    为什么要连起始包一起算：圣徽 / 祭司包 / 族谱卷轴 / 精致服装 / 印章戒指
    这些散件不在装备表里，但它们是「一个东西」而不是玩家随口编的杂物。
    """
    from .background import BACKGROUNDS
    from .equipment import ARMORS, WEAPONS
    from .items import ITEMS
    from .profession import CLASSES

    words: set[str] = set(_GENERIC_EQUIP_WORDS)
    for thing in (*ARMORS, *WEAPONS, *ITEMS):
        words.add(str(thing.cn))
        words.update(str(x) for x in (getattr(thing, "aliases", ()) or ()))
    for job in CLASSES:
        for package in job.equipment_packages:
            words.update(str(x).strip() for x in package.items)
    for past in BACKGROUNDS:
        words.update(str(x).strip() for x in past.equipment)
    # 带数量 / 堆叠的写法：把去掉数量那份也收进词表
    for word in list(words):
        head = _GLUE_STACK_RE.sub("", _GLUE_COUNT_RE.sub("", word)).strip()
        if head and head != word:
            words.add(head)
    return frozenset(w for w in words if w)


def split_glued_items(text: str, *, minimum: int = 2) -> list[str]:
    """把「武器盾牌链甲圣徽」这种**没有分隔符的一串**按已知装备名切回几件。

    为什么要有这一步：玩家会照着卡上的装备名连读下来
    （「我装备上武器盾牌链甲圣徽印章戒指精致服装」）——没有顿号、没有空格。
    整串当成一件收下，装备栏里就多出一条十六个字的「装备」，
    之后任何规则都认不出它，玩家只会看见自己多了一件不存在的东西。

    切法是**最长优先 + 回溯**，要求整串拼得满、且至少拼出 ``minimum`` 件；
    拼不满就原样返回一件——「发霉的绳索」这种真·单件不能被拆坏。
    """
    body = str(text or "").strip()
    if not body:
        return []
    if len(body) < 3:
        return [body]
    words = equipment_vocabulary()
    if body in words:
        return [body]

    memo: dict[int, list[str] | None] = {}

    def solve(start: int) -> list[str] | None:
        if start == len(body):
            return []
        if start in memo:
            return memo[start]
        best: list[str] | None = None
        for end in range(len(body), start, -1):
            piece = body[start:end]
            if piece not in words:
                continue
            rest = solve(end)
            if rest is None:
                continue
            candidate = [piece, *rest]
            if best is None or len(candidate) < len(best):
                best = candidate
        memo[start] = best
        return best

    parts = solve(0)
    if parts and len(parts) >= minimum:
        return parts
    return [body]


#: 种族 / 亚种自带的伤害抗性（建卡时自动写进
#: :attr:`DndCharacter.damage_resistances`）。龙裔的吐息抗性取决于自选龙种，
#: 由 DM 在建卡后自行补一条。
RACE_RESISTANCES: dict[tuple[str, str], tuple[str, ...]] = {
    ("dwarf", ""): ("毒素",),  # 矮人坚韧：两个亚种都有
    ("halfling", "stout"): ("毒素",),  # 壮足半身人
    ("tiefling", ""): ("火焰",),  # 炼狱血脉
}


@dataclass(slots=True)
class DeathSaveOutcome:
    """一次死亡豁免的结果。"""

    #: d20 点数
    roll: int
    #: ``success`` / ``failure`` / ``stable`` / ``dead`` / ``revived``
    outcome: str
    successes: int
    failures: int

    @property
    def nat20(self) -> bool:
        return self.roll == 20

    @property
    def nat1(self) -> bool:
        return self.roll == 1

    def describe(self) -> str:
        """给 DM / 玩家看的一句话说明。"""
        if self.outcome == "revived":
            return f"d20={self.roll} → 天然 20！恢复 1 点生命并清醒（死亡豁免清零）"
        label = {
            "stable": "第 3 次成功：伤势稳定",
            "dead": "第 3 次失败：死亡",
            "success": "成功",
            "failure": "失败",
        }.get(self.outcome, self.outcome)
        return (
            f"d20={self.roll} → {label}（成功 {self.successes} / 失败 {self.failures}）"
        )


@dataclass
class DndCharacter(TrpgCharacter):
    """DND 5e 角色卡。"""

    # —— 建卡来源 ——
    race: str = ""  # 种族内部键
    subrace: str = ""  # 亚种键（无则空）
    profession: str = ""  # 职业内部键
    background: str = ""  # 背景内部键
    #: 掷出的属性值（未加种族加值），六项按属性键存放
    base_scores: dict[str, int] = field(default_factory=dict)
    #: 属性值生成方式：``4d6`` / ``standard`` / ``pointbuy``
    score_method: str = "4d6"
    #: 属性值生成时的原始点数，仅用于回放展示
    score_rolls: dict[str, list[int]] = field(default_factory=dict)
    #: 熟练技能（内部键）
    skill_proficiencies: list[str] = field(default_factory=list)
    #: 护甲 / 盾牌熟练项（类别名：轻甲 / 中甲 / 重甲 / 盾牌），职业 + 种族结算
    armor_proficiencies: list[str] = field(default_factory=list)
    #: 武器熟练项（``简易武器`` / ``军用武器`` 或具体武器键 / 中文名）
    weapon_proficiencies: list[str] = field(default_factory=list)
    #: 装备（字符串形式，如 ``长剑`` / ``箭×20``）
    equipment: list[str] = field(default_factory=list)
    #: 已选装备包键（多选一的记录）
    equipment_package: str = ""
    #: 是否持盾
    has_shield: bool = False
    #: 已选职业特性 / 专长等自由文本
    features: list[str] = field(default_factory=list)
    #: 经验值
    exp: int = 0
    #: 当前生命值；``None`` 表示未记录（视为满血）
    current_hp: int | None = None
    #: 临时生命值
    temp_hp: int = 0
    #: 当前状态（中毒、倒地、力竭等自由文本）
    conditions: list[str] = field(default_factory=list)
    #: 力竭等级（0~6）
    exhaustion: int = 0
    #: 口粮份数。5e 的初始装备包给 10 份，长休吃一份；
    #: 吃不上就按饥饿规则计时（见 :meth:`hunger_limit`）。
    #: **刻意不做逐日口粮清单**：一个整数够回答「还剩几顿」，多一套物品记录只是负担。
    rations: int = 10
    #: 连续未进食天数（5e2014「粮食与水」：3 + 体质调整值 天才开始挨饿，
    #: 之后每饿一天 +1 力竭；吃上一天就归零）
    hunger_days: int = 0
    #: 已同调的魔法物品（物品 key）。5e2014「同调」：短休中专注一件物品完成同调，
    #: **同时至多 3 件**，且不能与多于一件**相同**的物品同调。
    attuned: list[str] = field(default_factory=list)
    #: 已知 / 可用法术（法术 key，法师为法术书收录，其他职业为已习得 / 可准备）
    spells: list[str] = field(default_factory=list)
    #: 已消耗的法术位：``{"1": 2}`` 表示 1 环已用 2 个（上限由职业与等级推出）
    spell_slots_used: dict[str, int] = field(default_factory=dict)
    #: 死亡豁免成功次数（0~3；3 次 = 伤势稳定）
    death_saves_success: int = 0
    #: 死亡豁免失败次数（0~3；3 次 = 死亡）
    death_saves_failures: int = 0
    #: 0 血但已稳定，不再需要掷死亡豁免（等治疗 / 长休）
    stable: bool = False
    #: 正在维持的专注法术（法术 key；换专注 / 受伤没过豁免 / 失能时清空）
    concentrating: str = ""
    #: 伤害抗性条目，形如 ``"火焰"``（抗性）、``"毒素:免疫"``、``"钝击:易伤"``；
    #: 种族特性（矮人抗毒、提夫林抗火）在建卡时自动写入，DM 可再增补
    damage_resistances: list[str] = field(default_factory=list)
    #: 已消耗的生命骰数（短休花掉的；长休按 5e 回一半，见
    #: :meth:`regain_hit_dice`）。总数 = 等级，恢复见 :meth:`restore_hit_dice`
    hit_dice_used: int = 0
    #: 游泳速度（尺）；水生种族（海精灵 / 崔斯特 / 蜥蜴人等）在建卡或 DM 设定后非零。
    #: 用于水下战斗——有游泳速度时水下近战武器攻击不受劣势（见 combat.attack）。
    swim_speed: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        self.system = self.system or "DND5e"
        self.skill_proficiencies = list(dict.fromkeys(self.skill_proficiencies))
        self.equipment = list(dict.fromkeys(self.equipment))
        self.conditions = list(dict.fromkeys(self.conditions))
        # 旧存档没有熟练项字段：按职业 + 种族补一遍（之后 DM 可以自行增改）
        if not self.armor_proficiencies and not self.weapon_proficiencies:
            self._fill_default_proficiencies()
        self.death_saves_success = max(0, min(3, int(self.death_saves_success or 0)))
        self.death_saves_failures = max(0, min(3, int(self.death_saves_failures or 0)))
        self.stable = bool(self.stable)
        # 法术统一存成法术 key（旧存档里可能是中文名 / 英文名）
        self.spells = list(
            dict.fromkeys(
                spell.key if (spell := get_spell(item)) else str(item)
                for item in self.spells
            )
        )
        self.spell_slots_used = {
            str(k): max(0, int(v)) for k, v in (self.spell_slots_used or {}).items()
        }
        # 抗性条目规范化：「火焰:免疫」里的全角冒号统一成半角，空条目丢掉
        self.damage_resistances = [
            str(item).strip().replace("：", ":")
            for item in (self.damage_resistances or [])
            if str(item).strip()
        ]
        self.concentrating = str(self.concentrating or "").strip()
        # 生命骰消耗不能超过等级、也不能为负
        self.hit_dice_used = max(0, min(self.level, int(self.hit_dice_used or 0)))
        self.hunger_days = max(0, int(self.hunger_days or 0))
        # 同调去重（手改存档可能写重）
        self.attuned = list(dict.fromkeys(str(k) for k in (self.attuned or [])))

    # —— 生命骰（短休花费 / 长休恢复）——

    @property
    def hit_dice_total(self) -> int:
        """生命骰总数 = 当前等级。"""
        return max(1, int(self.level or 1))

    @property
    def hit_dice_left(self) -> int:
        """还能花的生命骰数。"""
        return max(0, self.hit_dice_total - int(self.hit_dice_used or 0))

    def spend_hit_dice(self, count: int) -> int:
        """花掉 ``count`` 颗生命骰；返回实际花掉的颗数（不够就只花有的）。"""
        want = max(0, int(count or 0))
        spent = min(want, self.hit_dice_left)
        self.hit_dice_used += spent
        return spent

    def regain_hit_dice(self, count: int) -> int:
        """恢复 ``count`` 颗生命骰；返回实际恢复的颗数。"""
        want = max(0, int(count or 0))
        back = min(want, int(self.hit_dice_used or 0))
        self.hit_dice_used -= back
        return back

    # —— 构造 ——

    @classmethod
    def create(
        cls,
        user_id: str,
        *,
        name: str,
        race: Race | str,
        profession: DndClass | str,
        background: Background | str | None = None,
        subrace: SubRace | str | None = None,
        base_scores: dict[str, int] | None = None,
        score_method: str = "4d6",
        score_rolls: dict[str, list[int]] | None = None,
        skills: list[str] | None = None,
        equipment: list[str] | None = None,
        equipment_package: str = "",
        spells: list[str] | None = None,
        damage_resistances: list[str] | None = None,
    ) -> DndCharacter:
        """按建卡流程的产出一份角色卡。"""
        race_obj = get_race(race)
        sub_obj = race_obj.resolve_subrace(subrace) if subrace else None
        class_obj = get_class(profession)
        resistances = list(damage_resistances or [])
        # 种族 / 亚种自带的抗性（矮人抗毒、提夫林抗火……）自动补上；
        # 先查（种族, 亚种）组合，再回退到（种族, 空）的种族级条目
        sub_key = sub_obj.key if sub_obj else ""
        for item in RACE_RESISTANCES.get(
            (race_obj.key, sub_key),
            RACE_RESISTANCES.get((race_obj.key, ""), ()),
        ):
            if item not in resistances:
                resistances.append(item)
        return cls(
            user_id=str(user_id),
            name=name,
            race=race_obj.key,
            subrace=sub_obj.key if sub_obj else "",
            profession=class_obj.key,
            background=get_background(background).key if background else "",
            base_scores=dict(base_scores or {}),
            score_method=score_method,
            score_rolls=dict(score_rolls or {}),
            skill_proficiencies=list(skills or []),
            armor_proficiencies=list(
                dict.fromkeys(
                    (*class_obj.armor_proficiencies, *race_obj.armor_of(sub_obj))
                )
            ),
            weapon_proficiencies=list(
                dict.fromkeys(
                    (*class_obj.weapon_proficiencies, *race_obj.weapons_of(sub_obj))
                )
            ),
            equipment=list(equipment or []),
            equipment_package=equipment_package,
            has_shield=any("盾" in str(item) for item in (equipment or [])),
            spells=[s.key for s in (get_spell(x) for x in (spells or [])) if s],
            damage_resistances=resistances,
        )

    # —— 规则对象 ——

    @property
    def race_obj(self) -> Race | None:
        return get_race(self.race) if self.race else None

    @property
    def subrace_obj(self) -> SubRace | None:
        race = self.race_obj
        if not race or not self.subrace:
            return None
        try:
            return race.resolve_subrace(self.subrace)
        except KeyError:
            return None

    @property
    def profession_obj(self) -> DndClass | None:
        return get_class(self.profession) if self.profession else None

    @property
    def background_obj(self) -> Background | None:
        return get_background(self.background) if self.background else None

    @property
    def proficiency(self) -> int:
        """熟练加值。"""
        return proficiency_bonus(self.level)

    # —— 护甲 / 武器熟练项 ——

    def _fill_default_proficiencies(self) -> None:
        """按职业 + 种族（含亚种）填出默认熟练项（建卡与旧存档升级都用它）。"""
        armor: list[str] = []
        weapons: list[str] = []
        profession = self.profession_obj
        if profession:
            armor.extend(profession.armor_proficiencies)
            weapons.extend(profession.weapon_proficiencies)
        race = self.race_obj
        if race:
            sub = self.subrace_obj
            armor.extend(race.armor_of(sub))
            weapons.extend(race.weapons_of(sub))
        self.armor_proficiencies = list(dict.fromkeys(armor))
        self.weapon_proficiencies = list(dict.fromkeys(weapons))

    def has_weapon_proficiency(self, weapon: Weapon | str) -> bool:
        """是否有这件武器的熟练项（没有就不能加熟练加值）。"""
        return weapon_proficient(weapon, self.weapon_proficiencies)

    def worn_body_armor(self) -> Armor | None:
        """当前穿在身上的护甲（盾牌不算；取第一件认得出的）。"""
        for item in self.equipment:
            armor = get_armor(item)
            if armor and not armor.is_shield:
                return armor
        return None

    def armor_penalty(self) -> tuple[bool, str]:
        """``(是否受罚, 原因)``——穿了不会的护甲 / 盾牌。

        5e：着装不具有熟练项的护甲或盾牌时，属性检定、豁免检定、以力量或敏捷
        进行的攻击检定都具有劣势，且不能在该着装下施展法术。

        **空列表 = 什么都不会**（武僧 / 术士 / 法师的护甲熟练项本就是空的），
        不是「没登记」——没登记的旧存档在 ``__post_init__`` 里已经补过了。
        只有连种族职业都没有的卡才无从判断，那种放行。
        """
        if not self.armor_proficiencies and not (self.profession or self.race):
            return False, ""
        body = self.worn_body_armor()
        shield = next(
            (a for a in (get_armor(item) for item in self.equipment) if a and a.is_shield),
            None,
        )
        bad_name = ""
        if body is not None and not armor_proficient(body, self.armor_proficiencies):
            bad_name = body.cn
        elif shield is not None and not armor_proficient(shield, self.armor_proficiencies):
            bad_name = shield.cn
        if not bad_name:
            return False, ""
        return True, f"（未熟练：{bad_name}——检定 / 豁免 / 攻击劣势，且无法施法）"

    def heavy_armor_speed_penalty(self) -> int:
        """重甲力量不足的速度惩罚（5e：达不到「力量 13/15」要求，速度 −10 尺）。"""
        body = self.worn_body_armor()
        if body is None or not body.strength_required:
            return 0
        return 10 if self._str_score() < body.strength_required else 0

    def _str_score(self) -> int:
        """力量属性值（重甲要求按**属性值**判，不是调整值）。"""
        return self.abilities["str"]

    def can_cast(self) -> tuple[bool, str]:
        """``(能否施法, 原因)``——不会的护甲下不能施法。"""
        penalized, reason = self.armor_penalty()
        if penalized:
            return False, reason
        return True, ""

    # —— 属性 ——

    @property
    def abilities(self) -> AbilitySet:
        """结算种族 / 亚种加值后的属性。"""
        scores = AbilitySet(self.base_scores or {a.key: 10 for a in ABILITIES})
        race = self.race_obj
        if race:
            scores.apply_bonus(race.bonus_of(self.subrace_obj))
        return scores

    def modifier(self, ability: Ability | str) -> int:
        """取某项属性的调整值。"""
        return self.abilities.modifier(ability)

    def condition_bonus(self, ability: Ability | str, *, save: bool = False) -> int:
        """增益类状态给的属性加值（祝福的 +1、元素亲和等）。

        与优势 / 劣势不同：**数值加值可叠加**（5e 规则），所以这里返回的是
        所有来源的总和，不是「有 / 没有」。
        """
        effect = merge_effects(self.conditions, exhaustion=self.exhaustion)
        pairs = effect.save_bonus_abilities if save else effect.check_bonus_abilities
        key = get_ability(ability).key
        return sum(int(value) for who, value in pairs if who in (key, "all"))

    def skill_bonus(self, skill: Skill | str) -> int:
        """技能检定加值 = 属性调整值 +（熟练则加熟练加值）+ 状态给的加值。"""
        skill_obj = get_skill(skill)
        bonus = self.abilities.modifier(skill_obj.ability)
        if skill_obj.key in self.skill_proficiencies:
            bonus += self.proficiency
        return bonus + self.condition_bonus(skill_obj.ability)

    def save_bonus(self, ability: Ability | str) -> int:
        """豁免加值 = 属性调整值 +（职业豁免熟练则加熟练加值）+ 状态给的加值。"""
        ability_obj = get_ability(ability)
        bonus = self.abilities.modifier(ability_obj)
        profession = self.profession_obj
        if profession and ability_obj.key in profession.saves:
            bonus += self.proficiency
        return bonus + self.condition_bonus(ability_obj, save=True)

    def passive_score(self, skill: Skill | str) -> int:
        """被动检定值（10 + 技能加值），常用于被动察觉。"""
        return 10 + self.skill_bonus(skill)

    # —— 派生数值 ——

    @property
    def max_hp(self) -> int:
        """生命值上限。

        1 级取生命骰最大值 + 体质调整值；之后每级取生命骰平均值（向上取整）+ 体质调整值。
        力竭 4 级起生命上限减半。
        """
        profession = self.profession_obj
        con = self.modifier("con")
        if not profession:
            hp = max(1, 8 + con)
        else:
            hp = profession.level1_hp + con
            average = profession.hit_die // 2 + 1
            hp += (self.level - 1) * (average + con)
        scale = merge_effects(self.conditions, exhaustion=self.exhaustion).hp_max_scale
        if scale < 1.0:
            hp = max(1, int(hp * scale))
        return max(1, hp)

    @property
    def hp(self) -> int:
        """当前生命值；未记录（``None``）时视为满血，取值 0~``max_hp``。"""
        if self.current_hp is None:
            return self.max_hp
        return max(0, min(int(self.current_hp), self.max_hp))

    @property
    def is_downed(self) -> bool:
        """生命值是否已降至 0（倒地 / 濒死）。"""
        return self.hp <= 0

    @property
    def is_dead(self) -> bool:
        """死亡豁免累计 3 次失败（或受过致命伤）。"""
        return self.death_saves_failures >= 3

    @property
    def is_dying(self) -> bool:
        """倒地濒死：0 血、未稳定、未死亡——每个自己的回合要掷死亡豁免。"""
        return self.is_downed and not self.stable and not self.is_dead

    @property
    def is_stable_down(self) -> bool:
        """0 血但已稳定：昏迷，不再掷死亡豁免，等治疗或长休。"""
        return self.is_downed and self.stable and not self.is_dead

    # —— 倒地 / 死亡豁免 ——

    def _set_death_condition(self, label: str) -> None:
        """把倒地相关状态换成 ``label``（其余自由状态不动）。"""
        self.conditions = [c for c in self.conditions if c not in DEATH_CONDITIONS]
        self.conditions.append(label)

    def _set_unconscious(self, value: bool) -> None:
        """挂上 / 取下「昏迷」——0 血的玩家按 5e 就是失去意识的。"""
        if value:
            if "昏迷" not in self.conditions:
                self.conditions.append("昏迷")
        else:
            self.conditions = [c for c in self.conditions if c != "昏迷"]

    def mark_downed(self) -> None:
        """标记倒地濒死（生命归零，或稳定后又挨打）。"""
        self.stable = False
        self._set_death_condition("倒地")
        # 0 血 = 昏迷：被攻击有优势、力量 / 敏捷豁免自动失败（5e）
        self._set_unconscious(True)

    def mark_stable(self) -> None:
        """死亡豁免 3 次成功：伤势稳定（仍 0 血昏迷，不再掷豁免）。"""
        self.stable = True
        self._set_death_condition("稳定")
        self._set_unconscious(True)

    def mark_dead(self) -> None:
        """标记死亡（3 次失败 / 致命伤）。"""
        self.stable = False
        self.death_saves_failures = 3
        self._set_death_condition("死亡")
        self._set_unconscious(False)
        self.concentrating = ""

    def clear_death_state(self) -> None:
        """清空死亡豁免进度与倒地 / 稳定 / 死亡状态（受治疗、复活、DM 裁决用）。"""
        self.stable = False
        self.death_saves_success = 0
        self.death_saves_failures = 0
        self.conditions = [
            c for c in self.conditions if c not in DEATH_CONDITIONS and c != "昏迷"
        ]

    def add_death_failure(self, count: int = 1) -> str:
        """倒地状态下再受伤：累计失败（暴击算 2 次）；返回说明文本。"""
        self.death_saves_failures = min(
            3, self.death_saves_failures + max(0, int(count))
        )
        if self.is_dead:
            self.mark_dead()
            return f"死亡豁免失败 {count} 次 → 累计 3 次，死亡"
        self.mark_downed()
        return (
            f"死亡豁免失败 {count} 次"
            f"（成功 {self.death_saves_success} / 失败 {self.death_saves_failures}）"
        )

    def die_instantly(self) -> str:
        """致命伤：单次伤害达到或超过生命上限，立即死亡。"""
        self.mark_dead()
        return "受到致命伤（单次伤害达到或超过生命上限），立即死亡"

    def death_save(self, *, rng: Random | None = None) -> DeathSaveOutcome:
        """掷一次死亡豁免：d20 无加值，DC 10。

        - ≥10 成功 +1；≤9 失败 +1；
        - **天然 20**：恢复 1 点生命并清醒（进度清零）；
        - **天然 1**：失败 +2；
        - 3 次成功 = 稳定；3 次失败 = 死亡。
        """
        value = roll_d20(rng=rng)[1]
        if value == 20:
            self.current_hp = 1
            self.clear_death_state()
            return DeathSaveOutcome(value, "revived", 0, 0)
        if value == 1:
            self.death_saves_failures = min(3, self.death_saves_failures + 2)
        elif value >= 10:
            self.death_saves_success = min(3, self.death_saves_success + 1)
        else:
            self.death_saves_failures = min(3, self.death_saves_failures + 1)

        if self.is_dead:
            self.mark_dead()
            outcome = "dead"
        elif self.death_saves_success >= 3:
            self.mark_stable()
            outcome = "stable"
        else:
            outcome = "success" if value >= 10 else "failure"
        return DeathSaveOutcome(
            value, outcome, self.death_saves_success, self.death_saves_failures
        )

    def revive(self, hp: int = 1) -> str:
        """复活 / 治疗清醒：清零死亡豁免、去掉倒地状态并回血。"""
        self.clear_death_state()
        self.current_hp = None if int(hp) <= 0 else max(1, min(int(hp), self.max_hp))
        return f"已清醒，生命 {self.hp}/{self.max_hp}"

    def death_save_text(self) -> str:
        """生存状态文本；没倒地时返回空串。"""
        if self.is_dead:
            return "死亡"
        if self.is_stable_down:
            return "稳定（昏迷，0 血）"
        if not self.is_downed:
            return ""
        return f"濒死（死亡豁免 {self.death_saves_success} 成功 / {self.death_saves_failures} 失败）"

    @property
    def armor_class(self) -> int:
        """护甲等级，含无甲防御（野蛮人 / 武僧）判定。"""
        return self.armor_class_detail[0]

    @property
    def armor_class_detail(self) -> tuple[int, str]:
        """``(AC, 说明)``。"""
        profession = self.profession_obj
        unarmored: int | None = None
        if profession:
            if profession.key == "barbarian" and not self._wears_armor:
                unarmored = 10 + self.modifier("dex") + self.modifier("con")
            elif profession.key == "monk" and not self._wears_armor:
                unarmored = 10 + self.modifier("dex") + self.modifier("wis")
        if unarmored is not None:
            shield_part = 2 if self.has_shield else 0
            return (
                unarmored + shield_part,
                f"无甲防御 {unarmored - self.modifier('dex')} {self.modifier('dex'):+d}",
            )
        return compute_armor_class(
            self.modifier("dex"),
            armors=tuple(self.equipment),
            has_shield=self.has_shield,
        )

    @property
    def _wears_armor(self) -> bool:
        """身上是否有真正的护甲（盾牌不算）。"""
        for item in self.equipment:
            armor = get_armor(item)
            if armor and not armor.is_shield:
                return True
        return False

    @property
    def initiative(self) -> int:
        """先攻加值。"""
        return self.modifier("dex")

    def primary_weapons(self) -> list[Weapon]:
        """从装备里挑出可用的武器。"""
        weapons: list[Weapon] = []
        for item in self.equipment:
            stack = parse_item_stack(item)
            weapon = get_weapon(stack.name)
            if weapon and weapon not in weapons:
                weapons.append(weapon)
        return weapons

    def attack_bonus(self, weapon: Weapon | str) -> int:
        """攻击检定加值：灵巧武器取力量 / 敏捷较高者，远程武器取敏捷。

        **武器熟练**：不具有该武器的熟练项时**不能**加熟练加值（5e），
        只留属性调整值——法师抡长剑就是这么个后果。
        """
        weapon_obj = get_weapon(weapon)
        if weapon_obj is None:
            # 徒手打击：人人都有熟练项
            return self.proficiency
        if weapon_obj.is_ranged:
            ability_mod = self.modifier("dex")
        elif weapon_obj.is_finesse:
            ability_mod = max(self.modifier("str"), self.modifier("dex"))
        else:
            ability_mod = self.modifier("str")
        if not self.has_weapon_proficiency(weapon_obj):
            return ability_mod
        return self.proficiency + ability_mod

    def damage_bonus(self, weapon: Weapon | str) -> int:
        """伤害加值（与攻击加值的属性选择一致）。"""
        weapon_obj = get_weapon(weapon)
        if weapon_obj is None:
            return 0
        if weapon_obj.is_ranged:
            return self.modifier("dex")
        if weapon_obj.is_finesse:
            return max(self.modifier("str"), self.modifier("dex"))
        return self.modifier("str")

    # —— 法术 ——

    @property
    def is_caster(self) -> bool:
        """是否具备施法能力（职业带施法属性）。"""
        profession = self.profession_obj
        return bool(profession and profession.is_caster)

    @property
    def casting_ability(self) -> str:
        """施法属性内部键（"" = 非施法者）。"""
        profession = self.profession_obj
        return profession.casting if profession else ""

    @property
    def casting_text(self) -> str:
        """施法属性中文名（"" = 非施法者）。"""
        profession = self.profession_obj
        return profession.casting_text if profession else ""

    @property
    def spell_attack_bonus(self) -> int:
        """法术攻击加值 = 熟练加值 + 施法属性调整值。"""
        if not self.casting_ability:
            return self.proficiency
        return self.proficiency + self.modifier(self.casting_ability)

    @property
    def spell_save_dc(self) -> int:
        """法术豁免 DC = 8 + 熟练加值 + 施法属性调整值。"""
        if not self.casting_ability:
            return 8 + self.proficiency
        return 8 + self.proficiency + self.modifier(self.casting_ability)

    def max_spell_slots(self) -> dict[int, int]:
        """按职业与等级推出的法术位上限 ``{环阶: 数量}``。"""
        profession = self.profession_obj
        if not profession or not profession.is_caster:
            return {}
        return max_slots(profession.caster_kind, self.level)

    def spent_spell_slots(self) -> dict[int, int]:
        """已消耗的法术位 ``{环阶: 数量}``。"""
        out: dict[int, int] = {}
        for key, value in self.spell_slots_used.items():
            try:
                level = int(key)
            except (TypeError, ValueError):
                continue
            out[level] = out.get(level, 0) + max(0, int(value))
        return out

    def spell_slots(self) -> dict[int, int]:
        """剩余法术位 ``{环阶: 剩余}``。"""
        spent = self.spent_spell_slots()
        return {
            level: max(0, total - spent.get(level, 0))
            for level, total in self.max_spell_slots().items()
        }

    def spell_slots_text(self) -> str:
        """法术位展示文本，如 ``一环 2/3、二环 1/2``。"""
        return slot_text(self.max_spell_slots(), self.spent_spell_slots())

    def slot_available(self, level: int) -> bool:
        """某个环阶是否还有剩余法术位（0 环戏法永远为真）。"""
        if int(level) <= 0:
            return True
        return self.spell_slots().get(int(level), 0) > 0

    def highest_slot(self) -> int:
        """剩余的最高环法术位（无则 0）。"""
        remaining = self.spell_slots()
        return max((lv for lv, n in remaining.items() if n > 0), default=0)

    def spend_slot(self, level: int) -> bool:
        """消耗一个指定环阶的法术位，成功返回 ``True``。0 环不消耗。"""
        level = int(level)
        if level <= 0:
            return True
        if not self.slot_available(level):
            return False
        key = str(level)
        self.spell_slots_used[key] = int(self.spell_slots_used.get(key, 0)) + 1
        return True

    def hunger_limit(self) -> int:
        """连续多少天不进食才开始掉力竭（5e2014：3 + 体质调整值，至少 1 天）。"""
        return max(1, 3 + self.modifier("con"))

    # —— 同调（5e2014「同调」）——

    def attune(self, name: str) -> tuple[bool, str]:
        """与一件魔法物品同调（短休中专注完成）。

        规则：需同调的物品才谈得上同调；**同时至多 3 件**；不能与多于一件
        **相同**的物品同调（两枚防护戒指只认一枚）。

        :return: ``(是否成功, 说明)``
        """
        item = get_item(name)
        if item is None:
            return False, f"「{name}」不是表里的魔法物品，谈不到同调。"
        if not item.requires_attunement:
            return False, f"{item.cn}不需要同调，拿着就能用。"
        if item.key in self.attuned:
            return False, f"已经与{item.cn}同调了。"
        if len(self.attuned) >= 3:
            held = "、".join(
                (get_item(key).cn if get_item(key) else key) for key in self.attuned
            )
            return (
                False,
                f"同调已满 3 件（{held}）——先终止一个再同调{item.cn}。",
            )
        self.attuned = [*self.attuned, item.key]
        return True, f"与{item.cn}完成同调（{len(self.attuned)}/3）。"

    def unattune(self, name: str) -> tuple[bool, str]:
        """终止与某件物品的同调（短休中主动放弃，或物品被拿走 / 失效）。"""
        item = get_item(name)
        key = item.key if item else str(name).strip()
        if key not in self.attuned:
            return False, f"本来就没和「{name}」同调。"
        self.attuned = [k for k in self.attuned if k != key]
        label = item.cn if item else key
        return True, f"已终止与{label}的同调（剩 {len(self.attuned)}/3）。"

    def attunement_text(self) -> str:
        """同调清单文本（空则空串）。"""
        if not self.attuned:
            return ""
        names = [
            (get_item(key).cn if get_item(key) else key) for key in self.attuned
        ]
        return f"同调 {len(self.attuned)}/3：" + "、".join(names)

    def ammo_count(self, names: tuple[str, ...] | list[str]) -> int:
        """装备栏里符合的弹药剩余数（``箭×20`` → 20）。"""
        total = 0
        for item in self.equipment:
            stack = parse_item_stack(item)
            if stack.name in names:
                total += max(0, stack.count)
        return total

    def spend_ammo(self, names: tuple[str, ...] | list[str]) -> tuple[bool, str, str]:
        """花掉一枚弹药（5e：弹药武器每次攻击都要耗一枚）。

        :return: ``(是否花掉了, 说明, 实际耗掉的弹药名)``——没有弹药时
            返回 ``(False, ...)``，由调用方决定是打回 DM 还是按叙述裁决。
        """
        for index, item in enumerate(self.equipment):
            stack = parse_item_stack(item)
            if stack.name not in names or stack.count <= 0:
                continue
            left = stack.count - 1
            if left > 0:
                self.equipment[index] = f"{stack.name}×{left}"
            else:
                # 扣到 0 就整条删掉——留着光名字会被解析成「还剩 1」
                self.equipment.pop(index)
            return (
                True,
                f"（消耗 1 枚{stack.name}，剩 {left}）",
                stack.name,
            )
        return False, f"没有{' / '.join(names)}了", ""

    def restore_slots(self, *, kind: str = "long") -> None:
        """恢复法术位：长休全部恢复；短休只恢复契约魔法（邪术师）。"""
        if kind == "long":
            self.spell_slots_used = {}
            return
        profession = self.profession_obj
        if profession and profession.caster_kind == "pact":
            self.spell_slots_used = {}

    def _owns(self, item: str, *, extra: list[str] | None = None) -> bool:
        """这件东西是不是**已经在身上**了（泛称也算：``武器`` 被 ``任意军用武器`` 盖住）。

        :param extra: 同一次调用里刚收下的几件——一次收「圣徽、圣徽」不该落两条。
        """
        name = str(item or "").strip()
        if not name:
            return True
        for owned in [*self.equipment, *(extra or [])]:
            owned = str(owned)
            if name == owned:
                return True
            # 泛称被具体名盖住：身上有「任意军用武器」时，再收一条「武器」是噪音。
            # 只对两个字以上的名字这么做，免得单字被随便吞掉。
            if len(name) >= 2 and name in owned:
                return True
        return False

    def knows_item(self, item: str) -> bool:
        """这个名字是不是**「一个东西」**：装备表 / 魔法物品 / 起始包散件 / 身上已有。

        玩家侧的快捷收纳只收认得出的——「喷火龙鳞甲」这种编出来的名字，系统
        没有本事判断它存不存在，所以不接管、原话交回 DM（它才是知道箱子里
        有什么的那一个）。DM 侧的工具不走这道门：它说箱子里有，那就是有。
        """
        name = str(item or "").strip()
        if not name:
            return False
        if name in equipment_vocabulary():
            return True
        return self._owns(name)

    def equip(self, items: str | list[str]) -> tuple[list[str], list[str]]:
        """把东西收进装备栏（追加，不覆盖）。

        认得的（装备表里的武器 / 护甲、**魔法物品表里的物品**）换成规范名再收；
        认不出的**照原样收下**——战利品里有杂物（箭 / 绳索 / 干粮），
        它们不进 AC 计算但确实是玩家拿到的东西，一律丢掉会让玩家觉得东西凭空消失。
        DM 工具与玩家侧快捷入口共用这一套。

        **身上已经有的不再收一遍**：否则「把起始装备再装备上」会留下一串重复，
        而泛称（「武器」）会跟具体名（「任意军用武器」）并存成两条。

        :return: ``(这次真收下的规范名列表, 这次真收下的认不出的原名列表)``
        """
        wanted = (
            [str(x).strip() for x in items if str(x).strip()]
            if isinstance(items, list | tuple)
            else split_text(str(items or ""))
        )
        # 自愈：旧记录里可能躺着一条「把几件装备粘成一串」的假条目
        # （曾经把「武器盾牌链甲圣徽印章戒指精致服装」当一件收下）。能完整拆回
        # 几件的就拆开；拆出来的泛称（「武器」）若已被更具体的名字盖住就不留，
        # 于是那条假条目自然消失。
        flat = [piece for item in self.equipment for piece in split_glued_items(str(item))]
        self.equipment = [
            name
            for name in dict.fromkeys(flat)
            if not any(
                name != other and len(name) >= 2 and name in other for other in flat
            )
        ]
        known: list[str] = []
        unknown: list[str] = []
        for item in wanted:
            # 先按**玩家写的那个名字**判一次：他写「盾牌」而身上就写着「盾牌」时，
            # 规范化会把它换成「木盾」，于是同一个盾又被收成第二件。
            if self._owns(item, extra=[*known, *unknown]):
                continue
            armor = get_armor(item) or get_weapon(item)
            if armor is not None:
                if not self._owns(armor.cn, extra=[*known, *unknown]):
                    known.append(armor.cn)
                continue
            # 魔法物品：认得出就用规范名（「精灵靴」→「精灵之靴」），
            # 认不出的仍照原样收下（战利品里的杂物不能丢）。
            magic = get_item(item)
            name = magic.cn if magic is not None else item
            if self._owns(name, extra=[*known, *unknown]):
                continue
            (known if magic is not None else unknown).append(name)
        self.equipment = list(dict.fromkeys([*self.equipment, *known, *unknown]))
        # 盾牌影响 AC 结算，收下盾牌时顺手更新标记
        self.has_shield = any("盾" in str(x) for x in self.equipment)
        return known, unknown

    def known_spells(self) -> list[Spell]:
        """解析卡上的法术列表（按环阶、名字排序，未识别的条目跳过）。"""
        found: list[Spell] = []
        for item in self.spells:
            spell = get_spell(item)
            if spell and spell not in found:
                found.append(spell)
        return sorted(found, key=lambda s: (s.level, s.cn))

    def knows_spell(self, value: Spell | str) -> bool:
        """卡上是否有这个法术。"""
        spell = get_spell(value)
        return bool(spell and spell.key in self.spells)

    def learn_spell(self, value: Spell | str) -> Spell | None:
        """把法术记进卡里（已存在则忽略），返回法术对象；认不出返回 ``None``。"""
        spell = get_spell(value)
        if spell is None:
            return None
        if spell.key not in self.spells:
            self.spells.append(spell.key)
        return spell

    def forget_spell(self, value: Spell | str) -> bool:
        """从卡上删掉一个法术。"""
        spell = get_spell(value)
        if spell is None or spell.key not in self.spells:
            return False
        self.spells.remove(spell.key)
        return True

    # —— 检定 ——

    def _check_advantage(
        self,
        kind: str,
        explicit: Advantage,
    ) -> tuple[Advantage, str]:
        """把状态给的优势 / 劣势叠到调用方显式指定的姿态上。

        :param kind: ``check``（属性 / 技能）/ ``save``（豁免）/ ``attack``（攻击）。
        :return: ``(最终姿态, 状态原因文本)``。
        """
        effect = merge_effects(self.conditions, exhaustion=self.exhaustion)
        state_adv = {
            "check": effect.own_check,
            "save": effect.own_save,
            "attack": effect.own_attack,
        }[kind]
        if state_adv is Advantage.NORMAL:
            combined, reason = explicit, ""
        else:
            combined = state_adv
            if explicit is not Advantage.NORMAL:
                # 状态与显式指定相遇：优势劣势抵消
                combined = state_adv if explicit is state_adv else Advantage.NORMAL
            names = [c for c in normalize_conditions(self.conditions) if c]
            if self.exhaustion:
                names.append(f"力竭 {self.exhaustion} 级")
            kind_cn = {"check": "检定", "save": "豁免", "attack": "攻击"}[kind]
            stance = (
                "优势"
                if combined is Advantage.ADVANTAGE
                else ("劣势" if combined is Advantage.DISADVANTAGE else "优劣抵消")
            )
            reason = (
                f"（{'、'.join(names)}：{kind_cn}{stance}）" if names else ""
            )
        if kind in ("check", "save"):
            # 不会的护甲：所有属性检定与豁免检定都有劣势（5e）
            combined, reason = self._apply_armor_penalty(combined, reason)
        return combined, reason

    def _apply_armor_penalty(
        self, advantage: Advantage, reason: str
    ) -> tuple[Advantage, str]:
        """把「不会的护甲 / 盾牌」带来的劣势叠上去（属性检定 / 豁免 / 物理攻击）。

        5e：着装不具有熟练项的护甲或盾牌时，属性检定、豁免检定、以力量或敏捷
        进行的攻击检定都具有劣势。
        """
        penalized, note = self.armor_penalty()
        if not penalized:
            return advantage, reason
        combined = combine_advantage(advantage, Advantage.DISADVANTAGE)
        return combined, _join_reason(reason, note.lstrip("（").rstrip("）"))

    def resolve_attack_advantage(self, explicit: Advantage) -> tuple[Advantage, str]:
        """攻击检定的最终优势姿态：把自身状态（中毒 / 力竭 / 恐慌……）与
        调用方显式指定的姿态合并（相遇抵消），并给出原因文本。

        不走武器的攻击（徒手、推撞、擒抱）用这个方法拿最终姿态——
        这些都以力量 / 敏捷进行，所以不会的护甲照样给劣势。
        """
        final, reason = self._check_advantage("attack", explicit)
        return self._apply_armor_penalty(final, reason)

    def check_skill(
        self,
        skill: Skill | str,
        dc: int,
        *,
        advantage: Advantage = Advantage.NORMAL,
        rng: Random | None = None,
    ) -> CheckResult:
        """技能检定（自动应用中毒 / 恐慌 / 力竭等状态效果）。"""
        skill_obj = get_skill(skill)
        final, reason = self._check_advantage("check", advantage)
        result = roll_check(
            self.skill_bonus(skill_obj),
            dc,
            kind=f"{skill_obj.label}检定",
            advantage=final,
            rng=rng,
        )
        if reason:
            result.kind += reason
        return result

    def check_ability(
        self,
        ability: Ability | str,
        dc: int,
        *,
        advantage: Advantage = Advantage.NORMAL,
        rng: Random | None = None,
    ) -> CheckResult:
        """属性检定（无关联技能的裸检定）。"""
        ability_obj = get_ability(ability)
        final, reason = self._check_advantage("check", advantage)
        # 野蛮人狂暴：力量检定与力量豁免有优势
        effect = merge_effects(self.conditions)
        if ability_obj.key in effect.check_adv_abilities:
            final = combine_advantage(final, Advantage.ADVANTAGE)
            reason = _join_reason(reason, "狂暴：此检定有优势")
        result = roll_check(
            self.modifier(ability_obj),
            dc,
            kind=f"{ability_obj.cn}检定",
            advantage=final,
            rng=rng,
        )
        if reason:
            result.kind += reason
        return result

    def check_save(
        self,
        ability: Ability | str,
        dc: int,
        *,
        advantage: Advantage = Advantage.NORMAL,
        rng: Random | None = None,
    ) -> CheckResult:
        """豁免检定（自动应用状态效果；麻痹 / 震慑 / 昏迷 / 石化直接失败）。"""
        ability_obj = get_ability(ability)
        final, reason = self._check_advantage("save", advantage)
        effect = merge_effects(self.conditions)
        if ability_obj.key in effect.save_adv_abilities:
            final = combine_advantage(final, Advantage.ADVANTAGE)
            reason = _join_reason(reason, "狂暴：此豁免有优势")
        result = roll_check(
            self.save_bonus(ability_obj),
            dc,
            kind=f"{ability_obj.cn}豁免",
            advantage=final,
            rng=rng,
        )
        if save_auto_fail(self.conditions, ability_obj.key):
            # 状态导致的自动失败：照掷骰（给玩家看点数），但结果强制为失败
            result.auto_critical = False
            result.forced_failure = True
            reason = _join_reason(reason, "状态导致自动失败")
        if reason:
            result.kind += reason
        return result

    def check_attack(
        self,
        weapon: Weapon | str,
        target_ac: int,
        *,
        advantage: Advantage = Advantage.NORMAL,
        rng: Random | None = None,
    ) -> CheckResult:
        """攻击检定（对抗目标 AC；自动应用攻击方状态与护甲惩罚）。"""
        weapon_obj = get_weapon(weapon)
        label = weapon_obj.cn if weapon_obj else str(weapon)
        final, reason = self._check_advantage("attack", advantage)
        # 武器攻击都以力量 / 敏捷进行：不会的护甲给劣势（法术攻击不走这里）
        final, reason = self._apply_armor_penalty(final, reason)
        if weapon_obj is not None and not self.has_weapon_proficiency(weapon_obj):
            reason = _join_reason(reason, f"未熟练：{weapon_obj.cn}，不加熟练加值")
        result = roll_check(
            self.attack_bonus(weapon_obj or weapon),
            target_ac,
            kind=f"{label}攻击",
            advantage=final,
            # 攻击检定：天然 20 必中、天然 1 必失（5e2014）
            auto_critical=True,
            rng=rng,
        )
        if reason:
            result.kind += reason
        return result

    def check_spell_attack(
        self,
        target_ac: int,
        *,
        label: str = "法术攻击",
        advantage: Advantage = Advantage.NORMAL,
        rng: Random | None = None,
    ) -> CheckResult:
        """法术攻击检定（对抗目标 AC），加值取 :attr:`spell_attack_bonus`。"""
        final, reason = self._check_advantage("attack", advantage)
        result = roll_check(
            self.spell_attack_bonus,
            target_ac,
            kind=label,
            advantage=final,
            # 法术攻击也是攻击检定：天然 20 必中、天然 1 必失
            auto_critical=True,
            rng=rng,
        )
        if reason:
            result.kind += reason
        return result

    # —— 专注 ——

    @property
    def concentrating_spell(self) -> Spell | None:
        """正在维持的专注法术（认不出返回 ``None``）。"""
        if not self.concentrating:
            return None
        return get_spell(self.concentrating)

    def start_concentration(self, value: Spell | str) -> tuple[Spell | None, str]:
        """开始维持一个需专注的法术；返回 ``(法术, 说明)``。

        同时只能维持一个：原本在维持的法术被**新法术顶替**而中断（5e），
        说明文本里会写清被顶掉的是哪个。
        """
        spell = get_spell(value)
        if spell is None:
            return None, ""
        previous = self.concentrating_spell
        self.concentrating = spell.key
        if previous is not None and previous.key != spell.key:
            return spell, f"（原专注的「{previous.cn}」被顶替而中断）"
        return spell, ""

    def break_concentration(self, reason: str = "") -> str:
        """中断当前专注（受挫 / 失能 / 主动放弃）；返回说明文本。"""
        spell = self.concentrating_spell
        self.concentrating = ""
        if spell is None:
            return ""
        tail = f"（{reason}）" if reason else ""
        return f"专注中断：「{spell.cn}」效果终止{tail}"

    def concentration_save(
        self, damage: int, *, rng: Random | None = None
    ) -> tuple[bool, str]:
        """受伤后掷体质豁免维持专注：DC = max(10, 伤害 // 2)。

        :return: ``(是否维持住, 结算文本)``；没在专注时返回 ``(True, "")``。
        """
        spell = self.concentrating_spell
        if spell is None or int(damage) <= 0:
            return True, ""
        dc = max(10, int(damage) // 2)
        result = self.check_save("con", dc, rng=rng)
        if result.success:
            return True, f"{result.describe()}：维持「{spell.cn}」的专注"
        self.concentrating = ""
        return False, f"{result.describe()}：专注中断，「{spell.cn}」效果终止"

    # —— 伤害抗性 ——

    def apply_resistance(
        self, amount: int, damage_type: str = "", *, magical: bool = False
    ) -> tuple[int, str]:
        """按抗性表结算伤害（抗性减半 / 免疫归零 / 易伤翻倍）。

        抗性 = 角色卡上的固定抗性（种族等）+ 当前状态带来的抗性（狂暴的物理抗性）。

        :param magical: 这一击是不是魔法的（见 :func:`~.conditions.resolve_damage`）。
        """
        entries = list(self.damage_resistances)
        for item in merge_effects(self.conditions).resistances:
            if item not in entries:
                entries.append(item)
        return resolve_damage(amount, damage_type, entries, magical=magical)

    def resistances_text(self) -> str:
        """抗性表文本；没有抗性返回空串。"""
        return resistances_text(self.damage_resistances)

    # —— 状态 ——

    def conditions_text(self) -> str:
        """当前状态的规范名列表（含力竭等级，仅展示用）。"""
        names = normalize_conditions(self.conditions)
        if self.exhaustion:
            names = [*names, f"力竭 {self.exhaustion} 级"]
        return "、".join(names)

    def effect(self) -> ConditionEffect:
        """当前状态合并后的机械效果。"""
        return merge_effects(self.conditions, exhaustion=self.exhaustion)

    def speed(self) -> int:
        """当前速度（含状态修正：力竭减半 / 归零，束缚 / 擒抱归零；
        重甲力量不足 −10 尺）。"""
        race = self.race_obj
        base = race.speed if race else 30
        scale = speed_scale(self.conditions, exhaustion=self.exhaustion)
        return max(0, int(base * scale) - self.heavy_armor_speed_penalty())

    # —— 展示与序列化 ——

    def skill_lines(self) -> list[str]:
        """熟练技能列表文本。"""
        lines = []
        for key in self.skill_proficiencies:
            try:
                skill = get_skill(key)
            except KeyError:
                continue
            lines.append(f"{skill.label} {self.skill_bonus(skill):+d}")
        return lines

    def summary(self) -> str:
        """角色卡摘要，用于聊天展示。"""
        race = self.race_obj
        profession = self.profession_obj
        race_text = race.cn if race else "未知种族"
        if self.subrace_obj:
            race_text += f"·{self.subrace_obj.cn}"
        lines = [
            f"【{self.name or '无名者'}】{race_text} {profession.cn if profession else '无业'} Lv.{self.level}",
            format_ability_block(self.abilities),
            f"HP {self.hp}/{self.max_hp}  AC {self.armor_class}  先攻 {self.initiative:+d}  熟练 {self.proficiency:+d}",
        ]
        if self.temp_hp:
            lines.append(f"临时 HP {self.temp_hp}")
        if self.death_save_text():
            lines.append("生存状态 " + self.death_save_text())
        if self.is_caster:
            lines.append(
                f"施法 {self.casting_text}　法术攻击 {self.spell_attack_bonus:+d}　"
                f"法术豁免 DC {self.spell_save_dc}"
            )
            lines.append(f"法术位 {self.spell_slots_text()}")
            lines.extend(self.spell_display_lines())
        if self.conditions or self.exhaustion:
            status = list(self.conditions)
            if self.exhaustion:
                status.append(f"力竭 {self.exhaustion} 级")
            lines.append("状态 " + "、".join(status))
        resist = self.resistances_text()
        if resist:
            lines.append(f"伤害抗性 {resist}")
        if self.concentrating_spell is not None:
            lines.append(f"专注维持中：{self.concentrating_spell.cn}")
        attuned = self.attunement_text()
        if attuned:
            lines.append(attuned)
        if profession:
            dice_text = f"生命骰 {profession.hit_dice}"
            if self.hit_dice_used:
                dice_text += f"（已耗 {self.hit_dice_used}，剩 {self.hit_dice_left}）"
            lines.append(dice_text)
        bg = self.background_obj
        if bg:
            lines.append(f"背景 {bg.cn}")
        if self.skill_lines():
            lines.append("技能 " + "、".join(self.skill_lines()))
        prof = []
        if self.armor_proficiencies:
            prof.append("护甲 " + "、".join(self.armor_proficiencies))
        if self.weapon_proficiencies:
            prof.append("武器 " + "、".join(self.weapon_proficiencies))
        if prof:
            lines.append("熟练 " + "；".join(prof))
        if self.equipment:
            lines.append("装备 " + "、".join(self.equipment_display()))
        return "\n".join(lines)

    # —— HTML 角色卡（喂给 html_to_pic 渲染成图片）——

    @staticmethod
    def _esc(text: object) -> str:
        """转义 HTML 特殊字符（角色名 / 装备名可能是玩家自由输入）。"""
        return html.escape(str(text), quote=True)

    def _card_section(self, title: str, items: list[str], *, as_text: bool = False) -> str:
        """拼一块角色卡分区；``as_text`` 时分条用换行而非标签。"""
        if not items:
            return ""
        if as_text:
            body = '<div class="muted">' + "<br>".join(self._esc(x) for x in items) + "</div>"
        else:
            body = "".join(f'<span class="tag">{self._esc(x)}</span>' for x in items)
        return (
            f'<div class="section"><div class="title">{self._esc(title)}</div>{body}</div>'
        )

    def equipment_display(self) -> list[str]:
        """装备展示列表：武器 / 护甲附上数值（伤害骰、AC），其余原样。"""
        out: list[str] = []
        for raw in self.equipment:
            stack = parse_item_stack(str(raw))
            name = stack.name
            count_suffix = f"×{stack.count}" if stack.count > 1 else ""
            weapon = get_weapon(name)
            if weapon is not None:
                dmg = weapon.damage
                if weapon.is_versatile:
                    dmg += f"/{weapon.two_handed_damage}"
                out.append(f"{name} {dmg} {weapon.damage_type}{count_suffix}")
                continue
            armor = get_armor(name)
            if armor is not None:
                if armor.is_shield:
                    out.append(f"{name} +2 AC{count_suffix}")
                else:
                    out.append(f"{name} AC {armor.base_ac}{count_suffix}")
                continue
            out.append(f"{name}{count_suffix}")
        return out

    def spell_display_lines(self) -> list[str]:
        """已知法术按环阶分组的展示行：先环阶名（戏法 / 一环…），再逐条 `法术名：简介`。

        简介取数据卡的 ``desc``；没有简介的只写名字。
        """
        known = self.known_spells()
        if not known:
            return []
        by_level: dict[int, list[Spell]] = {}
        for s in known:
            by_level.setdefault(s.level, []).append(s)
        lines: list[str] = []
        for lv in sorted(by_level):
            lines.append(by_level[lv][0].level_text)
            for s in by_level[lv]:
                line = s.cn
                if s.desc:
                    line += "：" + s.desc
                lines.append(line)
        return lines

    def to_html(self) -> str:
        """渲染一张可直接喂给 ``html_to_pic`` 的角色卡图片 HTML。

        纯展示：只读取角色卡的派生数据，不碰存档、不调模型。
        返回完整 HTML 文档（内联样式），无外部资源依赖，方便截图。
        """
        race = self.race_obj
        race_text = race.cn if race else "未知种族"
        if self.subrace_obj:
            race_text += f"·{self.subrace_obj.cn}"
        profession = self.profession_obj
        prof_text = profession.cn if profession else "无业"
        bg = self.background_obj
        bg_text = bg.cn if bg else ""

        # 六大属性
        ability_cells = []
        for a in ABILITIES:
            score = self.abilities[a]
            mod = self.abilities.modifier(a)
            ability_cells.append(
                '<div class="ab">'
                f'<div class="cn">{self._esc(a.cn)}</div>'
                f'<div class="sc">{score}</div>'
                f'<div class="mo">{mod:+d}</div>'
                "</div>"
            )
        abilities_html = "".join(ability_cells)

        # 生命条（仅在扣过血或记录了当前血量时显示，满血新卡不画空条）
        hp_bar = ""
        if self.current_hp is not None or self.hp < self.max_hp:
            hp_pct = max(0, min(100, int(100 * self.hp / max(1, self.max_hp))))
            hp_bar = (
                '<div class="hpbar">'
                f'<div class="hpfill" style="width:{hp_pct}%"></div>'
                "</div>"
            )
        temp_hp_line = (
            f'<div class="k">临时 {self.temp_hp}</div>' if self.temp_hp else ""
        )

        # 各分区
        sections: list[str] = []

        skills = self.skill_lines()
        if skills:
            sections.append(self._card_section("技能", skills))

        if self.equipment:
            sections.append(self._card_section("装备", self.equipment_display()))

        status_tags: list[str] = []
        if self.conditions or self.exhaustion:
            status_tags.extend(normalize_conditions(self.conditions))
            if self.exhaustion:
                status_tags.append(f"力竭 {self.exhaustion} 级")
        death = self.death_save_text()
        if death:
            status_tags.append(death)
        if status_tags:
            sections.append(self._card_section("状态", status_tags))

        resist = self.resistances_text()
        if resist:
            sections.append(self._card_section("伤害抗性", resist.split("、")))

        if self.is_caster:
            spell_lines = [
                f"施法属性 {self.casting_text}",
                f"法术攻击 {self.spell_attack_bonus:+d}",
                f"豁免 DC {self.spell_save_dc}",
                f"法术位 {self.spell_slots_text() or '（无）'}",
            ]
            spell_lines.extend(self.spell_display_lines())
            sections.append(self._card_section("法术", spell_lines, as_text=True))

        if self.concentrating_spell is not None:
            sections.append(
                self._card_section(
                    "专注",
                    [f"维持中：{self.concentrating_spell.cn}"],
                    as_text=True,
                )
            )

        attune = self.attunement_text()
        if attune:
            sections.append(self._card_section("同调", [attune], as_text=True))

        if profession:
            hd = f"生命骰 {profession.hit_dice}"
            if self.hit_dice_used:
                hd += f"（已耗 {self.hit_dice_used}，剩 {self.hit_dice_left}）"
            sections.append(self._card_section("生命骰", [hd], as_text=True))

        sections_html = "".join(sections)

        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:540px;font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC","Heiti SC",sans-serif;background:#15102b;padding:14px}}
.card{{background:linear-gradient(160deg,#2a2348,#1d1836);border:1px solid #6b5bb5;border-radius:14px;overflow:hidden;color:#ece8ff;box-shadow:0 6px 20px rgba(0,0,0,.5)}}
.header{{background:linear-gradient(135deg,#7b5cc4,#4b3a8a);padding:16px 20px}}
.name{{font-size:23px;font-weight:700;color:#fff}}
.meta{{font-size:13px;color:#e3dcff;margin-top:5px;opacity:.92}}
.stats{{display:flex;gap:10px;padding:14px 20px;background:rgba(0,0,0,.18)}}
.stat{{flex:1;text-align:center;background:rgba(255,255,255,.06);border-radius:10px;padding:8px 4px}}
.stat .v{{font-size:19px;font-weight:700;color:#ffd86b}}
.stat .k{{font-size:11px;color:#c3bce8}}
.hpbar{{height:7px;background:#3a2f5e;border-radius:5px;overflow:hidden;margin-top:6px}}
.hpfill{{height:100%;background:linear-gradient(90deg,#ff6b6b,#ffd86b)}}
.abilities{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:14px 20px}}
.ab{{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:8px;text-align:center}}
.ab .cn{{font-size:12px;color:#c3bce8}}
.ab .sc{{font-size:18px;font-weight:700}}
.ab .mo{{font-size:12px;color:#ffd86b}}
.section{{padding:10px 20px;border-top:1px solid rgba(255,255,255,.06);font-size:13px;line-height:1.7}}
.section .title{{color:#ffd86b;font-weight:700;margin-bottom:5px}}
.tag{{display:inline-block;background:rgba(123,92,196,.25);border:1px solid rgba(123,92,196,.5);border-radius:6px;padding:2px 8px;margin:2px 5px 2px 0;font-size:12px}}
.muted{{color:#c3bce8;font-size:13px}}
</style></head><body><div class="card">
<div class="header"><div class="name">{self._esc(self.name or '无名者')}</div>
<div class="meta">{self._esc(race_text)} · {self._esc(prof_text)} · Lv.{self.level}{(' · ' + self._esc(bg_text)) if bg_text else ''}</div></div>
<div class="stats">
<div class="stat"><div class="v">{self.hp}/{self.max_hp}</div><div class="k">生命</div>{hp_bar}</div>
<div class="stat"><div class="v">{self.armor_class}</div><div class="k">护甲 AC</div></div>
<div class="stat"><div class="v">{self.initiative:+d}</div><div class="k">先攻</div></div>
<div class="stat"><div class="v">{self.proficiency:+d}</div><div class="k">熟练</div>{temp_hp_line}</div>
</div>
<div class="abilities">{abilities_html}</div>
{sections_html}
</div></body></html>"""

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["abilities"] = self.abilities.as_dict()
        data["max_hp"] = self.max_hp
        data["armor_class"] = self.armor_class
        data["proficiency"] = self.proficiency
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DndCharacter:
        """从字典恢复；兼容 ``to_dict`` 写出的派生字段（自动忽略）。"""
        known = {
            "user_id",
            "name",
            "system",
            "level",
            "notes",
            "tags",
            "created_at",
            "updated_at",
            "race",
            "subrace",
            "profession",
            "background",
            "base_scores",
            "score_method",
            "score_rolls",
            "skill_proficiencies",
            "armor_proficiencies",
            "weapon_proficiencies",
            "equipment",
            "equipment_package",
            "has_shield",
            "features",
            "exp",
            "current_hp",
            "temp_hp",
            "conditions",
            "exhaustion",
            "rations",
            "hunger_days",
            "attuned",
            "spells",
            "spell_slots_used",
            "death_saves_success",
            "death_saves_failures",
            "stable",
            "concentrating",
            "damage_resistances",
            "hit_dice_used",
            "swim_speed",
        }
        return cls(**{k: v for k, v in data.items() if k in known})


def ability_roll_pool(
    count: int = 6, rng: Random | None = None
) -> tuple[list[list[int]], list[int]]:
    """建卡第 1 步：摇出一组候选属性值（4d6 去最低）。"""
    from ..trpg.dice import roll_scores_4d6

    return roll_scores_4d6(count, rng=rng)
