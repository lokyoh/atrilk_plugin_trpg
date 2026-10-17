"""规则判断器基类。

DM Agent 工作流（``.agents/new_plusin_lktrpg.md``）中的第 3 步：
玩家 @ 机器人给出行动后，先由规则判断器判断这一行动是否符合本规则体系的要求，
不符合就退回第 2 步要求玩家重新描述，符合才交给 Agent 生成结果。

各规则体系（DND 等）继承 :class:`RuleJudge` 实现自己的判断逻辑。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "JudgeResult",
    "KeywordRule",
    "KeywordRuleJudge",
    "RuleJudge",
    "Verdict",
]


class Verdict(Enum):
    """规则判断结论。"""

    ACCEPT = "通过"  # 符合规则，交给 Agent 处理
    REJECT = "打回"  # 不符合规则，要求玩家重新描述
    NEED_INFO = "追问"  # 信息不足，需要玩家补充


@dataclass(slots=True)
class JudgeResult:
    """规则判断结果。"""

    verdict: Verdict
    reason: str = ""
    suggestion: str = ""

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPT

    def message(self) -> str:
        """给玩家的回复文案。"""
        if self.accepted:
            return self.reason or "行动符合规则，正在结算……"
        text = self.reason or "这个行动不太可行，请重新描述。"
        if self.suggestion:
            text += f"\n提示：{self.suggestion}"
        return text


@dataclass(slots=True)
class KeywordRule:
    """基于正则的关键词规则，命中后直接给出结论。"""

    pattern: str
    verdict: Verdict
    reason: str
    flags: int = re.IGNORECASE
    suggestion: str = ""

    def match(self, action: str) -> bool:
        return re.search(self.pattern, action, self.flags) is not None


class RuleJudge(ABC):
    """规则判断器基类。"""

    #: 该判断器适用的规则体系标识
    system: str = ""

    @abstractmethod
    async def judge(self, action: str, context: dict | None = None) -> JudgeResult:
        """判断玩家行动是否符合规则。"""
        raise NotImplementedError

    def rules_text(self) -> str:
        """给玩家看的规则摘要，子类可覆盖。"""
        return ""


class KeywordRuleJudge(RuleJudge):
    """只依赖关键词规则的判断器，可作为规则体系的兜底实现。"""

    #: 命中即打回的规则（如违规内容、越权行动）
    reject_rules: tuple[KeywordRule, ...] = ()
    #: 命中即追问的规则（如信息不足、指令歧义）
    need_info_rules: tuple[KeywordRule, ...] = ()
    #: 命中即通过的正向规则（如明确的检定 / 攻击 / 移动指令）
    accept_rules: tuple[KeywordRule, ...] = ()
    #: 全部未命中时的默认结论（子类可覆盖）
    fallback: JudgeResult = JudgeResult(
        Verdict.NEED_INFO,
        reason="没看懂你想做什么。",
        suggestion="请描述一个具体的行动，例如「我推开门」「我用长剑砍向地精」。",
    )

    async def judge(self, action: str, context: dict | None = None) -> JudgeResult:
        text = (action or "").strip()
        if not text:
            return JudgeResult(
                Verdict.NEED_INFO,
                reason="你还没有描述行动。",
                suggestion="请 @ 我并说明角色要做什么。",
            )
        for group, default in (
            (self.reject_rules, Verdict.REJECT),
            (self.need_info_rules, Verdict.NEED_INFO),
            (self.accept_rules, Verdict.ACCEPT),
        ):
            for rule in group:
                if rule.match(text):
                    return JudgeResult(
                        rule.verdict if rule.verdict else default,
                        reason=rule.reason,
                        suggestion=rule.suggestion,
                    )
        return JudgeResult(
            self.fallback.verdict,
            reason=self.fallback.reason,
            suggestion=self.fallback.suggestion,
        )
