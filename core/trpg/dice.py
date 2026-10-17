"""通用骰子工具。

本模块不依赖任何具体规则体系，供 :mod:`..trpg` 基类与各规则系统（DND 等）复用。
提供骰子表达式解析、掷骰与常见掷骰法。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "MAX_DICE_COUNT",
    "MAX_DICE_FACES",
    "Advantage",
    "DiceError",
    "DiceExpr",
    "RollResult",
    "parse_dice",
    "roll",
    "roll_4d6_drop_lowest",
    "roll_d20",
    "roll_scores_4d6",
]

MAX_DICE_COUNT = 100
MAX_DICE_FACES = 1000

_DICE_RE = re.compile(
    r"^\s*(?P<count>\d*)\s*[dD]\s*(?P<faces>\d+)"
    r"\s*(?:(?P<sign>[+-])\s*(?P<modifier>\d+))?\s*$"
)


class DiceError(ValueError):
    """非法的骰子表达式。"""


class Advantage(Enum):
    """d20 掷骰的优势 / 劣势。"""

    NORMAL = "普通"
    ADVANTAGE = "优势"
    DISADVANTAGE = "劣势"

    @property
    def dice_count(self) -> int:
        """该姿态下需要掷的 d20 数量。"""
        return 1 if self is Advantage.NORMAL else 2

    @property
    def is_advantage(self) -> bool:
        return self is Advantage.ADVANTAGE

    @property
    def is_disadvantage(self) -> bool:
        return self is Advantage.DISADVANTAGE


@dataclass(frozen=True, slots=True)
class DiceExpr:
    """骰子表达式，如 ``2d6+3``。"""

    count: int
    faces: int
    modifier: int = 0

    def __str__(self) -> str:
        text = f"{self.count}d{self.faces}"
        if self.modifier:
            text += f"{self.modifier:+d}"
        return text

    def validate(self) -> None:
        if self.count < 1:
            raise DiceError(f"骰子数量必须大于 0：{self}")
        if self.count > MAX_DICE_COUNT:
            raise DiceError(f"骰子数量过多（上限 {MAX_DICE_COUNT}）：{self}")
        if self.faces < 2:
            raise DiceError(f"骰子面数至少为 2：{self}")
        if self.faces > MAX_DICE_FACES:
            raise DiceError(f"骰子面数过多（上限 {MAX_DICE_FACES}）：{self}")


def parse_dice(expr: str | DiceExpr) -> DiceExpr:
    """解析骰子表达式，支持 ``d20`` / ``2d6`` / ``1d8+3`` / ``4d6-1``。"""
    if isinstance(expr, DiceExpr):
        expr.validate()
        return expr
    if not isinstance(expr, str):
        raise DiceError(f"无法解析的骰子表达式：{expr!r}")
    match = _DICE_RE.match(expr)
    if not match:
        raise DiceError(f"无法解析的骰子表达式：{expr!r}，示例：2d6+3")
    count = int(match.group("count") or 1)
    faces = int(match.group("faces"))
    modifier = int(match.group("modifier") or 0)
    if match.group("sign") == "-":
        modifier = -modifier
    parsed = DiceExpr(count=count, faces=faces, modifier=modifier)
    parsed.validate()
    return parsed


@dataclass(slots=True)
class RollResult:
    """一次掷骰的结果。"""

    expr: str
    rolls: list[int]
    modifier: int = 0
    dropped: list[int] = field(default_factory=list)
    label: str = ""

    @property
    def total(self) -> int:
        return sum(self.rolls) + self.modifier

    def describe(self) -> str:
        prefix = f"{self.label}：" if self.label else ""
        detail = "+".join(str(r) for r in self.rolls) if self.rolls else "0"
        text = f"{prefix}[{detail}]"
        if self.dropped:
            text += f"（弃 {', '.join(str(d) for d in self.dropped)}）"
        if self.modifier:
            text += f"{self.modifier:+d}"
        return f"{text} = {self.total}"


def _resolve_rng(rng: random.Random | None) -> random.Random:
    return rng if rng is not None else random


def roll(
    expr: str | DiceExpr,
    *,
    label: str = "",
    rng: random.Random | None = None,
) -> RollResult:
    """掷一次骰子表达式。"""
    parsed = parse_dice(expr)
    generator = _resolve_rng(rng)
    rolls = [generator.randint(1, parsed.faces) for _ in range(parsed.count)]
    return RollResult(
        expr=str(parsed), rolls=rolls, modifier=parsed.modifier, label=label
    )


def roll_4d6_drop_lowest(
    *, rng: random.Random | None = None
) -> tuple[list[int], list[int], int]:
    """DND 标准掷属性法：4d6 取最高的三个。

    :return: ``(全部点数, 保留的点数, 合计)``
    """
    generator = _resolve_rng(rng)
    rolls = [generator.randint(1, 6) for _ in range(4)]
    kept = sorted(rolls, reverse=True)[:3]
    return rolls, kept, sum(kept)


def roll_scores_4d6(
    count: int = 6, *, rng: random.Random | None = None
) -> tuple[list[list[int]], list[int]]:
    """按 4d6 去最低生成 ``count`` 组属性值。

    :return: ``(每组原始点数, 降序排列的合计值)``
    """
    if count < 1:
        raise DiceError("属性组数必须大于 0")
    raw: list[list[int]] = []
    totals: list[int] = []
    for _ in range(count):
        rolls, _kept, total = roll_4d6_drop_lowest(rng=rng)
        raw.append(rolls)
        totals.append(total)
    return raw, sorted(totals, reverse=True)


def roll_d20(
    advantage: Advantage = Advantage.NORMAL,
    *,
    rng: random.Random | None = None,
) -> tuple[list[int], int]:
    """掷 d20，按优势 / 劣势保留高点 / 低点。

    :return: ``(全部点数, 参与结算的点数)``
    """
    generator = _resolve_rng(rng)
    rolls = [generator.randint(1, 20) for _ in range(advantage.dice_count)]
    if advantage.is_advantage:
        return rolls, max(rolls)
    if advantage.is_disadvantage:
        return rolls, min(rolls)
    return rolls, rolls[0]
