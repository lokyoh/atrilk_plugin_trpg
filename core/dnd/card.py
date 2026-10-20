"""DND 5e 角色卡。

继承 :class:`~..trpg.character.TrpgCharacter`，把属性、种族、职业、技能、装备
组装成一份可参与检定的角色数据对象，并负责：

- 属性加值的结算（基础值 → 种族 / 亚种加值）
- 派生数值（调整值、熟练加值、AC、生命值、先攻）
- 检定 / 豁免（统一走 ``trpg.check.roll_check``）
- 序列化（枚举、装备栈转成普通字典，便于落库与 JSON 传输）
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
)
from .equipment import (
    Weapon,
    compute_armor_class,
    get_armor,
    get_weapon,
    parse_item_stack,
)
from .profession import DndClass, get_class
from .race import Race, SubRace, get_race
from .skills import Skill, get_skill
from .spells import Spell, get_spell, max_slots, slot_text

__all__ = ["DEATH_CONDITIONS", "DeathSaveOutcome", "DndCharacter", "ability_roll_pool"]

#: 系统维护的倒地相关状态文本（``conditions`` 里这几项不是 DM 的自由文本）
DEATH_CONDITIONS: tuple[str, ...] = ("倒地", "稳定", "死亡")


def _join_reason(reason: str, extra: str) -> str:
    """把两条原因文本并进同一对括号里。"""
    if not reason:
        return f"（{extra}）"
    return reason[:-1] + f"；{extra}）"


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

    def __post_init__(self) -> None:
        super().__post_init__()
        self.system = self.system or "DND5e"
        self.skill_proficiencies = list(dict.fromkeys(self.skill_proficiencies))
        self.equipment = list(dict.fromkeys(self.equipment))
        self.conditions = list(dict.fromkeys(self.conditions))
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
            profession=get_class(profession).key,
            background=get_background(background).key if background else "",
            base_scores=dict(base_scores or {}),
            score_method=score_method,
            score_rolls=dict(score_rolls or {}),
            skill_proficiencies=list(skills or []),
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

    def skill_bonus(self, skill: Skill | str) -> int:
        """技能检定加值 = 属性调整值 +（熟练则加熟练加值）。"""
        skill_obj = get_skill(skill)
        bonus = self.abilities.modifier(skill_obj.ability)
        if skill_obj.key in self.skill_proficiencies:
            bonus += self.proficiency
        return bonus

    def save_bonus(self, ability: Ability | str) -> int:
        """豁免加值 = 属性调整值 +（职业豁免熟练则加熟练加值）。"""
        ability_obj = get_ability(ability)
        bonus = self.abilities.modifier(ability_obj)
        profession = self.profession_obj
        if profession and ability_obj.key in profession.saves:
            bonus += self.proficiency
        return bonus

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
                unarmored = 10 + self.modifier("con")
            elif profession.key == "monk" and not self._wears_armor:
                unarmored = 10 + self.modifier("wis")
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
        """攻击检定加值：灵巧武器取力量 / 敏捷较高者，远程武器取敏捷。"""
        weapon_obj = get_weapon(weapon)
        if weapon_obj is None:
            return self.proficiency
        if weapon_obj.is_ranged:
            ability_mod = self.modifier("dex")
        elif weapon_obj.is_finesse:
            ability_mod = max(self.modifier("str"), self.modifier("dex"))
        else:
            ability_mod = self.modifier("str")
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

    def restore_slots(self, *, kind: str = "long") -> None:
        """恢复法术位：长休全部恢复；短休只恢复契约魔法（邪术师）。"""
        if kind == "long":
            self.spell_slots_used = {}
            return
        profession = self.profession_obj
        if profession and profession.caster_kind == "pact":
            self.spell_slots_used = {}

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
            return explicit, ""
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
        if not names:
            return combined, ""
        reason = f"（{'、'.join(names)}：{kind_cn}{stance}）"
        return combined, reason

    def resolve_attack_advantage(self, explicit: Advantage) -> tuple[Advantage, str]:
        """攻击检定的最终优势姿态：把自身状态（中毒 / 力竭 / 恐慌……）与
        调用方显式指定的姿态合并（相遇抵消），并给出原因文本。

        不走武器的攻击（徒手、推撞、擒抱）用这个方法拿最终姿态。
        """
        return self._check_advantage("attack", explicit)

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
        """攻击检定（对抗目标 AC；自动应用攻击方状态）。"""
        weapon_obj = get_weapon(weapon)
        label = weapon_obj.cn if weapon_obj else str(weapon)
        final, reason = self._check_advantage("attack", advantage)
        result = roll_check(
            self.attack_bonus(weapon_obj or weapon),
            target_ac,
            kind=f"{label}攻击",
            advantage=final,
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

    def apply_resistance(self, amount: int, damage_type: str = "") -> tuple[int, str]:
        """按抗性表结算伤害（抗性减半 / 免疫归零 / 易伤翻倍）。

        抗性 = 角色卡上的固定抗性（种族等）+ 当前状态带来的抗性（狂暴的物理抗性）。
        """
        entries = list(self.damage_resistances)
        for item in merge_effects(self.conditions).resistances:
            if item not in entries:
                entries.append(item)
        return resolve_damage(amount, damage_type, entries)

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
        """当前速度（含状态修正：力竭减半 / 归零，束缚 / 擒抱归零）。"""
        race = self.race_obj
        base = race.speed if race else 30
        scale = speed_scale(self.conditions, exhaustion=self.exhaustion)
        return max(0, int(base * scale))

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
            known = self.known_spells()
            if known:
                lines.append("法术 " + "、".join(s.cn for s in known))
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
        if self.equipment:
            lines.append("装备 " + "、".join(self.equipment))
        return "\n".join(lines)

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
            "equipment",
            "equipment_package",
            "has_shield",
            "features",
            "exp",
            "current_hp",
            "temp_hp",
            "conditions",
            "exhaustion",
            "spells",
            "spell_slots_used",
            "death_saves_success",
            "death_saves_failures",
            "stable",
            "concentrating",
            "damage_resistances",
            "hit_dice_used",
        }
        return cls(**{k: v for k, v in data.items() if k in known})


def ability_roll_pool(
    count: int = 6, rng: Random | None = None
) -> tuple[list[list[int]], list[int]]:
    """建卡第 1 步：摇出一组候选属性值（4d6 去最低）。"""
    from ..trpg.dice import roll_scores_4d6

    return roll_scores_4d6(count, rng=rng)
