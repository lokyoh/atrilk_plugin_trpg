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
