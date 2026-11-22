"""通用 d20 检定工具。

把「掷骰 + 加值 + 难度」量化成 :class:`CheckResult`，并负责把结果翻译成玩家可读的文案。
规则体系只需提供加值与难度（DC），无需关心掷骰细节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

from .dice import Advantage, roll_d20

__all__ = [
    "DC_LEVELS",
    "CheckResult",
    "dc_level",
    "group_check_passes",
    "reroll_check",
    "roll_check",
]

DC_LEVELS: tuple[tuple[int, str], ...] = (
    (5, "非常简单"),
    (10, "简单"),
    (15, "中等"),
    (20, "困难"),
    (25, "非常困难"),
    (30, "几乎不可能"),
)


def dc_level(dc: int) -> str:
    """把难度值翻译成难度描述（取不超过 dc 的最高档）。"""
    level = "近乎儿戏"
    for limit, name in DC_LEVELS:
        if dc >= limit:
            level = name
    return level


@dataclass(slots=True)
class CheckResult:
    """一次检定 / 豁免 / 攻击检定的结果。"""

    kind: str
    dc: int
    natural: int
    total: int
    bonus: int = 0
    advantage: Advantage = Advantage.NORMAL
    rolls: list[int] = field(default_factory=list)
    #: 天然 20 必成功 / 天然 1 必失败——**只对攻击检定（与死亡豁免）生效**。
    #: 属性检定与豁免检定的天然 1 / 20 没有特殊含义（2014 与 2024 均如此；
    #: 「属性检定天然 20 自动成功」是常见房规，本局不采用——口径见 res/rule.md），
    #: 所以默认关，攻击调用点显式打开。
    auto_critical: bool = False
    #: 强制失败（状态导致：麻痹 / 震慑 / 昏迷 / 石化的力量与敏捷豁免）。
    #: 骰子照掷（给玩家看点数），但结果恒为失败。
    forced_failure: bool = False
    #: 强制成功（**传奇抗性**：豁免失败时可以改成成功，每日有次数）。
    #: 与 :attr:`forced_failure` 同时成立时**成功优先**——传奇抗性的意义就是
    #: 不让首领被一次豁免摁死。
    forced_success: bool = False
    #: 这次检定被「重掷」替换掉的原始点数（英雄激励这类效应）；``None`` = 没重掷过。
    #: 重掷**必须采用新结果**，所以旧点数只留作展示，不参与成败判定。
    rerolled_from: int | None = None

    @property
    def nat20(self) -> bool:
        """d20 是否掷出自然 20。"""
        return self.natural == 20

    @property
    def nat1(self) -> bool:
        """d20 是否掷出自然 1。"""
        return self.natural == 1

    @property
    def margin(self) -> int:
        """与难度的差值（正数即达成）。"""
        return self.total - self.dc

    @property
    def consequence_hint(self) -> str:
        """DMG 2024 的**成败后果层**（L1008-1028），只给 DM 的裁定提示。

        刻意不写进 :meth:`describe`：那个文案要进玩家「🎲 判定」卡，也会被十几个
        战斗 / 豁免调用点复用，逐条挂建议会把战斗日志刷成废话。这里单独取，
        由 DM 的 ``check`` 工具在回执里附一句；**提示不等于改判**，采纳与否由 DM 定。

        - 失败只差 1~2：可给「有代价的成功」，用困境换达成（L1012-1020）；
        - 失败差 ≥5：可追加更坏后果，如触发陷阱、惊动守卫（L1022-1024）；
        - 成功大幅超出：按超过 DC 的程度给更高完成度 / 额外收获（L1026-1028）。

        攻击检定（``auto_critical``）不适用「有代价的成功」与「更坏后果」——
        命中就是命中、失手就是失手，但成功程度（比试计分）仍然适用。

        ⚠️ ``check`` 的返回会同时被 :func:`...agents.dnd.game._player_rulings`
        收进玩家的判定卡，所以 ``game._strip_dm_noise`` 在发出去之前会按上面四句
        的开头把整段剥掉。加新句式时记得同步那条正则，否则这句又漏到玩家脸上。
        """
        if self.forced_success or self.forced_failure:
            return ""
        if self.auto_critical and (self.nat20 or self.nat1):
            return ""
        margin = self.margin
        if self.success:
            if margin >= 10:
                return "（大幅超出 DC：可给额外收获或更高完成度）"
            if margin >= 5:
                return "（明显超出 DC：可给额外细节或优势）"
            return ""
        if self.auto_critical:
            return ""
        if margin >= -2:
            return "（只差 1~2：可给「有代价的成功」，用困境换达成）"
        if margin <= -5:
            return "（差 ≥5：可追加更坏后果，如触发陷阱 / 惊动守卫）"
        return ""

    @property
    def success(self) -> bool:
        """是否达成。默认由 d20 自然 20 / 自然 1 直接决定成败。"""
        if self.forced_success:
            return True
        if self.forced_failure:
            return False
        if self.auto_critical:
            if self.nat20:
                return True
            if self.nat1:
                return False
        return self.total >= self.dc

    def describe(self) -> str:
        """可读的检定文案，例如 ``力量（运动）检定：[17]+3 = 20 ≥ DC15 成功``。

        难度同时给**数值和档位名**（DC15 中等）——DM 按数值判断是否合理，
        玩家只看得懂「中等 / 困难」这种档位，两边各取所需，不用维护两份文案。
        """
        rolls_text = (
            "+".join(str(r) for r in self.rolls) if self.rolls else str(self.natural)
        )
        text = f"{self.kind}：[{rolls_text}]"
        if len(self.rolls) > 1:
            text += f" 取 {self.natural}"
        if self.bonus:
            text += f"{self.bonus:+d}"
        text += f" = {self.total}"
        if self.rerolled_from is not None:
            text += f"（重掷 {self.rerolled_from}→{self.natural}，用新结果）"
        if self.forced_success:
            return f"{text}（传奇抗性：改为豁免成功）"
        if self.forced_failure:
            return f"{text}（状态导致自动失败）"
        if self.auto_critical and self.nat20:
            return f"{text}（大成功）"
        if self.auto_critical and self.nat1:
            return f"{text}（大失败）"
        verdict = "成功" if self.success else "失败"
        label = self.dc_label
        return (
            f"{text} / DC{self.dc} {label} {verdict}"
            if label
            else f"{text} / DC{self.dc} {verdict}"
        )

    @property
    def dc_label(self) -> str:
        """难度的档位名（非常简单 / 容易 / 中等 / 困难……），玩家看得懂这个。

        惰性导入：本模块是``core.trpg`` 里的通用 d20 层，不该反向依赖规则包
        ``core.dnd``，所以等真的用到档位名时再去取。
        """
        try:
            from ..dnd.difficulty import describe_dc
        except ImportError:  # 规则包未就位时不该让检定崩掉
            return ""
        return describe_dc(self.dc)


def roll_check(
    bonus: int,
    dc: int,
    *,
    kind: str = "检定",
    advantage: Advantage = Advantage.NORMAL,
    auto_critical: bool = False,
    rng: Random | None = None,
) -> CheckResult:
    """执行一次 d20 检定。

    :param bonus: 检定加值（属性调整值 + 熟练加值等）
    :param dc: 难度值
    :param kind: 检定名称，仅用于展示
    :param advantage: 优势 / 劣势 / 普通
    :param auto_critical: 是否让天然 20 必成功、天然 1 必失败。
        **只有攻击检定该开**（PHB 两版相同：攻击检定天然 20 必中、天然 1 必失）；
        属性检定与豁免检定不开——那里的 1 和 20 只是很糟 / 很好的一次点数。
    """
    rolls, natural = roll_d20(advantage, rng=rng)
    return CheckResult(
        kind=kind,
        dc=dc,
        natural=natural,
        total=natural + bonus,
        bonus=bonus,
        advantage=advantage,
        rolls=rolls,
        auto_critical=auto_critical,
    )


def group_check_passes(successes: int, total: int) -> bool:
    """DMG 2024 团体检定：至少一半成功则整组成功（平局算成功）。

    原文（第二章「团体检定」）：「当多个角色一起进行同一项属性检定或豁免时，
    每个人都进行检定；如果**至少一半**的人成功，整组就成功。」没有「过半」的
    额外要求——4 人 2 人成功也算整组成功；0 人不算。
    """
    return total > 0 and successes * 2 >= total


def reroll_check(result: CheckResult, *, rng: Random | None = None) -> CheckResult:
    """把一次检定里的**一颗 d20** 重掷掉，返回新结果。

    PHB 2024「英雄激励」：掷完骰子后可以消耗它重掷一次，**必须采用新结果**；
    有优势 / 劣势时只能重掷其中一颗（由玩家选哪颗）。这里默认重掷**算数那一颗**
    ——优势时就是高的那颗、劣势时就是低的那颗，也就是玩家最想换掉的那颗。

    新结果可能比原来更差：那是规则的一部分，不该偷偷保留旧值。
    """
    rolls = list(result.rolls) or [result.natural]
    # 重掷「算数那颗」：它的点数就是参与比较的那个（平手时取第一颗）
    index = rolls.index(result.natural) if result.natural in rolls else 0
    generator = rng or Random()
    old = rolls[index]
    rolls[index] = generator.randint(1, 20)
    if result.advantage.is_advantage:
        natural = max(rolls)
    elif result.advantage.is_disadvantage:
        natural = min(rolls)
    else:
        natural = rolls[index]
    return CheckResult(
        kind=result.kind,
        dc=result.dc,
        natural=natural,
        total=natural + result.bonus,
        bonus=result.bonus,
        advantage=result.advantage,
        rolls=rolls,
        auto_critical=result.auto_critical,
        forced_failure=result.forced_failure,
        forced_success=result.forced_success,
        rerolled_from=old,
    )
