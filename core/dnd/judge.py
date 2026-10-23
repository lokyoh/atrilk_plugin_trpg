"""DND 规则判断器。

对应 DM Agent 工作流第 3 步：玩家 @ 机器人描述行动后，先判断行动是否符合 DND 规则，
不符合就打回让玩家重新描述，符合才交给 Agent 生成结果。

判断分两层：

1. **指令层**：识别玩家直接给出的检定 / 骰子指令（如 ``力量检定``、``2d6+3``、
   ``察觉 DC15``），这类请求直接转成 :class:`~..trpg.check.CheckResult`。
2. **叙述层**：把自然语言行动映射到可能的检定，或判定其不可行（如凭空飞行、
   声明自己无所不能等越权叙述）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..trpg.dice import Advantage, DiceError, roll
from ..trpg.rule import JudgeResult, KeywordRule, KeywordRuleJudge, Verdict
from .attributes import ABILITY_MAP
from .card import DndCharacter
from .difficulty import dc_for
from .skills import SKILL_MAP, get_skill

__all__ = [
    "COMBAT_REASON",
    "DndAction",
    "DndRuleJudge",
    "dnd_judge",
    "parse_dice_request",
    "parse_skill_request",
]

#: 命中即打回：明显违背规则、或试图让 AI 直接给出结果的叙述
REJECT_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        r"(我是神|我就是神|无所不能|想做什么就做什么|直接赢|立即获胜|秒杀全场)",
        Verdict.REJECT,
        reason="宣言式的「无敌」行动不符合跑团规则。",
        suggestion="请描述角色**具体**做什么，例如「我冲向地精并用长剑攻击」。",
    ),
    KeywordRule(
        r"(修改|篡改|删掉|重写)(我|角色)?(的)?(属性|骰子|结果|血量|生命值)",
        Verdict.REJECT,
        reason="玩家不能直接修改角色数据或骰子结果。",
        suggestion="想改变结果可以尝试其他行动，或接受这次失败继续推进剧情。",
    ),
    KeywordRule(
        r"(跳过|略过)(检定|判定|规则)",
        Verdict.REJECT,
        reason="跑团需要按规则进行判定，不能跳过。",
        suggestion="请描述行动，我会按规则掷骰结算。",
    ),
)

#: 命中即追问：信息不足
NEED_INFO_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        r"^\s*(我|我要|我想)\s*[。.!！?？]*\s*$",
        Verdict.NEED_INFO,
        reason="没有说明具体行动。",
        suggestion="例如「我用长剑砍向最近的敌人」「我检查墙上的浮雕」。",
    ),
    KeywordRule(
        r"(怎么办|做什么|接下来呢|随便|你决定)\s*[?？]?\s*$",
        Verdict.NEED_INFO,
        reason="需要你决定角色的行动。",
        suggestion="可以描述一个具体动作，或说「观察四周」让我先描述环境。",
    ),
    KeywordRule(
        r"(死亡豁免|濒死豁免|掷死亡豁免)",
        Verdict.NEED_INFO,
        reason="死亡豁免由系统在轮到你的回合时自动掷（DC 10），不用自己提交。",
        suggestion="等自己的回合，或让队友用治疗让你恢复意识。",
    ),
)

#: 战斗行动的判定说明。
#:
#: 命中它就是**战斗动作**（攻击 / 施法）——这类行动由 ``start_combat`` 之后的系统
#: 结算（attack / cast_spell / check），判断器**不再替玩家预掷检定**：
#: 从前「用法术妖精之火攻击」会被当成「提到魔法」而掷一次智力（奥秘），
#: 于是同一次行动被结算两遍，存档里留下与实际结果矛盾的判定。
COMBAT_REASON = "战斗行动。"

#: 命中即通过：明确的行动指令
ACCEPT_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        r"(攻击|砍|劈|刺|射|施法|施放|释放法术|投掷)",
        Verdict.ACCEPT,
        reason=COMBAT_REASON,
    ),
    KeywordRule(
        r"(移动|跑|走|冲|跳|爬|潜行|躲藏|后退)", Verdict.ACCEPT, reason="移动行动。"
    ),
    KeywordRule(
        r"(观察|查看|检查|搜索|聆听|嗅|摸索|调查)", Verdict.ACCEPT, reason="探索行动。"
    ),
    KeywordRule(
        r"(交谈|说服|欺骗|威吓|询问|打听|交涉|谈判)",
        Verdict.ACCEPT,
        reason="交涉行动。",
    ),
    KeywordRule(
        r"(休息|长休|短休|治疗|包扎|喝药)", Verdict.ACCEPT, reason="恢复行动。"
    ),
)


@dataclass(slots=True)
class DndAction:
    """一次行动被规则判断器解析后的结论。"""

    judge: JudgeResult
    #: 需要执行的检定（若规则上要求检定）
    check_kind: str = ""
    check_bonus: int = 0
    dc: int = 0
    advantage: Advantage = Advantage.NORMAL

    @property
    def needs_check(self) -> bool:
        return bool(self.check_kind)


class DndRuleJudge(KeywordRuleJudge):
    """DND 规则判断器。"""

    system = "DND5e"
    reject_rules = REJECT_RULES
    need_info_rules = NEED_INFO_RULES
    accept_rules = ACCEPT_RULES
    #: 未命中任何关键词的自由叙述直接放行，交给 DM 结合局面裁决；
    #: 判断器只拦「明显违规」（REJECT_RULES）与「明显没说清」（NEED_INFO_RULES），
    #: 不当自然语言的门卫——「我环顾四周」这类正常描述不该被拦下。
    fallback = JudgeResult(Verdict.ACCEPT, reason="")

    async def judge_action(
        self,
        action: str,
        character: DndCharacter | None = None,
        context: dict | None = None,
    ) -> DndAction:
        """解析行动：先认直接指令，再走关键词规则，最后结合角色给出检定建议。"""
        text = (action or "").strip()

        # 1. 纯骰子指令：2d6+3
        dice_expr = parse_dice_request(text)
        if dice_expr:
            try:
                result = roll(dice_expr, label="自由掷骰")
            except DiceError as e:
                return DndAction(JudgeResult(Verdict.NEED_INFO, reason=str(e)))
            return DndAction(
                JudgeResult(Verdict.ACCEPT, reason=f"自由掷骰：{result.describe()}"),
            )

        # 2. 技能 / 属性检定指令：察觉 / 力量检定 / 察觉 DC15
        if character is not None:
            check = parse_skill_request(text)
            if check:
                skill_key, dc, advantage = check
                result = self._roll(character, skill_key, dc, advantage)
                return DndAction(
                    JudgeResult(Verdict.ACCEPT, reason=result.describe()),
                    check_kind=result.kind,
                    check_bonus=result.bonus,
                    dc=dc,
                    advantage=advantage,
                )

        # 3. 关键词规则
        base = await self.judge(text, context)

        # 4. 通过时，若叙述里含检定关键词且带角色，附上建议检定
        #    战斗动作例外：命中 / 伤害 / 豁免由战斗系统结算，判断器不预掷——
        #    预掷出的那次（如施法被当成「提到魔法」掷奥秘）会与正式结算打架。
        if base.accepted and character is not None and base.reason != COMBAT_REASON:
            suggested = self._suggest_check(text, character)
            if suggested:
                skill_key, dc, advantage = suggested
                result = self._roll(character, skill_key, dc, advantage)
                base.reason = (
                    f"{base.reason}\n{result.describe()}"
                    if base.reason
                    else result.describe()
                )
                return DndAction(
                    base,
                    check_kind=result.kind,
                    check_bonus=result.bonus,
                    dc=dc,
                    advantage=advantage,
                )
        return DndAction(base)

    @staticmethod
    def _roll(character: DndCharacter, key: str, dc: int, advantage: Advantage):
        """按技能 / 属性键执行检定。"""
        if key in SKILL_MAP:
            return character.check_skill(key, dc, advantage=advantage)
        return character.check_ability(key, dc, advantage=advantage)

    @staticmethod
    def _suggest_check(
        text: str, character: DndCharacter
    ) -> tuple[str, int, Advantage] | None:
        """从叙述里猜一个检定与难度；猜不出返回 ``None``。

        DC 不再一律 15：由 :mod:`.difficulty` 按「技能基准难度 + 叙述里的难度
        描述词」定档——偷偷潜行（容易）与徒手破门（困难）不该同档。
        """
        # 叙述中直接出现的技能名优先
        for name, skill in ((s.cn, s) for s in SKILL_MAP.values()):
            if name in text:
                return skill.key, dc_for(text, skill.key), Advantage.NORMAL
        for ability in ABILITY_MAP.values():
            if ability.cn in text:
                return ability.key, dc_for(text, ability.key), Advantage.NORMAL

        rules: tuple[tuple[str, str], ...] = (
            (r"(潜行|偷偷|隐蔽|摸过去)", "stealth"),
            (r"(说服|劝|请求)", "persuasion"),
            (r"(欺骗|撒谎|假装)", "deception"),
            (r"(威吓|威胁|恐吓)", "intimidation"),
            (r"(观察|查看|察觉|留意)", "perception"),
            (r"(搜索|检查|调查|研究)", "investigation"),
            (r"(攀爬|跳|游泳|推|拉|举起)", "athletics"),
            (r"(悄悄|平衡|翻滚|挣脱)", "acrobatics"),
            (r"(野外|追踪|辨识方向)", "survival"),
            (r"(历史|传说|典故)", "history"),
            # 奥秘是「辨识魔法」的技能，只在真的去辨认时才掷；
            # 光是提到「用魔法 / 用法术」属于施法，走 cast_spell 而不是奥秘检定。
            (
                r"(符文|魔法阵|奥术|辨识.{0,6}(魔法|法术)"
                r"|(魔法|法术).{0,4}(痕迹|效果|残留|来源))",
                "arcana",
            ),
        )
        for pattern, key in rules:
            if re.search(pattern, text):
                return key, dc_for(text, key), Advantage.NORMAL
        return None

    def rules_text(self) -> str:
        return (
            "行动请尽量具体：做什么、用什么、对谁。\n"
            "可直接下检定指令，如「察觉」「力量检定」「察觉 DC15」「2d6+3」。"
        )


#: 全局默认判断器
dnd_judge = DndRuleJudge()


def parse_dice_request(text: str) -> str | None:
    """识别纯骰子指令，返回规范化表达式；不是骰子指令则返回 ``None``。"""
    raw = (text or "").strip()
    for prefix in ("/r", "/roll", "骰", "掷骰", "roll"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :].strip()
            break
    if not re.fullmatch(r"\d*[dD]\d+([+-]\d+)?", raw):
        return None
    return raw


def parse_skill_request(text: str) -> tuple[str, int, Advantage] | None:
    """识别检定指令，返回 ``(技能 / 属性键, DC, 优势姿态)``；不是检定指令则返回 ``None``。

    支持写法：``察觉``、``力量检定``、``察觉DC15``、``力量豁免 DC 20``、
    ``察觉 优势``、``隐匿劣势``。

    玩家没写 DC 时不一律按 15：先按技能的基准难度
    （见 :data:`~.difficulty.SKILL_BASE_DC`，察言观色这类偏容易、开门撬锁
    这类偏难），再按指令里有没有「悄悄」「徒手」之类的难度词调整。
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 24:
        return None

    advantage = Advantage.NORMAL
    if re.search(r"优势|好处|adv", raw, re.IGNORECASE):
        advantage = Advantage.ADVANTAGE
    elif re.search(r"劣势", raw):
        advantage = Advantage.DISADVANTAGE

    dc_match = re.search(r"(?:dc|难度|DC)\s*(\d{1,2})", raw, re.IGNORECASE)
    #: 玩家显式写的 DC 优先；没写则按技能基准 + 难度词算（``None`` = 待定）
    explicit_dc: int | None = None
    if dc_match:
        explicit_dc = int(dc_match.group(1))
    else:
        plain_dc = re.search(r"(?<![\d.])(\d{1,2})(?![\d.])", raw)
        if plain_dc:
            explicit_dc = int(plain_dc.group(1))

    body = re.sub(r"(?:dc|难度)\s*\d{1,2}", "", raw, flags=re.IGNORECASE)
    body = re.sub(r"\d{1,2}", "", body)
    body = re.sub(r"(检定|判定|豁免|检定骰|优势|劣势|一下|一次)", "", body).strip()
    body = body.strip("，,。.！!？?、 ")

    if not body or body in {"骰子", "掷骰", "roll"}:
        return None

    #: 没写 DC 时，按命中的技能 / 属性定基准难度
    def _resolve(key: str) -> tuple[str, int, Advantage]:
        if explicit_dc is not None:
            return key, max(1, min(30, explicit_dc)), advantage
        return key, dc_for(raw, key), advantage

    for skill in SKILL_MAP.values():
        if body in (skill.cn, skill.key, skill.en.lower()):
            return _resolve(skill.key)
    for ability in ABILITY_MAP.values():
        if body in (ability.cn, ability.key, ability.abbr.lower(), ability.en.lower()):
            return _resolve(ability.key)

    # 「力量（运动）」这类带属性的技能写法
    bracket = re.match(
        rf"^({'|'.join(a.cn for a in ABILITY_MAP.values())})[（(]?(.+?)[）)]?$", body
    )
    if bracket:
        try:
            return _resolve(get_skill(bracket.group(2)).key)
        except KeyError:
            pass
    return None
