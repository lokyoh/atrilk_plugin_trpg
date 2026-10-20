"""DND 5e 状态条件（conditions）与伤害抗性结算。

把规则里只有文字的部分落成可结算的数据：

- **状态注册表**：14 种状态 + 野蛮人「狂暴」+ 「擒抱」，每项带机械效果
  （攻击 / 被攻击的优势劣势、自动失败的豁免、能否行动、速度、伤害抗性……）；
- **力竭**：按 1~6 级查表（检定劣势 → 速度减半 → 攻击与豁免劣势 →
  生命上限减半 → 速度归零 → 死亡），等级记在角色卡 / 参战者身上；
- **抗性结算**：抗性减半、免疫归零、易伤翻倍，按伤害类型查表。

状态只是**系统侧的记账与提示**：DM 用 ``update_character`` 的 conditions 字段
（或 ``update_enemy``）维护，攻击 / 豁免 / 施法 / 移动的结算函数自动查表，
把「中毒了攻击带劣势」「麻痹了力量豁免自动失败」这类规则变成默认行为，
DM 仍然可以用显式的 advantage 参数覆盖（剧情上说得通时）。

自由文本进、规范名出：:func:`normalize_conditions` 认常见的同义写法
（「俯伏」→「倒地」、「惊吓」→「恐慌」），认不出的原样保留成自定义备注，
不会因为模型换了个说法就丢掉状态。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from ..trpg.dice import Advantage

__all__ = [
    "CONDITIONS",
    "CONDITION_ALIASES",
    "CONDITION_MAP",
    "DAMAGE_LEVELS",
    "DAMAGE_TYPES",
    "Condition",
    "ConditionEffect",
    "attack_advantage",
    "combine_advantage",
    "condition_text",
    "exhaustion_effect",
    "is_incapacitated",
    "merge_effects",
    "normalize_conditions",
    "resistances_text",
    "resolve_damage",
    "save_auto_fail",
    "speed_scale",
]

#: 伤害类型（中文，与武器 / 法术表一致）
DAMAGE_TYPES: tuple[str, ...] = (
    "强酸",
    "钝击",
    "寒冷",
    "火焰",
    "力场",
    "闪电",
    "穿刺",
    "光耀",
    "挥砍",
    "雷鸣",
    "毒素",
    "暗蚀",
    "心灵",
)

#: 物理伤害类型（「非魔法武器抗性」只对这几类生效）
PHYSICAL_TYPES: tuple[str, ...] = ("钝击", "穿刺", "挥砍")

#: 抗性等级：抗性减半、免疫归零、易伤翻倍；``抗性(非魔法)`` 只挡非魔法攻击
DAMAGE_LEVELS: tuple[str, ...] = ("免疫", "抗性", "抗性(非魔法)", "易伤")


@dataclass(frozen=True, slots=True)
class ConditionEffect:
    """一组状态合并后的机械效果。

    优势 / 劣势字段遵循 5e「不叠加、相遇抵消」：多处来源同时给优势与劣势时
    互相抵消成普通（见 :meth:`merge`）。
    """

    #: 自身发动攻击时的优势姿态
    own_attack: Advantage = Advantage.NORMAL
    #: 被攻击时的优势姿态（对方视角）
    vs_attack: Advantage = Advantage.NORMAL
    #: 自身属性 / 技能检定
    own_check: Advantage = Advantage.NORMAL
    #: 自身豁免
    own_save: Advantage = Advantage.NORMAL
    #: 自动失败的豁免属性（力量 / 敏捷……）
    auto_fail_saves: tuple[str, ...] = ()
    #: 这些属性的检定获得优势（野蛮人狂暴的力量检定）
    check_adv_abilities: tuple[str, ...] = ()
    #: 这些属性的豁免获得优势（野蛮人狂暴的力量豁免）
    save_adv_abilities: tuple[str, ...] = ()
    #: 无法行动也无法反应（震慑 / 麻痹 / 昏迷 / 石化 / 失能）
    incapacitated: bool = False
    #: 速度归零（束缚 / 擒抱 / 力竭 5 级）
    speed_zero: bool = False
    #: 速度倍率（力竭 2 级减半）
    speed_scale: float = 1.0
    #: 生命上限倍率（力竭 4 级减半）
    hp_max_scale: float = 1.0
    #: 减半的伤害类型（狂暴的物理抗性、石化的全抗性等）
    resistances: tuple[str, ...] = ()
    #: 中断当前专注（进入失能时）
    breaks_concentration: bool = False
    #: 软效果说明（给 DM 看的文字，不参与数值结算）
    notes: tuple[str, ...] = ()

    def merge(self, other: ConditionEffect) -> ConditionEffect:
        """合并两组效果：优势劣势相遇抵消、自动失败取并集、抗性取并集。"""
        return ConditionEffect(
            own_attack=_combine(self.own_attack, other.own_attack),
            vs_attack=_combine(self.vs_attack, other.vs_attack),
            own_check=_combine(self.own_check, other.own_check),
            own_save=_combine(self.own_save, other.own_save),
            auto_fail_saves=tuple(
                dict.fromkeys(self.auto_fail_saves + other.auto_fail_saves)
            ),
            check_adv_abilities=tuple(
                dict.fromkeys(
                    self.check_adv_abilities + other.check_adv_abilities
                )
            ),
            save_adv_abilities=tuple(
                dict.fromkeys(self.save_adv_abilities + other.save_adv_abilities)
            ),
            incapacitated=self.incapacitated or other.incapacitated,
            speed_zero=self.speed_zero or other.speed_zero,
            speed_scale=min(self.speed_scale, other.speed_scale),
            hp_max_scale=min(self.hp_max_scale, other.hp_max_scale),
            resistances=tuple(dict.fromkeys(self.resistances + other.resistances)),
            breaks_concentration=self.breaks_concentration or other.breaks_concentration,
            notes=tuple(dict.fromkeys(self.notes + other.notes)),
        )


def _combine(left: Advantage, right: Advantage) -> Advantage:
    """优势劣势相遇抵消；同向不叠加。"""
    if left == right:
        return left
    if left is Advantage.NORMAL:
        return right
    if right is Advantage.NORMAL:
        return left
    return Advantage.NORMAL


#: 公共别名：把两处优势 / 劣势来源按 5e 规则合并（不叠加、相遇抵消）
combine_advantage = _combine


@dataclass(frozen=True, slots=True)
class Condition:
    """一种状态。"""

    key: str  #: 内部键，如 ``"prone"``
    cn: str  #: 中文名，如 ``"倒地"``
    en: str  #: 英文名
    effect: ConditionEffect  #: 机械效果
    desc: str = ""  #: 一句话说明（给查询 / 展示用）

    @property
    def label(self) -> str:
        return self.cn


#: 状态注册表（5e 14 种 + 野蛮人狂暴 + 擒抱；力竭按等级单独结算）
CONDITIONS: tuple[Condition, ...] = (
    Condition(
        "prone",
        "倒地",
        "Prone",
        ConditionEffect(
            own_attack=Advantage.DISADVANTAGE,
            notes=("近战攻击带劣势；起身要花一半移动力",),
        ),
        "趴在地上：自己的攻击有劣势，5 尺内近战打它有优势、远程打它有劣势。",
    ),
    Condition(
        "blinded",
        "目盲",
        "Blinded",
        ConditionEffect(
            own_attack=Advantage.DISADVANTAGE,
            vs_attack=Advantage.ADVANTAGE,
        ),
        "看不见：自己的攻击检定有劣势，被攻击时有优势。",
    ),
    Condition(
        "deafened",
        "耳聋",
        "Deafened",
        ConditionEffect(notes=("听不见：依赖听觉的检定自动失败",)),
        "听不见：无法进行依赖听觉的检定。",
    ),
    Condition(
        "charmed",
        "魅惑",
        "Charmed",
        ConditionEffect(notes=("不能攻击魅惑者；魅惑者对它社交检定有优势",)),
        "被魅惑：无法攻击魅惑者，魅惑者对它的社交检定有优势。",
    ),
    Condition(
        "frightened",
        "恐慌",
        "Frightened",
        ConditionEffect(
            own_attack=Advantage.DISADVANTAGE,
            own_check=Advantage.DISADVANTAGE,
            notes=("恐惧源在视线内时生效；不能主动靠近恐惧源",),
        ),
        "恐慌：恐惧源在视线内时攻击与检定有劣势，不能主动靠近它。",
    ),
    Condition(
        "incapacitated",
        "失能",
        "Incapacitated",
        ConditionEffect(
            incapacitated=True,
            breaks_concentration=True,
            notes=("无法行动也无法反应",),
        ),
        "失能：无法进行动作与反应（会中断专注）。",
    ),
    Condition(
        "invisible",
        "隐形",
        "Invisible",
        ConditionEffect(
            own_attack=Advantage.ADVANTAGE,
            vs_attack=Advantage.DISADVANTAGE,
            notes=("未被发现时生效；出手后会暴露位置",),
        ),
        "隐形：攻击它有劣势，它的攻击有优势；出手后暴露位置。",
    ),
    Condition(
        "paralyzed",
        "麻痹",
        "Paralyzed",
        ConditionEffect(
            own_attack=Advantage.DISADVANTAGE,
            vs_attack=Advantage.ADVANTAGE,
            auto_fail_saves=("str", "dex"),
            incapacitated=True,
            breaks_concentration=True,
            notes=("5 尺内命中它的攻击自动暴击",),
        ),
        "麻痹：无法行动，力量与敏捷豁免自动失败，5 尺内命中它的攻击自动暴击。",
    ),
    Condition(
        "petrified",
        "石化",
        "Petrified",
        ConditionEffect(
            vs_attack=Advantage.ADVANTAGE,
            auto_fail_saves=("str", "dex"),
            incapacitated=True,
            resistances=DAMAGE_TYPES,
            breaks_concentration=True,
            notes=("对所有伤害有抗性",),
        ),
        "石化：变成石头，无法行动，力量与敏捷豁免自动失败，对所有伤害有抗性。",
    ),
    Condition(
        "poisoned",
        "中毒",
        "Poisoned",
        ConditionEffect(
            own_attack=Advantage.DISADVANTAGE,
            own_check=Advantage.DISADVANTAGE,
        ),
        "中毒：攻击检定与属性检定有劣势。",
    ),
    Condition(
        "restrained",
        "束缚",
        "Restrained",
        ConditionEffect(
            own_attack=Advantage.DISADVANTAGE,
            vs_attack=Advantage.ADVANTAGE,
            own_save=Advantage.DISADVANTAGE,
            speed_zero=True,
        ),
        "束缚：速度归零，自己的攻击与敏捷豁免有劣势，被攻击有优势。",
    ),
    Condition(
        "stunned",
        "震慑",
        "Stunned",
        ConditionEffect(
            vs_attack=Advantage.ADVANTAGE,
            auto_fail_saves=("str", "dex"),
            incapacitated=True,
            breaks_concentration=True,
        ),
        "震慑：无法行动，力量与敏捷豁免自动失败，被攻击有优势。",
    ),
    Condition(
        "unconscious",
        "昏迷",
        "Unconscious",
        ConditionEffect(
            own_attack=Advantage.DISADVANTAGE,
            vs_attack=Advantage.ADVANTAGE,
            auto_fail_saves=("str", "dex"),
            incapacitated=True,
            breaks_concentration=True,
            notes=("倒地、松手掉物品、不知道周围发生的事",),
        ),
        "昏迷：失去意识，无法行动，力量与敏捷豁免自动失败，被攻击有优势。",
    ),
    Condition(
        "grappled",
        "擒抱",
        "Grappled",
        ConditionEffect(speed_zero=True, notes=("挣脱需要运动（特技）检定胜过擒抱者",)),
        "被擒抱：速度归零，挣脱要靠竞技 / 特技检定。",
    ),
    Condition(
        "raging",
        "狂暴",
        "Raging",
        ConditionEffect(
            check_adv_abilities=("str",),
            save_adv_abilities=("str",),
            resistances=PHYSICAL_TYPES,
            notes=("野蛮人：近战伤害有加值；不穿重甲才能进 / 维持",),
        ),
        "野蛮人狂暴：力量检定与豁免有优势，对钝击 / 穿刺 / 挥砍有抗性。",
    ),
)

CONDITION_MAP: dict[str, Condition] = {c.key: c for c in CONDITIONS}
_CN_TO_KEY: dict[str, str] = {c.cn: c.key for c in CONDITIONS}

#: 常见同义写法 -> 规范状态名。只收同义异写，不收动作描述。
CONDITION_ALIASES: dict[str, str] = {
    "俯伏": "倒地",
    "趴下": "倒地",
    "倒地不起": "倒地",
    "倒地状态": "倒地",
    "盲眼": "目盲",
    "失明": "目盲",
    "目盲状态": "目盲",
    "聋": "耳聋",
    "耳聋状态": "耳聋",
    "魅惑状态": "魅惑",
    "被魅惑": "魅惑",
    "惊吓": "恐慌",
    "恐惧": "恐慌",
    "恐慌状态": "恐慌",
    "恐惧状态": "恐慌",
    "无能": "失能",
    "无力化": "失能",
    "隐身": "隐形",
    "隐形状态": "隐形",
    "麻痹状态": "麻痹",
    "僵直": "麻痹",
    "石化状态": "石化",
    "中毒状态": "中毒",
    "毒素": "中毒",
    "束缚状态": "束缚",
    "被困": "束缚",
    "震慑状态": "震慑",
    "昏迷状态": "昏迷",
    "无意识": "昏迷",
    "昏睡": "昏迷",
    "被擒": "擒抱",
    "被擒抱": "擒抱",
    "擒抱状态": "擒抱",
    "狂暴状态": "狂暴",
    "愤怒": "狂暴",
}


def normalize_conditions(raw: Iterable[str] | str | None) -> list[str]:
    """把自由文本状态列表规范成已知状态名。

    - 拆开「中毒、倒地」这类写法（调用方传字符串时也认）；
    - 同义写法归一（「俯伏」→「倒地」、「恐惧」→「恐慌」）；
    - 认不出的原样保留（自定义备注，如「旧伤发作」），不丢信息；
    - 去重保序。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [part for part in _split_text(raw)]
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip().strip("。.!！?？;；,，、 ")
        if not text:
            continue
        # 去掉常见的尾缀与状态包装词：「中毒了」「处于震慑状态」
        text = text.removeprefix("处于").removeprefix("进入").removesuffix("状态")
        text = text.removesuffix("了").strip()
        text = CONDITION_ALIASES.get(text, text)
        if text in _CN_TO_KEY or text in CONDITION_ALIASES.values():
            if text not in out:
                out.append(text)
        elif text not in out:
            out.append(text)
    return out


def _split_text(text: str) -> list[str]:
    """把「中毒、倒地/束缚」这类写法拆成列表。"""
    import re

    return [part for part in re.split(r"[、,，;；/|\n]+", text) if part.strip()]


def merge_effects(
    conditions: Iterable[str] | None, *, exhaustion: int = 0
) -> ConditionEffect:
    """合并一组状态的机械效果，并叠加力竭等级效果。

    :param exhaustion: 力竭等级 0~6（见角色卡 / 参战者上的记录）。
    """
    effect = ConditionEffect()
    for name in normalize_conditions(conditions):
        condition = CONDITION_MAP.get(_CN_TO_KEY.get(name, ""))
        if condition is not None:
            effect = effect.merge(condition.effect)
    if exhaustion:
        effect = effect.merge(exhaustion_effect(int(exhaustion)))
    return effect


def exhaustion_effect(level: int) -> ConditionEffect:
    """力竭第 ``level`` 级（1~6）的机械效果（5e 2014 表）。"""
    level = max(0, min(6, int(level)))
    if level <= 0:
        return ConditionEffect()
    effect = ConditionEffect()
    if level >= 1:
        # 属性检定劣势
        effect = replace(effect, own_check=Advantage.DISADVANTAGE)
    if level >= 2:
        effect = replace(effect, speed_scale=0.5)
    if level >= 3:
        effect = replace(
            effect,
            own_attack=Advantage.DISADVANTAGE,
            own_save=Advantage.DISADVANTAGE,
        )
    if level >= 4:
        effect = replace(effect, hp_max_scale=0.5)
    if level >= 5:
        effect = replace(effect, speed_zero=True, speed_scale=0.0)
    if level >= 6:
        effect = replace(effect, notes=("力竭 6 级：死亡",))
    return effect


def is_incapacitated(conditions: Iterable[str] | None) -> bool:
    """是否处于无法行动的状态（震慑 / 麻痹 / 昏迷 / 石化 / 失能）。"""
    return merge_effects(conditions).incapacitated


def attack_advantage(
    attacker_conditions: Iterable[str] | None,
    target_conditions: Iterable[str] | None,
    *,
    melee: bool = True,
    distance: int = 0,
) -> tuple[Advantage, str]:
    """按双方状态算一次攻击的优势姿态。

    :param melee: 是否近战攻击（倒地的目标只让 5 尺内的近战获得优势）。
    :param distance: 攻击者与目标的距离（尺），用于上面的 5 尺判定。
    :return: ``(优势姿态, 原因文本)``；原因为空表示没有状态参与。
    """
    attacker = merge_effects(attacker_conditions)
    target = merge_effects(target_conditions)
    advantage = _combine(attacker.own_attack, target.vs_attack)
    reasons: list[str] = []
    if attacker.own_attack is not Advantage.NORMAL:
        reasons.append(
            "攻击者" + ("有优势" if attacker.own_attack is Advantage.ADVANTAGE else "有劣势")
        )
    if target.vs_attack is not Advantage.NORMAL:
        reasons.append(
            "目标" + ("有优势" if target.vs_attack is Advantage.ADVANTAGE else "有劣势")
        )
    # 倒地：5 尺内近战优势、远程劣势（写进 target 的 notes 太绕，这里显式补）
    if "倒地" in normalize_conditions(target_conditions):
        if melee and 0 <= distance <= 5:
            advantage = _combine(advantage, Advantage.ADVANTAGE)
            reasons.append("目标倒地且攻击者在 5 尺内：近战优势")
        elif not melee:
            advantage = _combine(advantage, Advantage.DISADVANTAGE)
            reasons.append("目标倒地：远程劣势")
    if "麻痹" in normalize_conditions(target_conditions) and melee and 0 <= distance <= 5:
        reasons.append("目标麻痹：5 尺内命中自动暴击")
    reason = "；".join(reasons)
    return advantage, reason


def save_auto_fail(
    conditions: Iterable[str] | None, ability: str
) -> bool:
    """某项豁免是否因状态自动失败（麻痹 / 震慑 / 昏迷 / 石化的力量与敏捷）。"""
    ability_obj = str(ability or "").strip().lower()
    aliases = {
        "力量": "str",
        "敏捷": "dex",
        "体质": "con",
        "智力": "int",
        "感知": "wis",
        "魅力": "cha",
    }
    key = aliases.get(ability_obj, ability_obj)
    return key in merge_effects(conditions).auto_fail_saves


def speed_scale(conditions: Iterable[str] | None, *, exhaustion: int = 0) -> float:
    """速度倍率（力竭 2 级减半、5 级归零；束缚 / 擒抱归零）。"""
    return merge_effects(conditions, exhaustion=exhaustion).speed_scale


def resolve_damage(
    amount: int,
    damage_type: str,
    resistances: Iterable[str] | None,
) -> tuple[int, str]:
    """按抗性表结算伤害。

    :param amount: 原始伤害。
    :param damage_type: 伤害类型（火焰 / 挥砍……；空字符串不过抗性）。
    :param resistances: 抗性条目，形如 ``"火焰"``（抗性）、``"毒素:免疫"``、
        ``"钝击:易伤"``、``"挥砍:抗性(非魔法)"``（只挡非魔法攻击，会注明）。
    :return: ``(最终伤害, 说明文本)``；没有命中抗性表时说明为空串。
    """
    amount = max(0, int(amount))
    dtype = str(damage_type or "").strip()
    if not dtype:
        return amount, ""
    entries = [str(item).strip() for item in (resistances or ()) if str(item).strip()]
    notes: list[str] = []
    factor = 1.0
    for entry in entries:
        if ":" in entry or "：" in entry:
            target, _, level = entry.replace("：", ":").partition(":")
            target, level = target.strip(), level.strip()
        else:
            target, level = entry, "抗性"
        if target not in {dtype, "全部"} and not (
            target == "非魔法" and dtype in PHYSICAL_TYPES
        ):
            continue
        if level.startswith("免疫"):
            factor = 0.0
            notes.append(f"{dtype}免疫" + ("（非魔法武器）" if "非魔法" in level else ""))
        elif level == "易伤":
            factor *= 2.0
            notes.append(f"{dtype}易伤")
        elif level.startswith("抗性"):
            factor *= 0.5
            if "非魔法" in level:
                notes.append(f"{dtype}抗性（非魔法武器）")
            else:
                notes.append(f"{dtype}抗性")
    if not notes:
        return amount, ""
    final = int(amount * factor)
    if final == 0 and amount > 0:
        final = 0 if factor == 0.0 else 1
    return final, "；".join(notes)


def resistances_text(resistances: Iterable[str] | None) -> str:
    """抗性表的人类可读文本；空表返回空串。"""
    entries = [str(item).strip() for item in (resistances or ()) if str(item).strip()]
    if not entries:
        return ""
    parts: list[str] = []
    for entry in entries:
        if ":" in entry or "：" in entry:
            target, _, level = entry.replace("：", ":").partition(":")
            parts.append(f"{target.strip()}{level.strip()}")
        else:
            parts.append(f"{entry}抗性")
    return "、".join(parts)


def condition_text(conditions: Iterable[str] | None, *, exhaustion: int = 0) -> str:
    """一组状态的效果速览（给卡面 / 战斗块用）；没有状态返回空串。"""
    names = normalize_conditions(conditions)
    if not names and not exhaustion:
        return ""
    parts: list[str] = []
    for name in names:
        condition = CONDITION_MAP.get(_CN_TO_KEY.get(name, ""))
        if condition is not None:
            parts.append(f"{name}（{condition.desc}）")
        else:
            parts.append(f"{name}（自定义备注）")
    if exhaustion:
        parts.append(f"力竭 {exhaustion} 级")
    return "；".join(parts)
