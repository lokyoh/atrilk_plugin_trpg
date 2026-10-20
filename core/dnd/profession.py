"""DND 5e 职业数据。

对应建卡流程中的 ``profession.md``。每个职业提供：

- 生命骰（决定生命值）与 1 级生命值
- 两项豁免熟练
- 可选技能池与可选数量（对应 ``skill_choice.md``）
- 起始装备包（对应 ``equioment.md``）
- 1 级职业特性
"""

from __future__ import annotations

from dataclasses import dataclass

from .attributes import get_ability
from .skills import SKILL_MAP

__all__ = [
    "CLASSES",
    "CLASS_MAP",
    "CLASS_SKILL_POOLS",
    "DndClass",
    "class_choices",
    "get_class",
    "profession_skill_choices",
]


@dataclass(frozen=True, slots=True)
class EquipmentChoice:
    """起始装备二选一（如「长剑 + 盾牌」或「两把短剑」）。"""

    key: str
    cn: str
    items: tuple[str, ...]

    @property
    def label(self) -> str:
        return "、".join(self.items) if self.items else self.cn


@dataclass(frozen=True, slots=True)
class DndClass:
    """一个职业。"""

    key: str
    cn: str
    en: str
    hit_die: int  # 生命骰面数
    primary: tuple[str, ...]  # 主要属性内部键
    saves: tuple[str, ...]  # 豁免熟练的属性内部键
    skill_pool: tuple[str, ...]  # 可选技能内部键
    skill_count: int  # 可选技能数量
    equipment_packages: tuple[EquipmentChoice, ...]
    fixed_equipment: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    desc: str = ""
    casting: str = ""  # 施法属性内部键（"" = 非施法者）
    caster_kind: str = ""  # full / half / pact（"" = 非施法者）
    cantrips: int = 0  # 建卡时可选戏法数
    spells_known: int = 0  # 建卡时可选法术数（法师 = 法术书收录数）

    @property
    def level1_hp(self) -> int:
        """1 级基础生命值（生命骰最大值 + 体质调整值，体质在结算时叠加）。"""
        return self.hit_die

    @property
    def is_caster(self) -> bool:
        """是否施法职业。"""
        return bool(self.casting and self.caster_kind)

    @property
    def casting_text(self) -> str:
        """施法属性的中文名（"" = 非施法者）。"""
        return get_ability(self.casting).cn if self.casting else ""

    @property
    def caster_text(self) -> str:
        """施法类型的中文名（"" = 非施法者）。"""
        return {"full": "全职施法", "half": "半施法", "pact": "契约魔法"}.get(
            self.caster_kind, ""
        )

    @property
    def hit_dice(self) -> str:
        return f"1d{self.hit_die}"

    def skill_names(self) -> tuple[str, ...]:
        """可选技能的中文名列表。"""
        return tuple(SKILL_MAP[k].cn for k in self.skill_pool)

    def format_equipment(self) -> str:
        """装备包的展示文本。"""
        lines: list[str] = []
        for i, pkg in enumerate(self.equipment_packages, 1):
            lines.append(f"{i}. {pkg.label}")
        if self.fixed_equipment:
            lines.append(f"固定：{'、'.join(self.fixed_equipment)}")
        return "\n".join(lines)


CLASSES: tuple[DndClass, ...] = (
    DndClass(
        key="barbarian",
        cn="野蛮人",
        en="Barbarian",
        hit_die=12,
        primary=("str",),
        saves=("str", "con"),
        skill_pool=(
            "animal_handling",
            "athletics",
            "intimidation",
            "nature",
            "perception",
            "survival",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a",
                "战斧 / 任意军用近战武器",
                ("战斧", "手斧×2", "探索者背包", "标枪×4"),
            ),
            EquipmentChoice(
                "b",
                "任意军用近战武器",
                ("任意军用近战武器", "手斧×2", "探索者背包", "标枪×4"),
            ),
        ),
        fixed_equipment=("探索者背包", "标枪×4"),
        features=(
            "狂暴：以附赠动作进入狂暴，近战伤害加值并获得伤害抗性",
            "无甲防御：未穿护甲时 AC = 10 + 敏捷调整值 + 体质调整值",
        ),
        desc="以原始怒火作战的战士，血越流越勇。",
    ),
    DndClass(
        key="bard",
        cn="吟游诗人",
        en="Bard",
        hit_die=8,
        primary=("cha",),
        saves=("dex", "cha"),
        skill_pool=tuple(SKILL_MAP.keys()),
        skill_count=3,
        equipment_packages=(
            EquipmentChoice("a", "细剑 + 艺人包", ("细剑", "艺人包", "皮甲", "匕首")),
            EquipmentChoice("b", "长剑 + 学者包", ("长剑", "学者包", "皮甲", "匕首")),
        ),
        features=(
            "施法：以魅力施展诗人法术",
            "诗人激励：以附赠动作给予同伴 1d6 激励骰",
        ),
        casting="cha",
        caster_kind="full",
        cantrips=2,
        spells_known=4,
        desc="用言语与音乐施展魔法，样样通而不精。",
    ),
    DndClass(
        key="cleric",
        cn="牧师",
        en="Cleric",
        hit_die=8,
        primary=("wis",),
        saves=("wis", "cha"),
        skill_pool=("history", "insight", "medicine", "persuasion", "religion"),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a",
                "硬头锤 + 鳞甲 + 盾牌",
                ("硬头锤", "鳞甲", "盾牌", "圣徽", "祭司包"),
            ),
            EquipmentChoice("b", "战锤 + 链甲", ("战锤", "链甲", "圣徽", "祭司包")),
        ),
        features=("施法：以感知施展神术", "神域：选择一项神域，获得其领域法术与特性"),
        casting="wis",
        caster_kind="full",
        cantrips=3,
        spells_known=4,
        desc="神祇在人间的代言者，既能治疗也能惩戒。",
    ),
    DndClass(
        key="druid",
        cn="德鲁伊",
        en="Druid",
        hit_die=8,
        primary=("wis",),
        saves=("int", "wis"),
        skill_pool=(
            "arcana",
            "animal_handling",
            "insight",
            "medicine",
            "nature",
            "perception",
            "religion",
            "survival",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a",
                "木盾 + 弯刀",
                ("木盾", "弯刀", "皮甲", "探索者背包", "德鲁伊法器和材料包"),
            ),
            EquipmentChoice(
                "b",
                "任意简易武器 + 盾牌",
                ("任意简易武器", "盾牌", "皮甲", "探索者背包"),
            ),
        ),
        features=("施法：以感知施展德鲁伊法术", "德鲁伊语：习得德鲁伊的秘密语言"),
        casting="wis",
        caster_kind="full",
        cantrips=2,
        spells_known=4,
        desc="自然的守护者，能化身为兽、呼唤风雨。",
    ),
    DndClass(
        key="fighter",
        cn="战士",
        en="Fighter",
        hit_die=10,
        primary=("str", "dex"),
        saves=("str", "con"),
        skill_pool=(
            "acrobatics",
            "animal_handling",
            "athletics",
            "history",
            "insight",
            "intimidation",
            "perception",
            "survival",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a",
                "链甲 + 军用武器 + 盾牌",
                ("链甲", "任意军用武器", "盾牌", "探索者背包"),
            ),
            EquipmentChoice(
                "b", "皮甲 + 长弓 + 箭袋", ("皮甲", "长弓", "箭×20", "探索者背包")
            ),
        ),
        features=(
            "战斗风格：选择一项战斗风格（如防御、决斗、巨武）",
            "回气：以附赠动作恢复 1d10 + 等级 的生命值",
        ),
        desc="最纯粹的武者，用武器与纪律取胜。",
    ),
    DndClass(
        key="monk",
        cn="武僧",
        en="Monk",
        hit_die=8,
        primary=("dex", "wis"),
        saves=("str", "dex"),
        skill_pool=(
            "acrobatics",
            "athletics",
            "history",
            "insight",
            "religion",
            "stealth",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a", "短剑 + 探索者背包", ("短剑", "探索者背包", "飞镖×10")
            ),
            EquipmentChoice(
                "b", "任意简易武器 + 探索者背包", ("任意简易武器", "探索者背包")
            ),
        ),
        features=(
            "无甲防御：AC = 10 + 敏捷调整值 + 感知调整值",
            "武术：徒手打击伤害 d4，可用附赠动作再攻击一次",
        ),
        desc="以身体为武器，追求内在的气与平衡。",
    ),
    DndClass(
        key="paladin",
        cn="圣武士",
        en="Paladin",
        hit_die=10,
        primary=("str", "cha"),
        saves=("wis", "cha"),
        skill_pool=(
            "athletics",
            "insight",
            "intimidation",
            "medicine",
            "persuasion",
            "religion",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a",
                "军用武器 + 盾牌",
                ("任意军用武器", "盾牌", "链甲", "圣徽", "祭司包"),
            ),
            EquipmentChoice(
                "b", "两把军用武器", ("两把军用武器", "链甲", "圣徽", "祭司包")
            ),
        ),
        features=(
            "神圣感知：以动作感知附近的天界 / 邪魔 / 不死生物",
            "圣疗：以动作治疗 5 × 等级 的生命值",
        ),
        casting="cha",
        caster_kind="half",
        desc="誓言束缚的圣战士，兼具武勇与神恩。",
    ),
    DndClass(
        key="ranger",
        cn="游侠",
        en="Ranger",
        hit_die=10,
        primary=("dex", "wis"),
        saves=("str", "dex"),
        skill_pool=(
            "animal_handling",
            "athletics",
            "insight",
            "investigation",
            "nature",
            "perception",
            "stealth",
            "survival",
        ),
        skill_count=3,
        equipment_packages=(
            EquipmentChoice("a", "鳞甲 + 双短剑", ("鳞甲", "短剑×2", "探索者背包")),
            EquipmentChoice(
                "b", "皮甲 + 长弓", ("皮甲", "长弓", "箭×20", "探索者背包")
            ),
        ),
        features=(
            "宿敌：选择一类宿敌，追踪与相关检定有优势",
            "自然探索者：选择一种地形，在其中行动迅速且不易迷路",
        ),
        casting="wis",
        caster_kind="half",
        desc="边境的猎手，既通武艺也懂自然之法。",
    ),
    DndClass(
        key="rogue",
        cn="游荡者",
        en="Rogue",
        hit_die=8,
        primary=("dex",),
        saves=("dex", "int"),
        skill_pool=(
            "acrobatics",
            "athletics",
            "deception",
            "insight",
            "intimidation",
            "investigation",
            "perception",
            "performance",
            "persuasion",
            "sleight_of_hand",
            "stealth",
        ),
        skill_count=4,
        equipment_packages=(
            EquipmentChoice(
                "a", "细剑 + 短弓", ("细剑", "短弓", "箭×20", "盗贼工具", "窃贼包")
            ),
            EquipmentChoice("b", "短剑 + 短剑", ("短剑×2", "盗贼工具", "窃贼包")),
        ),
        fixed_equipment=("皮甲", "匕首×2", "盗贼工具"),
        features=(
            "专精：两项熟练的技能获得双倍熟练加值",
            "偷袭：对处于劣势的敌人额外造成 1d6 伤害",
            "盗贼隐语：懂得地下世界的暗语",
        ),
        desc="靠技巧与时机取胜，从不正面硬拼。",
    ),
    DndClass(
        key="sorcerer",
        cn="术士",
        en="Sorcerer",
        hit_die=6,
        primary=("cha",),
        saves=("con", "cha"),
        skill_pool=(
            "arcana",
            "deception",
            "insight",
            "intimidation",
            "persuasion",
            "religion",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a", "轻弩 + 材料包", ("轻弩", "箭×20", "材料包", "匕首×2")
            ),
            EquipmentChoice(
                "b", "任意简易武器 + 材料包", ("任意简易武器", "材料包", "匕首×2")
            ),
        ),
        features=(
            "施法：以魅力施展天生的法术",
            "术法起源：选择血脉来源（如龙族血脉、野魔法）",
            "元气：1 级获得 2 点术法点",
        ),
        casting="cha",
        caster_kind="full",
        cantrips=4,
        spells_known=2,
        desc="魔法流淌在血液里，无需研习便能施展。",
    ),
    DndClass(
        key="warlock",
        cn="邪术师",
        en="Warlock",
        hit_die=8,
        primary=("cha",),
        saves=("wis", "cha"),
        skill_pool=(
            "arcana",
            "deception",
            "history",
            "intimidation",
            "investigation",
            "nature",
            "religion",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice(
                "a", "轻弩 + 学者包", ("轻弩", "箭×20", "学者包", "皮甲", "匕首×2")
            ),
            EquipmentChoice(
                "b",
                "任意简易武器 + 学者包",
                ("任意简易武器", "学者包", "皮甲", "匕首×2"),
            ),
        ),
        features=(
            "契约魔法：以魅力施展宗主赐予的法术",
            "异界宗主：选择一位宗主（如大魔鬼、古老者）",
            "契约祷言：获得宗主赐予的特殊能力",
        ),
        casting="cha",
        caster_kind="pact",
        cantrips=2,
        spells_known=2,
        desc="以契约为代价换取力量，代价往往尚未付清。",
    ),
    DndClass(
        key="wizard",
        cn="法师",
        en="Wizard",
        hit_die=6,
        primary=("int",),
        saves=("int", "wis"),
        skill_pool=(
            "arcana",
            "history",
            "insight",
            "investigation",
            "medicine",
            "religion",
        ),
        skill_count=2,
        equipment_packages=(
            EquipmentChoice("a", "法杖 + 学者包", ("法杖", "学者包", "法术书")),
            EquipmentChoice("b", "匕首 + 材料包", ("匕首", "材料包", "法术书")),
        ),
        features=(
            "施法：以智力施展研习的法术，法术书记录已知法术",
            "秘法恢复：短休时恢复部分法术位",
        ),
        casting="int",
        caster_kind="full",
        cantrips=3,
        spells_known=6,
        desc="以理性解剖魔法，把世界写成公式。",
    ),
)

CLASS_MAP: dict[str, DndClass] = {c.key: c for c in CLASSES}


def get_class(value: DndClass | str) -> DndClass:
    """按职业对象 / 内部键 / 中文名取出职业。"""
    if isinstance(value, DndClass):
        return value
    text = str(value).strip()
    if text in CLASS_MAP:
        return CLASS_MAP[text]
    for cls in CLASSES:
        if cls.cn == text or cls.en.lower() == text.lower():
            return cls
    raise KeyError(f"未知职业：{value}")


def class_choices() -> tuple[str, ...]:
    """全部职业中文名。"""
    return tuple(c.cn for c in CLASSES)


def profession_skill_choices(value: DndClass | str) -> tuple[tuple[str, ...], int]:
    """取某职业的可选技能（中文名）与可选数量，供 ``skill_choice`` 流程使用。"""
    cls = get_class(value)
    return cls.skill_names(), cls.skill_count


#: 兼容命名：职业 -> 可选技能池
CLASS_SKILL_POOLS: dict[str, tuple[str, ...]] = {c.key: c.skill_pool for c in CLASSES}
