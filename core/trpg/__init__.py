"""与具体规则体系无关的跑团基类与通用工具。

对外统一从这里导入，规则体系（``core.dnd``）只需依赖本包。
"""

from .character import TrpgCharacter
from .check import DC_LEVELS, CheckResult, dc_level, reroll_check, roll_check
from .dice import (
    Advantage,
    DiceError,
    DiceExpr,
    RollResult,
    parse_dice,
    roll,
    roll_4d6_drop_lowest,
    roll_d20,
    roll_scores_4d6,
)
from .rule import (
    JudgeResult,
    KeywordRule,
    KeywordRuleJudge,
    RuleJudge,
    Verdict,
)
from .scene import SceneEvent, TrpgScene
from .text import bullet, kv_block, rule_line, truncate

__all__ = [
    "DC_LEVELS",
    "Advantage",
    "CheckResult",
    "DiceError",
    "DiceExpr",
    "JudgeResult",
    "KeywordRule",
    "KeywordRuleJudge",
    "RollResult",
    "RuleJudge",
    "SceneEvent",
    "TrpgCharacter",
    "TrpgScene",
    "Verdict",
    "bullet",
    "dc_level",
    "kv_block",
    "parse_dice",
    "reroll_check",
    "roll",
    "roll_4d6_drop_lowest",
    "roll_check",
    "roll_d20",
    "roll_scores_4d6",
    "rule_line",
    "truncate",
]
