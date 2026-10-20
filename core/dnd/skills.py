"""DND 5e 十八项技能与关联属性。

技能检定 = d20 + 关联属性调整值 + （若熟练）熟练加值。
这里只描述「技能 ↔ 属性」的对应关系，加值结算见 :class:`~.card.DndCharacter`。
"""

from __future__ import annotations

from dataclasses import dataclass

from .attributes import Ability, get_ability

__all__ = [
    "SKILLS",
    "SKILL_ALIASES",
    "SKILL_MAP",
    "Skill",
    "get_skill",
    "skills_of_ability",
]


@dataclass(frozen=True, slots=True)
class Skill:
    """一项技能。"""

    key: str  #: 内部键，如 ``"athletics"``
    cn: str  #: 中文名，如 ``"运动"``
    en: str  #: 英文名
    ability: str  #: 关联属性的内部键

    @property
    def ability_obj(self) -> Ability:
        return get_ability(self.ability)

    @property
    def label(self) -> str:
        """形如 ``力量（运动）`` 的完整名称。"""
        return f"{self.ability_obj.cn}（{self.cn}）"

    def __str__(self) -> str:
        return self.cn


SKILLS: tuple[Skill, ...] = (
    # 力量
    Skill("athletics", "运动", "Athletics", "str"),
    # 敏捷
    Skill("acrobatics", "特技", "Acrobatics", "dex"),
    Skill("sleight_of_hand", "巧手", "Sleight of Hand", "dex"),
    Skill("stealth", "隐匿", "Stealth", "dex"),
    # 智力
    Skill("arcana", "奥秘", "Arcana", "int"),
    Skill("history", "历史", "History", "int"),
    Skill("investigation", "调查", "Investigation", "int"),
    Skill("nature", "自然", "Nature", "int"),
    Skill("religion", "宗教", "Religion", "int"),
    # 感知
    Skill("animal_handling", "驯兽", "Animal Handling", "wis"),
    Skill("insight", "洞悉", "Insight", "wis"),
    Skill("medicine", "医药", "Medicine", "wis"),
    Skill("perception", "察觉", "Perception", "wis"),
    Skill("survival", "求生", "Survival", "wis"),
    # 魅力
    Skill("deception", "欺瞒", "Deception", "cha"),
    Skill("intimidation", "威吓", "Intimidation", "cha"),
    Skill("performance", "表演", "Performance", "cha"),
    Skill("persuasion", "说服", "Persuasion", "cha"),
)

SKILL_MAP: dict[str, Skill] = {s.key: s for s in SKILLS}

#: 常见别名 / 别译 -> 技能内部键。
#:
#: 技能译名各家不一（「生存 / 求生」、「体操 / 特技」、「游说 / 说服」是同一批技能的
#: 两种通行译法），模型与玩家随手写哪一种都该认得出来。这里只收**同义异写**，
#: 不收口语化的动作描述（「翻墙」该走运动，不该硬塞进某一条技能）。
SKILL_ALIASES: dict[str, str] = {
    # 力量
    "竞技": "athletics",
    "体能": "athletics",
    # 敏捷
    "体操": "acrobatics",
    "杂技": "acrobatics",
    "身法": "acrobatics",
    "手上功夫": "sleight_of_hand",
    "妙手": "sleight_of_hand",
    "扒手": "sleight_of_hand",
    "潜行": "stealth",
    "隐身": "stealth",
    # 智力
    "秘法": "arcana",
    "神秘": "arcana",
    "魔法知识": "arcana",
    "搜索": "investigation",
    "侦查": "investigation",
    # 感知
    "驯养": "animal_handling",
    "驭兽": "animal_handling",
    "动物": "animal_handling",
    "洞察": "insight",
    "观察": "insight",
    "识破": "insight",
    "读心": "insight",
    "医疗": "medicine",
    "医术": "medicine",
    "感知": "perception",
    "觉察": "perception",
    "侦察": "perception",
    "生存": "survival",
    "野外": "survival",
    # 魅力
    "欺诈": "deception",
    "撒谎": "deception",
    "哄骗": "deception",
    "恐吓": "intimidation",
    "威逼": "intimidation",
    "恫吓": "intimidation",
    "演出": "performance",
    "游说": "persuasion",
    "交涉": "persuasion",
    "话术": "persuasion",
}


def get_skill(value: Skill | str) -> Skill:
    """按技能对象 / 内部键 / 中文名 / 英文名 / 别名取出技能。

    支持 :data:`SKILL_ALIASES` 里的同义异写（如「生存」→ survival），
    这样模型无论写「生存」还是「求生」都找得到同一条技能。
    """
    if isinstance(value, Skill):
        return value
    key = str(value).strip()
    if key in SKILL_MAP:
        return SKILL_MAP[key]
    alias = SKILL_ALIASES.get(key)
    if alias is not None:
        return SKILL_MAP[alias]
    lowered = key.lower()
    for skill in SKILLS:
        if skill.cn == key or skill.en.lower() == lowered:
            return skill
    raise KeyError(f"未知技能：{value}")


def skills_of_ability(ability: Ability | str) -> tuple[Skill, ...]:
    """取某项属性关联的全部技能。"""
    key = get_ability(ability).key
    return tuple(s for s in SKILLS if s.ability == key)
