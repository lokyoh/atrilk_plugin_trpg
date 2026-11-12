"""DMG 2024「战斗或逃跑」的士气判定（纯规则，不碰战斗流程）。

原文（参考文档 L1650-1668）：

- **开战前**：不确定一个生物是否愿意战斗时，掷先攻前让它过 **DC 10 感知豁免**；
  失败 = 逃跑或谈判，成功 = 愿意参战。**群体由首领代表掷一次**。
- **已参战**：满足任一条件时很可能逃跑 ——
  ① 回合开始时**浴血**且**超过一半盟友**已死亡/失能，而对方无人倒下；
  ② 回合开始时浴血且陷入**恐慌**。此时 DM 可直接判逃跑，也可让它再过一次
  DC 10 感知豁免，失败则逃跑或谈判。

这里只做可断言的纯函数；「谁算盟友、谁倒下了」由 :mod:`.combat` 的调用点组装。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..trpg.check import CheckResult, roll_check

__all__ = [
    "MORALE_DC",
    "MoraleResult",
    "bloodied",
    "flee_reasons",
    "morale_check",
]

#: 士气检定的默认 DC（DMG 2024：可视情况调高或调低）
MORALE_DC = 10


@dataclass(frozen=True, slots=True)
class MoraleResult:
    """一次士气检定的结果。"""

    check: CheckResult

    @property
    def willing(self) -> bool:
        """豁免成功 = 愿意继续战斗 / 参战。"""
        return self.check.success

    def describe(self) -> str:
        return self.check.describe()


def morale_check(
    wis_mod: int, *, dc: int = MORALE_DC, rng: Any = None
) -> MoraleResult:
    """掷一次士气检定（DC 10 感知豁免，默认值可用 ``dc`` 覆盖）。"""
    return MoraleResult(
        check=roll_check(
            int(wis_mod), int(dc), kind="士气（感知）", rng=rng
        )
    )


def bloodied(hp: int, max_hp: int) -> bool:
    """是否浴血：生命值不超过上限的一半，且还没倒下。"""
    hp, max_hp = int(hp or 0), int(max_hp or 0)
    return hp > 0 and max_hp > 0 and hp * 2 <= max_hp


def flee_reasons(
    *,
    is_bloodied: bool,
    allies_total: int,
    allies_down: int,
    foes_down: int,
    frightened: bool,
) -> tuple[str, ...]:
    """按 DMG 2024 返回「该考虑逃跑」的理由；空元组 = 不触发。

    严格按原文两条：

    1. 浴血 + 超过一半盟友死亡/失能 + **对方无人**死亡/失能；
    2. 浴血 + 恐慌。

    「一半」按**严格超过**算（4 个盟友倒下 2 个不算，倒 3 个才算）。
    """
    reasons: list[str] = []
    if (
        is_bloodied
        and int(allies_total) > 0
        and int(allies_down) * 2 > int(allies_total)
        and int(foes_down) == 0
    ):
        reasons.append("浴血且超过一半盟友已死亡/失能，而对方无人倒下")
    if is_bloodied and frightened:
        reasons.append("浴血且陷入恐慌")
    return tuple(reasons)
