"""建卡流程。

.. code-block:: text

    选择模式 → roll 属性 → 背景 → 背景属性提升 → 种族 → 职业 → 技能 → 装备包 → 选法术

**背景属性提升**（PHB 2024）：属性加值只来自背景 —— 背景给出三项属性，玩家在其中
选「一项 +2、另一项 +1」或「三项各 +1」（合计 +3，单项不超过 20）。种族**不**提供
属性加值，所以这一步必须紧跟在选背景之后（属性表要知道是哪三项）。

「选法术」只对施法职业出现（野蛮人 / 战士 / 武僧 / 游荡者会自动跳过）。
PHB 2024 起**圣武士 / 游侠 1 级就有施法**：只有 2 个一环法术、没有戏法。

流程以 :class:`CreationSession` 承载状态，每一步都由 :func:`creation_prompt`
给出指引、由 :meth:`CreationSession.handle` 解析玩家回复。会话对象可序列化成
``dict`` 直接存进 matcher 的 ``state``，因此不需要额外的会话存储。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..trpg.dice import roll_4d6_drop_lowest
from ..trpg.text import bullet
from .alignment import alignment_options, normalize_alignment
from .attributes import (
    ABILITIES,
    ABILITY_SCORE_MAX,
    ABILITY_SCORE_MIN,
    STANDARD_ARRAY,
    get_ability,
)
from .background import Background, background_choices, get_background
from .card import DndCharacter
from .coins import merge_coins, parse_coin_text
from .equipment import (
    WEAPONS,
    expand_packs,
    get_mastery,
    get_weapon,
    parse_item_stack,
    weapon_options_for,
    weapon_proficient,
)
from .feats import get_feat
from .languages import COMMON, standard_language_names
from .names import lk_name
from .options import (
    DIVINE_ORDERS,
    ELDRITCH_INVOCATIONS,
    FIGHTING_STYLES,
    PRIMAL_ORDERS,
    FeatureOption,
    available_invocations,
    feature_option,
)
from .profession import DndClass, class_choices, get_class
from .progression import (
    EPIC_BOON_LEVEL,
    LEVEL_XP_THRESHOLDS,
    MAX_LEVEL,
    asi_levels_for,
)
from .race import Race, get_race, race_choice_lines, race_choices
from .skills import get_skill
from .spells import Spell, get_spell, spells_for_class
from .subclass import SUBCLASS_LEVEL, SubClass, resolve_subclass, subclass_choices

__all__ = [
    "ABILITY_SET_COUNT",
    "CHOICE_STEPS",
    "CREATION_STEPS",
    "STEPS",
    "SUPPORTED_SYSTEMS",
    "CreationError",
    "CreationSession",
    "CreationStep",
    "build_character",
    "creation_prompt",
    "parse_multi_choice",
    "session_from_card",
]


class CreationError(Exception):
    """玩家输入无法解析时抛出，由 handler 捕获后重新提示。"""


class CreationStep(Enum):
    """建卡步骤。"""

    MODE = "选择模式"
    #: 开卡等级（1~20）。**放在最前面**：等级决定要不要选子职、要做几次属性提升。
    LEVEL = "选择等级"
    ABILITY = "roll属性"
    BACKGROUND = "选择背景"
    BOOST = "背景属性提升"
    RACE = "选择种族"
    LANGUAGES = "选择语言"
    PROFESSION = "选择职业"
    #: 子职（PHB 2024：所有职业 **3 级**获得；1~2 级跳过这一步）
    SUBCLASS = "选择子职"
    SKILL = "选择技能"
    FEATURES = "选择职业特性"
    EQUIPMENT = "选择装备包"
    SPELLS = "选择法术"
    #: 属性提升（ASI：4/8/12/16 级，战士 6/14、游荡者 10 额外一次）
    ASI = "属性提升"
    #: 阵营（PHB 2024 第四步；纯扮演标记，无机械效果，允许跳过）
    ALIGNMENT = "选择阵营"
    DONE = "完成"


#: 建卡步骤顺序：等级 → 属性 → 背景 → 属性提升 → 种族 → 语言 → 职业 → 子职 →
#: 技能 → 特性 → 装备包 → 法术 → 属性提升（ASI）→ 阵营。
#: 背景紧跟在属性后面，是因为 2024 的属性提升要落在背景给的三项上（D4/A1）；
#: 「特性」放在技能之后，是因为游荡者的专精要从**已熟练的技能**里挑；
#: ASI 放在最后，是因为它叠加在「roll + 背景提升」之上（高等级开卡才会出现）。
CREATION_STEPS: tuple[CreationStep, ...] = (
    CreationStep.MODE,
    CreationStep.LEVEL,
    CreationStep.ABILITY,
    CreationStep.BACKGROUND,
    CreationStep.BOOST,
    CreationStep.RACE,
    CreationStep.LANGUAGES,
    CreationStep.PROFESSION,
    CreationStep.SUBCLASS,
    CreationStep.SKILL,
    CreationStep.FEATURES,
    CreationStep.EQUIPMENT,
    CreationStep.SPELLS,
    CreationStep.ASI,
    CreationStep.ALIGNMENT,
    CreationStep.DONE,
)

#: 兼容命名
STEPS = CREATION_STEPS

#: 需要从选项列表里挑一个的步骤
CHOICE_STEPS: frozenset[CreationStep] = frozenset(
    {
        CreationStep.MODE,
        CreationStep.LEVEL,
        CreationStep.RACE,
        CreationStep.LANGUAGES,
        CreationStep.PROFESSION,
        CreationStep.SUBCLASS,
        CreationStep.BACKGROUND,
        CreationStep.SKILL,
        CreationStep.EQUIPMENT,
        CreationStep.SPELLS,
        CreationStep.ALIGNMENT,
    }
)

#: 目前支持的规则体系（回复键 -> 展示名）
SUPPORTED_SYSTEMS: dict[str, str] = {"dnd": "DND 5e", "dnd5e": "DND 5e", "5e": "DND 5e"}

_AUTO_WORDS = {"自动", "推荐", "随意", "你定", "auto"}
_SKIP_WORDS = {"跳过", "不选", "不用", "无", "skip", "否"}
_ROLL_WORDS = {
    "roll",
    "掷骰",
    "掷",
    "骰",
    "骰子",
    "重掷",
    "重摇",
    "再掷",
    "reroll",
    "重来",
}
_ARRAY_WORDS = {"标准数组", "标准", "数组", "默认"}

#: 背景起源专长「魔法学徒」括号里的法术表（中文 -> 职业内部键）
_MAGIC_INITIATE_CLASSES: dict[str, str] = {
    "牧师": "cleric",
    "德鲁伊": "druid",
    "法师": "wizard",
}

#: 1 级特性里「从固定菜单挑一个」的四种（问题键 -> 标题 + 选项）
_FEATURE_MENUS: dict[str, tuple[str, tuple[FeatureOption, ...]]] = {
    "divine_order": ("圣职", DIVINE_ORDERS),
    "primal_order": ("原初职能", PRIMAL_ORDERS),
    "fighting_style": ("战斗风格", FIGHTING_STYLES),
    "invocation": ("魔能祈唤", ELDRITCH_INVOCATIONS),
}


def _is_generic_weapon(item: str) -> bool:
    """起始包里的「任意军用武器 / 任意简易武器」这类**待定武器**。"""
    text = str(item or "")
    return "任意" in text and "武器" in text


def _match_ability(token: str, options: tuple[str, ...] | list[str]) -> str | None:
    """把玩家写的属性词匹配到 ``options`` 里的某一项（认中文名 / 键 / 英文 / 缩写）。"""
    text = token.strip().lower()
    for key in options:
        ability = get_ability(key)
        if text in (ability.key, ability.cn, ability.en.lower(), ability.abbr.lower()):
            return key
    for key in options:
        ability = get_ability(key)
        if ability.cn in text or ability.key in text:
            return key
    return None


def _pick_option(
    text: str, options: tuple[FeatureOption, ...], what: str
) -> FeatureOption:
    """从一组特性选项里按序号 / 名称挑一个。"""
    picked = _pick_one(text, [option.cn for option in options], what)
    for option in options:
        if option.cn == picked:
            return option
    raise CreationError(f"没有「{picked}」这个{what}。")


def _prereq_text(option: FeatureOption) -> str:
    """把魔能祈唤的先决条件拼成一句中文（无先决返回空串）。"""
    pre = option.prereq
    if not pre.level and not pre.feature and not pre.spell:
        return ""
    bits: list[str] = []
    if pre.level:
        bits.append(f"魔契师 {pre.level} 级")
    if pre.feature:
        other = feature_option("eldritch_invocations", pre.feature)
        bits.append(f"先选「{other.cn if other else pre.feature}」")
    if pre.spell:
        bits.append(pre.spell)
    return "、".join(bits)


#: roll 属性一次掷出的候选组数（玩家从中挑一组直接使用）
ABILITY_SET_COUNT = 5


def _split_choices(text: str) -> list[str]:
    """把玩家回复拆成多个选项（支持空格、逗号、顿号、加号分隔）。"""
    normalized = text
    for sep in ("、", ",", "，", "+", "/", "|", ";", "；"):
        normalized = normalized.replace(sep, " ")
    return [part for part in normalized.split() if part]


def _to_index(token: str) -> int | None:
    """把 ``1`` / ``第1个`` 之类的写法解析成列表下标（0 基）。"""
    cleaned = token.strip()
    for wrapper in ("第", "个", "项", "号"):
        cleaned = cleaned.replace(wrapper, "")
    if cleaned.isdigit():
        return int(cleaned) - 1
    return None


def _pick_one(text: str, options: list[str], what: str) -> str:
    """从 ``options`` 里按序号或名称挑一个。"""
    tokens = _split_choices(text)
    if not tokens:
        raise CreationError(f"没有收到内容，请输入{what}的序号或名称。")
    raw = tokens[0]
    index = _to_index(raw)
    if index is not None:
        if index < 0 or index >= len(options):
            raise CreationError(f"序号超出范围，可选 1~{len(options)}。")
        return options[index]
    exact = [opt for opt in options if opt == raw]
    if exact:
        return exact[0]
    matches = [opt for opt in options if raw in opt]
    if not matches:
        raise CreationError(f"没有「{raw}」这个{what}。")
    if len(matches) > 1:
        raise CreationError(
            f"「{raw}」对应多个{what}（{'、'.join(matches)}），请输入序号。"
        )
    return matches[0]


def parse_multi_choice(
    text: str,
    options: list[str] | tuple[str, ...],
    count: int,
    *,
    allow_skip: bool = False,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """从 ``options`` 里解析出 ``count`` 个选择。

    支持序号（``1 3 5``）与名称（``运动 察觉``）混用。
    """
    tokens = _split_choices(text)
    if not tokens:
        raise CreationError("没有收到内容，请输入选项序号或名称。")
    if allow_skip and len(tokens) == 1 and tokens[0].lower() in _SKIP_WORDS:
        return []

    picked: list[str] = []
    for token in tokens:
        alias = (aliases or {}).get(token)
        if alias is not None:
            token = alias
        index = _to_index(token)
        if index is not None:
            if index < 0 or index >= len(options):
                raise CreationError(f"序号 {token} 超出范围，可选 1~{len(options)}。")
            value = options[index]
        else:
            exact = [opt for opt in options if opt == token]
            matches = exact or [opt for opt in options if token in opt]
            if not matches:
                raise CreationError(f"没有「{token}」这个选项。")
            if len(matches) > 1:
                raise CreationError(
                    f"「{token}」对应多个选项（{'、'.join(matches)}），请输入序号。"
                )
            value = matches[0]
        if value in picked:
            raise CreationError(f"「{value}」重复选择了。")
        picked.append(value)
        if len(picked) >= count:
            break

    if len(picked) < count:
        raise CreationError(f"需要选择 {count} 项，目前只选了 {len(picked)} 项。")
    return picked


def creation_prompt(step: CreationStep, session: CreationSession | None = None) -> str:
    """给出某一步的提示文本。"""
    if step is CreationStep.MODE:
        options = bullet(
            f"{i}. {name}"
            for i, name in enumerate(dict.fromkeys(SUPPORTED_SYSTEMS.values()), 1)
        )
        return f"欢迎来到跑团！先选择要玩的规则模式：\n{options}\n回复序号即可（目前仅支持 DND 5e）。"

    if step is CreationStep.ABILITY:
        array_text = "/".join(str(v) for v in STANDARD_ARRAY)
        candidates = session.ability_candidates if session else []
        if candidates:
            lines = [
                f"第 1 步：选择属性组。已掷出 {len(candidates)} 组"
                "（每项 4d6 取最高的三个，按「"
                + " ".join(a.cn for a in ABILITIES)
                + "」顺序展示）："
            ]
            for i, scores in enumerate(candidates, 1):
                shown = "  ".join(f"{v:>2}" for v in scores)
                lines.append(f"{i}. {shown}（合计 {sum(scores)}）")
            lines.append(
                f"回复序号（1~{len(candidates)}）选一组直接使用；"
                f"回复「重掷」再掷 {ABILITY_SET_COUNT} 组，"
                f"或回复「标准数组」使用 {array_text}。"
            )
        else:
            lines = [
                "第 1 步：roll 属性。",
                f"回复「roll」掷 {ABILITY_SET_COUNT} 组完整属性"
                "（DND 5e：每项 4d6 取最高的三个），挑一组直接使用；"
                f"或回复「标准数组」使用 {array_text}。",
            ]
        return "\n".join(lines)

    if step is CreationStep.RACE:
        lines = [
            "选择种族：",
            bullet(f"{i}. {name}" for i, name in enumerate(race_choice_lines(), 1)),
        ]
        if session and session.base_scores:
            lines.append("")
            lines.append("当前属性：" + session.final_scores_text())
        lines.append(
            "回复序号或种族名；血系已列在种族后面，可写「精灵 高等精灵」直接指定。"
        )
        lines.append("（PHB 2024：种族不提供属性加值，属性提升已在上一步由背景给出。）")
        return "\n".join(lines)

    if step is CreationStep.LANGUAGES:
        session_count = session.language_count() if session else 2
        session_pool = session.language_pool() if session else list(
            standard_language_names()
        )
        lines = [
            f"选择语言：除了人人都懂的 {COMMON}，再自选 {session_count} 门"
            "（第二章 2133，可回复「跳过」）：",
            bullet(f"{i}. {name}" for i, name in enumerate(session_pool, 1)),
            f"回复 {session_count} 个序号或语言名（例如「1 5」）；"
            "种族额外给的自选已经算进上面的数量里。",
        ]
        return "\n".join(lines)

    if step is CreationStep.LEVEL:
        return "\n".join(
            [
                "选择开卡等级（1~20）：",
                "1 级就是常规新角色；想直接跑高等级战役就写等级（如「5」「第 5 级」）。",
                "等级会影响：生命上限、熟练加值、法术位、**3 级起要选子职**、"
                "以及 4/8/12/16 级的属性提升（战士 6/14、游荡者 10 另有）。",
                "回复「1」或直接回「跳过」按 1 级开卡。",
                "（各等级的职业新特性引擎不逐级发放，由 DM 按职业表补，见「规则口径」。）",
            ]
        )

    if step is CreationStep.PROFESSION:
        return "\n".join(
            [
                "选择职业：",
                bullet(f"{i}. {name}" for i, name in enumerate(class_choices(), 1)),
                "回复序号或职业名。",
            ]
        )

    if step is CreationStep.SUBCLASS:
        options = session.subclass_options() if session else ()
        if not options:
            return "选择子职：1~2 级还没有子职，这一步可以跳过。"
        profession = session.profession_obj if session else None
        lines = [
            f"选择子职（{profession.cn if profession else '本职业'}，"
            f"PHB 2024 所有职业都在 {SUBCLASS_LEVEL} 级获得）：",
            bullet(f"{i}. {sub.short}（{sub.cn}）" for i, sub in enumerate(options, 1)),
            "回复序号或子职名（写短名或全名都行）。",
        ]
        for sub in options:
            if sub.desc:
                lines.append(f"{sub.short}：{sub.desc}")
        return "\n".join(lines)

    if step is CreationStep.BACKGROUND:
        return "\n".join(
            [
                "确定你的起源：选择背景（可回复「跳过」）：",
                bullet(
                    f"{i}. {name}" for i, name in enumerate(background_choices(), 1)
                ),
                "背景会给你：三项属性提升（下一步选 +2/+1 或三项各 +1）、"
                "两项技能熟练、一项工具熟练、起始物品（与职业技能不重复）。",
                "2024 起背景还固定给**一项起源专长**（第五章，如侍僧→魔法学徒），"
                "它会直接记在你的角色卡上。",
            ]
        )

    if step is CreationStep.BOOST:
        background = session.background_obj if session else None
        options = session.boost_options() if session else ()
        if not background or not options:
            return "背景属性提升：当前背景没有可提升的属性。"
        current = (
            session.assigned_scores_text() if session and session.base_scores else "未分配"
        )
        return "\n".join(
            [
                f"背景属性提升（{background.cn}）：",
                f"PHB 2024：属性加值只来自背景 —— 你这项背景给了这三项："
                f"{background.bonus_text}",
                f"当前（未提升）：{current}",
                "",
                "选一种：",
                "1. 一项 +2、另一项 +1 —— 回复「1 智力 感知」"
                "（前一项 +2、后一项 +1）",
                "2. 三项各 +1 —— 回复「2」",
                f"（合计 +3，单项不超过 {ABILITY_SCORE_MAX}；"
                "回复「跳过」不加，但会弱于规则角色）",
            ]
        )

    if step is CreationStep.SKILL:
        profession = session.profession_obj if session else None
        if not profession:
            return "选择技能：需要先确定职业。"
        lines = [
            f"选择技能（{profession.cn}，选 {profession.skill_count} 项）：",
            bullet(
                f"{i}. {name}" for i, name in enumerate(profession.skill_names(), 1)
            ),
            "回复序号或技能名，多项用空格 / 逗号分隔（例如「1 3」）。",
        ]
        if session and session.background_obj:
            provided = session.background_skill_names()
            if provided:
                lines.append(
                    f"背景「{session.background_obj.cn}」已提供：" + "、".join(provided)
                )
        return "\n".join(lines)

    if step is CreationStep.FEATURES:
        if not session:
            return "选择职业特性：需要先确定职业。"
        pending = session.pending_features()
        if not pending:
            return "1 级特性都选好了。"
        lines = [session.feature_prompt(pending[0])]
        if len(pending) > 1:
            lines.append(f"（这一步还有 {len(pending) - 1} 个选择在后面）")
        return "\n".join(lines)

    if step is CreationStep.EQUIPMENT:
        # 起始包里还有「任意军用武器」这类待定项：先让玩家选具体武器
        if session and session.pending_weapon:
            return session.pending_weapon_prompt()
        profession = session.profession_obj if session else None
        if not profession:
            return "选择装备包：需要先确定职业。"
        return "\n".join(
            [
                f"选择起始装备包（{profession.cn}）：",
                profession.format_equipment(),
                "回复包序号（例如「1」），或回复「跳过」使用默认装备。",
            ]
        )

    if step is CreationStep.SPELLS:
        profession = session.profession_obj if session else None
        if not profession:
            return "选择法术：需要先确定职业。"
        cantrips, known = (
            session.spell_picks()
            if session
            else (profession.cantrips, profession.spells_known)
        )
        options = session.spell_options() if session else []
        if cantrips:
            head = f"戏法 {cantrips} 个 + 一环法术 {known} 个"
        else:
            head = f"一环法术 {known} 个（本职业没有戏法）"
        lines = [
            f"选择法术（{profession.cn}：{head}，"
            f"施法属性 {profession.casting_text}）："
        ]
        numbers = {id(spell): index for index, spell in enumerate(options, 1)}
        cantrip_entries = [(numbers[id(s)], s) for s in options if s.level == 0]
        spell_entries = [(numbers[id(s)], s) for s in options if s.level >= 1]

        def _format(entries: list[tuple[int, Spell]]) -> list[str]:
            rows: list[str] = []
            for start in range(0, len(entries), 8):
                chunk = entries[start : start + 8]
                rows.append("  " + "  ".join(f"{i}.{s.cn}" for i, s in chunk))
            return rows

        if cantrip_entries:
            lines.append("◆ 戏法（0 环，可无限施展）：")
            lines.extend(_format(cantrip_entries))
        if spell_entries:
            lines.append("◆ 一环法术：")
            lines.extend(_format(spell_entries))
        lines.append(
            "回复序号（例如「1 3 5 8 9 10」），或直接写法术名（用逗号分隔）；"
            "回复「自动」由我按列表顺序挑。"
        )
        return "\n".join(lines)

    if step is CreationStep.ASI:
        pending = session.asi_pending() if session else 0
        done = session.asi_done if session else 0
        total = session.asi_count() if session else 0
        lines = [
            f"属性提升（ASI）：这次是第 {done + 1} / {total} 次"
            + (f"（还剩 {pending} 次）" if pending else ""),
            "选一种：",
            "1. 一项 +2 —— 回复「力量 +2」（「力量+2」也行）",
            "2. 两项各 +1 —— 回复「力量 +1 体质 +1」",
            f"（单项不超过 {ABILITY_SCORE_MAX}；回复「自动」加到主属性，"
            "回复「跳过」放弃剩余加点）",
        ]
        if session and session.base_scores:
            lines.append("当前属性：" + session.final_scores_text())
        if session and session.epic_boon_unlocked():
            lines.append(
                f"⚠️ Lv.{EPIC_BOON_LEVEL} 还该有一项**传奇恩惠**（第五章，不是属性提升）："
                "建卡后由 DM 与你一起挑一项，用「改卡 特性」补上。"
            )
        return "\n".join(lines)

    if step is CreationStep.ALIGNMENT:
        return "\n".join(
            [
                "选择阵营（纯扮演标记，**没有任何机械效果**；可以回复「跳过」）：",
                alignment_options(),
                "回复序号或阵营名（例如「混乱善良」「CG」）。",
            ]
        )

    return "建卡完成！"


@dataclass
class CreationSession:
    """一次建卡会话的状态。"""

    user_id: str
    step: CreationStep = CreationStep.MODE
    system: str = ""
    name: str = ""
    #: 会话用途：``"create"`` = 建卡；``"levelup"`` = 升级后的选择会话
    #: （只走子职 / 属性加点，完成后写回角色卡、回到游戏）。
    #: 同一个 ``CreationSession`` 承载两种流程，提示与收尾文案按它区分。
    purpose: str = "create"
    #: 升级会话所属的存档 id（空 = 无存档）。升级发生在游戏内，答完的新卡
    #: 要写回**存档**（``set_character``）而不是插件目录的全局卡。
    save_id: str = ""
    #: 开卡等级（1~20）。**高等级开卡**：HP / 熟练加值 / 法术位 / 属性提升次数
    #: 都按它算；3 级起必须选子职。
    level: int = 1
    #: 已做的属性提升（ASI）次数与加点：属性键 -> 加点（与背景提升分开记，
    #: 落卡时合并进 ``ability_boost``）
    asi: dict[str, int] = field(default_factory=dict)
    #: 已完成的 ASI 次数（逐次询问，直到等于 :meth:`asi_count`）
    asi_done: int = 0
    #: 掷出的候选属性值（降序）
    ability_pool: list[int] = field(default_factory=list)
    #: 每组属性的原始 4d6 点数，按掷出顺序保存，用于回放展示
    ability_groups: list[list[int]] = field(default_factory=list)
    #: roll 时掷出的候选属性组（每组 6 个值，按属性展示顺序），等玩家挑一组
    ability_candidates: list[list[int]] = field(default_factory=list)
    #: 候选属性组对应的 4d6 原始点数，与 ``ability_candidates`` 一一对应
    ability_set_groups: list[list[list[int]]] = field(default_factory=list)
    #: 已分配属性：属性键 -> 值（**未含**背景提升）
    base_scores: dict[str, int] = field(default_factory=dict)
    #: 背景属性提升：属性键 -> 加点（PHB 2024 的 +2/+1 或 +1/+1/+1）
    boost: dict[str, int] = field(default_factory=dict)
    race: str = ""
    subrace: str = ""
    profession: str = ""
    #: 子职内部键（3 级以上开卡必选，见 :mod:`.subclass`）
    subclass: str = ""
    background: str = ""
    skills: list[str] = field(default_factory=list)
    equipment_package: str = ""
    equipment: list[str] = field(default_factory=list)
    #: 起始包里尚未确定的泛称武器（如「任意军用近战武器」），非空时要先选具体武器
    pending_weapon: str = ""
    #: 选好的法术（法术 key，含戏法）
    spells: list[str] = field(default_factory=list)
    #: 1 级特性的**选择**：``divine_order`` / ``primal_order`` / ``fighting_style`` /
    #: ``invocation`` -> 选项键（见 :mod:`.options`）
    feature_choices: dict[str, str] = field(default_factory=dict)
    #: 专精（游荡者）：技能内部键
    expertise: list[str] = field(default_factory=list)
    #: 武器精通：武器中文名
    weapon_masteries: list[str] = field(default_factory=list)
    #: 额外戏法（牧师「施法者」/ 德鲁伊「术士」原初职能额外习得）：法术 key
    extra_cantrips: list[str] = field(default_factory=list)
    #: 自选语言（第二章 2133：通用语之外再自选两门；种族额外给的自选也记在这里）
    languages: list[str] = field(default_factory=list)
    #: 阵营（第二步第四章 / 第二章第四步；纯扮演标记，可留空）
    alignment: str = ""
    #: 背景起源专长给的法术（魔法学徒：2 戏法 + 1 道一环，法术 key）
    feat_spells: list[str] = field(default_factory=list)
    #: 交互日志（记录玩家每一步的选择，便于回放）
    log: list[str] = field(default_factory=list)

    # —— 序列化（存进 matcher.state） ——

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "step": self.step.name,
            "system": self.system,
            "name": self.name,
            "purpose": self.purpose,
            "save_id": self.save_id,
            "level": self.level,
            "asi": dict(self.asi),
            "asi_done": self.asi_done,
            "ability_pool": list(self.ability_pool),
            "ability_groups": [list(g) for g in self.ability_groups],
            "ability_candidates": [list(s) for s in self.ability_candidates],
            "ability_set_groups": [
                [list(g) for g in groups] for groups in self.ability_set_groups
            ],
            "base_scores": dict(self.base_scores),
            "boost": dict(self.boost),
            "race": self.race,
            "subrace": self.subrace,
            "profession": self.profession,
            "subclass": self.subclass,
            "background": self.background,
            "skills": list(self.skills),
            "equipment_package": self.equipment_package,
            "equipment": list(self.equipment),
            "pending_weapon": self.pending_weapon,
            "spells": list(self.spells),
            "feature_choices": dict(self.feature_choices),
            "expertise": list(self.expertise),
            "weapon_masteries": list(self.weapon_masteries),
            "extra_cantrips": list(self.extra_cantrips),
            "languages": list(self.languages),
            "alignment": self.alignment,
            "feat_spells": list(self.feat_spells),
            "log": list(self.log),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreationSession:
        raw_step = data.get("step", CreationStep.MODE.name)
        step = (
            CreationStep[raw_step]
            if isinstance(raw_step, str) and raw_step in CreationStep.__members__
            else CreationStep.MODE
        )
        return cls(
            user_id=str(data.get("user_id", "")),
            step=step,
            system=data.get("system", ""),
            name=data.get("name", ""),
            purpose=str(data.get("purpose", "") or "create"),
            save_id=str(data.get("save_id", "") or ""),
            level=max(1, min(int(data.get("level", 1) or 1), MAX_LEVEL)),
            asi={str(k): int(v) for k, v in data.get("asi", {}).items()},
            asi_done=int(data.get("asi_done", 0) or 0),
            ability_pool=[int(v) for v in data.get("ability_pool", [])],
            ability_groups=[
                [int(v) for v in g] for g in data.get("ability_groups", [])
            ],
            ability_candidates=[
                [int(v) for v in s] for s in data.get("ability_candidates", [])
            ],
            ability_set_groups=[
                [[int(v) for v in g] for g in groups]
                for groups in data.get("ability_set_groups", [])
            ],
            base_scores={k: int(v) for k, v in data.get("base_scores", {}).items()},
            boost={k: int(v) for k, v in data.get("boost", {}).items()},
            race=data.get("race", ""),
            subrace=data.get("subrace", ""),
            profession=data.get("profession", ""),
            subclass=data.get("subclass", ""),
            background=data.get("background", ""),
            skills=list(data.get("skills", [])),
            equipment_package=data.get("equipment_package", ""),
            equipment=list(data.get("equipment", [])),
            pending_weapon=data.get("pending_weapon", ""),
            spells=list(data.get("spells", [])),
            feature_choices={
                str(k): str(v) for k, v in data.get("feature_choices", {}).items()
            },
            expertise=list(data.get("expertise", [])),
            weapon_masteries=list(data.get("weapon_masteries", [])),
            extra_cantrips=list(data.get("extra_cantrips", [])),
            languages=list(data.get("languages", [])),
            alignment=str(data.get("alignment", "") or ""),
            feat_spells=list(data.get("feat_spells", [])),
            log=list(data.get("log", [])),
        )

    # —— 规则对象 ——

    @property
    def race_obj(self) -> Race | None:
        """种族对象；2024 重排删掉了半精灵 / 半兽人，旧会话认不出时给 ``None``。"""
        if not self.race:
            return None
        try:
            return get_race(self.race)
        except KeyError:
            return None

    @property
    def profession_obj(self) -> DndClass | None:
        if not self.profession:
            return None
        try:
            return get_class(self.profession)
        except KeyError:
            return None

    @property
    def background_obj(self) -> Background | None:
        """背景对象；2024 重排删掉了民间英雄 / 化外之民，旧会话认不出时给 ``None``。"""
        if not self.background:
            return None
        try:
            return get_background(self.background)
        except KeyError:
            return None

    @property
    def subclass_obj(self) -> SubClass | None:
        """子职对象；认不出给 ``None``（旧会话 / 手写文本不该让流程崩）。"""
        if not self.subclass:
            return None
        return resolve_subclass(self.subclass)

    # —— 高等级开卡 ——

    def subclass_options(self) -> tuple[SubClass, ...]:
        """本职业的可选子职（无职业 / 等级不够时为空 → 跳过该步）。"""
        if self.level < SUBCLASS_LEVEL or not self.profession:
            return ()
        return subclass_choices(self.profession)

    def asi_count(self) -> int:
        """按等级与职业应当做完的属性提升次数（19 级给的是传奇恩惠，不算在这里）。"""
        levels = asi_levels_for(self.profession)
        return sum(1 for lv in levels if lv <= self.level)

    def asi_pending(self) -> int:
        """还剩几次属性提升没分配。"""
        return max(0, self.asi_count() - self.asi_done)

    def epic_boon_unlocked(self) -> bool:
        """是否到传奇恩惠等级（19 级，第五章；只能提示，不能自动选）。"""
        return self.level >= EPIC_BOON_LEVEL

    def asi_text(self) -> str:
        """已分配的属性提升（展示用）。"""
        if not self.asi:
            return ""
        return "、".join(
            f"{get_ability(k).cn} {v:+d}" for k, v in self.asi.items() if v
        )

    # —— 展示 ——

    def ability_pool_text(self) -> str:
        if not self.ability_pool:
            return ""
        groups = self._sorted_groups()
        parts = []
        for i, value in enumerate(self.ability_pool):
            detail = "+".join(str(r) for r in groups[i]) if i < len(groups) else ""
            parts.append(f"{value}（{detail}）" if detail else str(value))
        return "候选属性：" + "  ".join(parts)

    def _sorted_groups(self) -> list[list[int]]:
        """按合计降序排列的原始点数，与 :attr:`ability_pool` 对齐。"""
        groups = [list(g) for g in self.ability_groups]
        return sorted(
            groups, key=lambda g: sum(sorted(g, reverse=True)[:3]), reverse=True
        )

    def assigned_scores_text(self) -> str:
        if not self.base_scores:
            return "未分配"
        return "  ".join(f"{a.cn}{self.base_scores.get(a.key, '?')}" for a in ABILITIES)

    def final_scores_text(self) -> str:
        """含背景提升与 ASI 的属性展示（提升选完之后才有区别）。"""
        if not self.base_scores:
            return "未分配"
        return "  ".join(
            f"{a.cn}{self.final_score(a.key)}" for a in ABILITIES
        )

    def total_boost(self, key: str) -> int:
        """某项属性的全部加值（背景提升 + 等级带来的 ASI）。"""
        return int(self.boost.get(key, 0)) + int(self.asi.get(key, 0))

    def final_score(self, key: str) -> int:
        """某项属性的最终值（未封顶，建卡时由卡对象按 20 上限收口）。"""
        return int(self.base_scores.get(key, 0)) + self.total_boost(key)

    # —— 流程推进 ——

    def boost_options(self) -> tuple[str, ...]:
        """背景提供的三项可提升属性（无背景 / 无此项返回空元组 → 跳过该步）。"""
        background = self.background_obj
        if not background:
            return ()
        return background.bonus_abilities()

    # —— 1 级特性的选择（PHB 2024）——

    def mastery_count(self) -> int:
        """本职业 1 级能选几件武器精通（0 = 没有这项特性）。"""
        profession = self.profession_obj
        return int(profession.mastery_count) if profession else 0

    def language_count(self) -> int:
        """要自选几门语言（第二章 2133：通用语之外的两门；种族额外给的算进去）。"""
        count = 2
        race = self.race_obj
        if race:
            for item in race.languages:
                if "自选" in str(item) or "任意" in str(item):
                    count += 1
                    break
        return count

    def language_pool(self) -> list[str]:
        """可选的标准语言（不含通用语 —— 通用语人人都懂）。"""
        return list(standard_language_names())

    def feature_plan(self) -> list[str]:
        """本职业建卡时要做的特性选择（固定顺序）。

        背景给的**魔法学徒**排在最后：它要给的是「2 戏法 + 1 道一环法术」，
        与职业本身的选择互不依赖，最后问不至于打断职业的主线。
        """
        plan: list[str] = []
        if self.profession == "cleric":
            plan.append("divine_order")
            if self.feature_choices.get("divine_order") == "thaumaturge":
                plan.append("extra_cantrip")
        elif self.profession == "druid":
            plan.append("primal_order")
            if self.feature_choices.get("primal_order") == "magician":
                plan.append("extra_cantrip")
        elif self.profession == "fighter":
            plan.append("fighting_style")
        elif self.profession == "warlock":
            plan.append("invocation")
        elif self.profession == "rogue":
            plan.append("expertise")
        if self.mastery_count():
            plan.append("weapon_mastery")
        if self.magic_initiate_class():
            plan.append("magic_initiate")
        return plan

    # —— 背景起源专长：魔法学徒 ——

    def magic_initiate_class(self) -> str:
        """背景给的「魔法学徒」用哪张法术表（职业键）；没有这个专长返回空串。

        背景数据写成 ``feat: 魔法学徒（牧师）``——括号里就是法术表；
        背景没写括号时退回 **法师**表（2024 三张表任选，这里只给个兜底）。
        """
        background = self.background_obj
        if not background or not background.feat:
            return ""
        try:
            feat = get_feat(background.feat)
        except KeyError:
            return ""
        if feat.key != "magic_initiate" or not feat.spell_grant.cantrips:
            return ""
        for cn, key in _MAGIC_INITIATE_CLASSES.items():
            if cn in background.feat:
                return key
        return "wizard"

    def magic_initiate_grant(self) -> tuple[int, int, int]:
        """``(戏法数, 一环法术环阶, 该环阶数量)``；没有这个专长时全 0。"""
        background = self.background_obj
        if not background or not background.feat:
            return 0, 0, 0
        try:
            feat = get_feat(background.feat)
        except KeyError:
            return 0, 0, 0
        grant = feat.spell_grant
        return grant.cantrips, grant.level, grant.count

    def magic_initiate_pool(self) -> list[Spell]:
        """魔法学徒可选的法术（戏法在前，然后一环法术）。"""
        key = self.magic_initiate_class()
        if not key:
            return []
        cantrips, level, _count = self.magic_initiate_grant()
        pool = spells_for_class(key, levels=(0, level) if level else (0,))
        cantrip_spells = sorted((s for s in pool if s.level == 0), key=lambda s: s.cn)
        other = (
            sorted((s for s in pool if s.level == level), key=lambda s: s.cn)
            if level
            else []
        )
        if not cantrips:  # pragma: no cover - 数据恒给 2 戏法
            return other
        return [*cantrip_spells, *other]

    def _magic_initiate_need(self) -> int:
        """魔法学徒总共要选几道法术。"""
        cantrips, _level, count = self.magic_initiate_grant()
        return cantrips + count

    def extra_cantrip_count(self) -> int:
        """「施法者 / 术士」原初职能额外习得的戏法数（0 = 没有这一步）。"""
        if self.profession == "cleric":
            opt = feature_option("divine_orders", self.feature_choices.get("divine_order"))
        elif self.profession == "druid":
            opt = feature_option("primal_orders", self.feature_choices.get("primal_order"))
        else:
            return 0
        return opt.extra_cantrip if opt else 0

    def extra_cantrip_pool(self) -> list[Spell]:
        """额外戏法的候选：本职业 0 环戏法。"""
        profession = self.profession_obj
        if not profession:
            return []
        return [s for s in spells_for_class(profession.key) if s.level == 0]

    def _feature_done(self, item: str) -> bool:
        if item == "expertise":
            return len(self.expertise) >= 2
        if item == "weapon_mastery":
            return len(self.weapon_masteries) >= self.mastery_count()
        if item == "extra_cantrip":
            return len(self.extra_cantrips) >= self.extra_cantrip_count()
        if item == "magic_initiate":
            return len(self.feat_spells) >= self._magic_initiate_need()
        return item in self.feature_choices

    def pending_features(self) -> list[str]:
        """还没答的特性问题（空 = 这一步可以整步跳过）。"""
        return [item for item in self.feature_plan() if not self._feature_done(item)]

    def expertise_pool(self) -> list[tuple[str, str]]:
        """专精候选：已熟练的技能 ``(键, 中文名)``。"""
        background = self.background_obj
        keys = list(
            dict.fromkeys(
                [*self.skills, *(background.skills if background else ())]
            )
        )
        return [(key, get_skill(key).cn) for key in keys]

    def mastery_pool(self) -> list[str]:
        """武器精通候选：本职业受训范围内的全部具体武器。"""
        profession = self.profession_obj
        if not profession:
            return []
        return [
            weapon.cn
            for weapon in WEAPONS
            if weapon_proficient(weapon, profession.weapon_proficiencies)
        ]

    def _feature_menu(self, item: str) -> tuple[str, tuple[FeatureOption, ...]]:
        """返回某菜单类特性的（标题, 选项）；魔能祈唤按当前等级过滤。"""
        title, options = _FEATURE_MENUS[item]
        if item == "invocation":
            # 建卡在 1 级；只显示 1 级可选项的祈唤（无先决的 5 条）。
            # 升级时复用 options.available_invocations(level=角色等级) 即可。
            options = available_invocations(level=1)
        return title, options

    def feature_prompt(self, item: str) -> str:
        """某一问的提示文本。"""
        if item in _FEATURE_MENUS:
            title, options = self._feature_menu(item)
            lines = [f"{title}（选 1 项）："]
            lines.extend(
                f"{index}. {option.cn}——{option.desc}"
                + (f"（先决：{_prereq_text(option)}）" if _prereq_text(option) else "")
                for index, option in enumerate(options, 1)
            )
            lines.append("回复序号或名称。")
            return "\n".join(lines)
        if item == "expertise":
            pool = self.expertise_pool()
            lines = ["专精：从你**已熟练**的技能里选 2 项，其熟练加值翻倍："]
            lines.extend(
                f"{index}. {cn}" for index, (_, cn) in enumerate(pool, 1)
            )
            lines.append("回复两个序号或技能名（例如「1 3」）。")
            return "\n".join(lines)
        if item == "weapon_mastery":
            pool = self.mastery_pool()
            count = self.mastery_count()
            lines = [f"武器精通：从熟练武器里选 {count} 种（获得其精通词条）："]
            for start in range(0, len(pool), 6):
                chunk = []
                for index, name in list(enumerate(pool, 1))[start : start + 6]:
                    weapon = get_weapon(name)
                    mastery = (
                        get_mastery(weapon.mastery)
                        if weapon and weapon.mastery
                        else None
                    )
                    suffix = f"（{mastery.cn}）" if mastery else ""
                    chunk.append(f"{index}.{name}{suffix}")
                lines.append("  ".join(chunk))
            lines.append(f"回复 {count} 个序号或武器名（例如「1 5」）。")
            return "\n".join(lines)
        if item == "extra_cantrip":
            count = self.extra_cantrip_count()
            pool = self.extra_cantrip_pool()
            lines = [
                f"额外戏法（{count} 个）：作为「"
                f"{'施法者' if self.profession == 'cleric' else '术士'}」"
                f"原初职能额外习得，从本职业戏法里选："
            ]
            lines.extend(f"{index}. {s.cn}" for index, s in enumerate(pool, 1))
            lines.append("回复序号或戏法名。")
            return "\n".join(lines)
        if item == "magic_initiate":
            cantrips, level, count = self.magic_initiate_grant()
            pool = self.magic_initiate_pool()
            class_key = self.magic_initiate_class()
            class_cn = next(
                (cn for cn, key in _MAGIC_INITIATE_CLASSES.items() if key == class_key),
                class_key,
            )
            cantrip_pool = [s for s in pool if s.level == 0]
            other_pool = [s for s in pool if s.level > 0]
            lines = [
                f"起源专长 魔法学徒（{class_cn}）：从{class_cn}法术表里选 "
                f"{cantrips} 道戏法 + {count} 道{level}环法术："
            ]
            offset = 0
            if cantrips:
                lines.append(f"◆ 戏法（选 {cantrips} 道）：")
                lines.append(
                    "  "
                    + "  ".join(
                        f"{i}.{s.cn}" for i, s in enumerate(cantrip_pool, offset + 1)
                    )
                )
                offset += len(cantrip_pool)
            if count and other_pool:
                lines.append(f"◆ {level}环法术（选 {count} 道）：")
                lines.append(
                    "  "
                    + "  ".join(
                        f"{i}.{s.cn}" for i, s in enumerate(other_pool, offset + 1)
                    )
                )
            lines.append(f"回复 {cantrips + count} 个序号或法术名（先戏法后{level}环法术）。")
            return "\n".join(lines)
        return "1 级特性都选好了。"

    def spell_picks(self) -> tuple[int, int]:
        """本职业建卡要选的 ``(戏法数, 一环法术数)``；``(0, 0)`` 表示不用选法术。"""
        profession = self.profession_obj
        if not profession or not profession.is_caster:
            return 0, 0
        return profession.cantrips, profession.spells_known

    def spell_options(self) -> list[Spell]:
        """本职业建卡可选的戏法 + 一环法术（戏法在前，各自按名字排序）。"""
        profession = self.profession_obj
        if not profession:
            return []
        pool = spells_for_class(profession.key)
        cantrips = sorted((s for s in pool if s.level == 0), key=lambda s: s.cn)
        first = sorted((s for s in pool if s.level == 1), key=lambda s: s.cn)
        return [*cantrips, *first]

    def advance(self) -> CreationStep:
        """进入下一步（没有可选项的步骤自动跳过）。"""
        # 属性提升一轮只处理**一次**加点：还有剩余就留在这一步（4/8/12/16 级各一次）
        if self.step is CreationStep.ASI and self.asi_pending():
            return self.step
        # 升级选择会话只做**子职与属性加点**两件事：答完当前项直接收尾
        # （或接上还欠的 ASI）——技能 / 装备 / 法术 / 阵营都是建卡时定好的，
        # 重问一遍既烦人又可能覆盖玩家已有的选择。
        if self.purpose == "levelup":
            if self.step is CreationStep.SUBCLASS and self.asi_pending():
                self.step = CreationStep.ASI
            else:
                self.step = CreationStep.DONE
            return self.step
        index = CREATION_STEPS.index(self.step)
        while True:
            index = min(index + 1, len(CREATION_STEPS) - 1)
            self.step = CREATION_STEPS[index]
            if self.step is CreationStep.SPELLS and self.spell_picks() == (0, 0):
                continue
            # 跳过背景 / 背景没有三项属性时，这一步无事可做
            if self.step is CreationStep.BOOST and not self.boost_options():
                continue
            # 本职业的 1 级特性都不带选择（或都已经选完）→ 跳过
            if self.step is CreationStep.FEATURES and not self.pending_features():
                continue
            # 1~2 级没有子职（PHB 2024：所有职业统一 3 级获得）
            if self.step is CreationStep.SUBCLASS and not self.subclass_options():
                continue
            # 还没到 4 级（或 ASI 都分配完了）→ 没有属性提升可做
            if self.step is CreationStep.ASI and not self.asi_pending():
                continue
            break
        return self.step

    def record(self, text: str) -> None:
        """记录一条交互日志。"""
        self.log.append(text)

    def _require(self, expected: CreationStep) -> None:
        """确保当前步骤正确，避免玩家跳步或状态错乱。"""
        if self.step is not expected:
            raise CreationError(f"当前处于「{self.step.value}」步骤，请先完成它。")

    # —— 统一入口 ——

    def handle(self, text: str, *, rng: random.Random | None = None) -> str:
        """按当前步骤处理玩家回复，返回给玩家的文案。

        :raises CreationError: 输入非法（handler 捕获后重新提示即可）
        """
        raw = (text or "").strip()
        step = self.step

        if step is CreationStep.MODE:
            return self.choose_mode(raw)
        if step is CreationStep.LEVEL:
            return self.choose_level(raw)
        if step is CreationStep.ABILITY:
            lowered = raw.lower()
            if lowered in _ROLL_WORDS:
                return self.roll_abilities(rng=rng)
            if lowered in _ARRAY_WORDS:
                return self.use_standard_array()
            if self.ability_candidates:
                if lowered in _AUTO_WORDS:
                    best = max(
                        range(len(self.ability_candidates)),
                        key=lambda i: sum(self.ability_candidates[i]),
                    )
                    return self.choose_ability_set(best)
                parts = _split_choices(raw)
                index = _to_index(parts[0]) if len(parts) == 1 else None
                if index is None or not 0 <= index < len(self.ability_candidates):
                    raise CreationError(
                        f"请回复 1~{len(self.ability_candidates)} 选一组属性，"
                        f"回复「重掷」再掷 {ABILITY_SET_COUNT} 组，"
                        "或回复「标准数组」使用标准数组。"
                    )
                return self.choose_ability_set(index)
            return self.assign_scores(raw)
        if step is CreationStep.RACE:
            return self.choose_race(raw)
        if step is CreationStep.LANGUAGES:
            return self.choose_languages(raw)
        if step is CreationStep.PROFESSION:
            return self.choose_profession(raw)
        if step is CreationStep.SUBCLASS:
            return self.choose_subclass(raw)
        if step is CreationStep.BACKGROUND:
            return self.choose_background(raw)
        if step is CreationStep.BOOST:
            return self.choose_boost(raw)
        if step is CreationStep.SKILL:
            return self.choose_skills(raw)
        if step is CreationStep.FEATURES:
            return self.choose_feature(raw)
        if step is CreationStep.EQUIPMENT:
            if self.pending_weapon:
                return self.choose_package_weapon(raw)
            return self.choose_equipment(raw)
        if step is CreationStep.SPELLS:
            return self.choose_spells(raw)
        if step is CreationStep.ASI:
            return self.choose_asi(raw)
        if step is CreationStep.ALIGNMENT:
            return self.choose_alignment(raw)
        return "建卡已经完成了，回复「看卡」可以查看角色卡。"

    # —— 各步骤的输入处理 ——

    def choose_level(self, text: str) -> str:
        """选择开卡等级（1~20，默认 1）。

        高等级开卡不是「把等级字段改成 20」就完事：HP、熟练加值、法术位本来就跟
        等级走；**3 级起要选子职**、**4/8/12/16 级各要做一次属性提升**（战士 6/14、
        游荡者 10 另算），这两件由后面两步补齐。职业各等级的**新特性**仍需 DM
        按职业表补（引擎不逐级发特性，见 ``res/rule.md``）。
        """
        self._require(CreationStep.LEVEL)
        raw = text.strip()
        if raw.lower() in _SKIP_WORDS or raw.lower() in _AUTO_WORDS:
            self.level = 1
        else:
            cleaned = raw
            for wrapper in ("级", "lv", "Lv", "LV", "第"):
                cleaned = cleaned.replace(wrapper, "")
            cleaned = cleaned.strip()
            if not cleaned.isdigit():
                raise CreationError(f"请回复 1~{MAX_LEVEL} 的等级数字（直接回车可用 1 级）。")
            level = int(cleaned)
            if level < 1 or level > MAX_LEVEL:
                raise CreationError(f"等级要在 1~{MAX_LEVEL} 之间。")
            self.level = level
        self.record(f"等级：Lv.{self.level}")
        self.advance()
        return f"开卡等级 Lv.{self.level}。\n\n{creation_prompt(self.step, self)}"

    def choose_mode(self, text: str) -> str:
        """选择规则模式（目前仅 DND）。"""
        self._require(CreationStep.MODE)
        raw = text.strip()
        if raw.isdigit():
            index = int(raw) - 1
            names = list(dict.fromkeys(SUPPORTED_SYSTEMS.values()))
            if index < 0 or index >= len(names):
                raise CreationError(f"序号超出范围，可选 1~{len(names)}。")
            self.system = names[index]
        else:
            key = raw.lower()
            if key not in SUPPORTED_SYSTEMS:
                raise CreationError("目前只支持 DND 5e，请输入「1」或「DND」。")
            self.system = SUPPORTED_SYSTEMS[key]
        self.record(f"模式：{self.system}")
        self.advance()
        return f"已选择「{self.system}」。\n\n{creation_prompt(self.step, self)}"

    def roll_abilities(self, rng: random.Random | None = None) -> str:
        """掷 :data:`ABILITY_SET_COUNT` 组完整属性（每项 4d6 取最高的三个），等玩家挑一组。"""
        self._require(CreationStep.ABILITY)
        sets: list[list[int]] = []
        set_groups: list[list[list[int]]] = []
        for _ in range(ABILITY_SET_COUNT):
            groups: list[list[int]] = []
            totals: list[int] = []
            for _ability in ABILITIES:
                rolls, _kept, total = roll_4d6_drop_lowest(rng=rng)
                groups.append(rolls)
                totals.append(total)
            sets.append(totals)
            set_groups.append(groups)
        self.ability_candidates = sets
        self.ability_set_groups = set_groups
        self.ability_pool = []
        self.ability_groups = []
        self.record(f"掷属性：{len(sets)} 组候选（4d6 去最低）")
        return creation_prompt(CreationStep.ABILITY, self)

    def choose_ability_set(self, index: int) -> str:
        """选用第 ``index`` 组（0 基）候选属性，按展示顺序直接分配。"""
        self._require(CreationStep.ABILITY)
        if not self.ability_candidates:
            raise CreationError("还没有掷出属性组，请先回复「roll」或「标准数组」。")
        if index < 0 or index >= len(self.ability_candidates):
            raise CreationError(
                f"序号超出范围：可选 1~{len(self.ability_candidates)}。"
            )
        picked = list(self.ability_candidates[index])
        self.ability_pool = picked
        self.ability_groups = (
            [list(g) for g in self.ability_set_groups[index]]
            if index < len(self.ability_set_groups)
            else []
        )
        self.ability_candidates = []
        self.ability_set_groups = []
        self.base_scores = {a.key: v for a, v in zip(ABILITIES, picked, strict=True)}
        self.record(f"属性：第 {index + 1} 组 {picked} → {self.assigned_scores_text()}")
        self.advance()
        return (
            f"已选择第 {index + 1} 组属性：{self.assigned_scores_text()}"
            f"\n\n{creation_prompt(self.step, self)}"
        )

    def use_standard_array(self) -> str:
        """使用标准数组（15/14/13/12/10/8），按属性顺序直接分配。"""
        self._require(CreationStep.ABILITY)
        self.ability_pool = list(STANDARD_ARRAY)
        self.ability_groups = []
        self.ability_candidates = []
        self.ability_set_groups = []
        self.base_scores = {
            a.key: v for a, v in zip(ABILITIES, self.ability_pool, strict=True)
        }
        self.record(f"属性：标准数组 → {self.assigned_scores_text()}")
        self.advance()
        return (
            f"已按标准数组分配属性：{self.assigned_scores_text()}"
            f"\n\n{creation_prompt(self.step, self)}"
        )

    def assign_scores(self, text: str) -> str:
        """按「力量 敏捷 体质 智力 感知 魅力」的顺序分配候选值。"""
        self._require(CreationStep.ABILITY)
        if not self.ability_pool:
            raise CreationError("还没有候选属性值，请先回复「roll」或「标准数组」。")

        tokens = _split_choices(text)
        if len(tokens) == 1 and tokens[0].lower() in _AUTO_WORDS:
            values = sorted(self.ability_pool, reverse=True)
        else:
            values = []
            for token in tokens:
                if not token.isdigit():
                    raise CreationError(
                        "请按顺序输入 6 个数字，例如「15 14 13 12 10 8」，或回复「自动」。"
                    )
                values.append(int(token))
            if len(values) != len(ABILITIES):
                raise CreationError(
                    f"需要 {len(ABILITIES)} 个值（顺序："
                    + " ".join(a.cn for a in ABILITIES)
                    + "）。"
                )
            if sorted(values, reverse=True) != sorted(self.ability_pool, reverse=True):
                raise CreationError(
                    f"请使用候选值 {self.ability_pool} 中的数值，不要自行改动。"
                )

        for ability, value in zip(ABILITIES, values, strict=True):
            if not ABILITY_SCORE_MIN <= value <= ABILITY_SCORE_MAX:
                raise CreationError(
                    f"{ability.cn} 的 {value} 超出 {ABILITY_SCORE_MIN}~{ABILITY_SCORE_MAX}。"
                )
        self.base_scores = {a.key: v for a, v in zip(ABILITIES, values, strict=True)}
        self.record(f"属性分配：{self.assigned_scores_text()}")
        self.advance()
        return f"属性已分配：{self.assigned_scores_text()}\n\n{creation_prompt(self.step, self)}"

    def choose_race(self, text: str) -> str:
        """选择种族，可同时指定血系（``精灵 高等精灵``）。"""
        self._require(CreationStep.RACE)
        tokens = _split_choices(text)
        if not tokens:
            raise CreationError("请输入种族名称或序号。")
        race_name = _pick_one(tokens[0], list(race_choices()), "种族")
        race = get_race(race_name)

        subrace = ""
        if race.subraces:
            sub_raw = tokens[1] if len(tokens) > 1 else ""
            if sub_raw:
                try:
                    sub_obj = race.resolve_subrace(sub_raw)
                except KeyError as e:
                    raise CreationError(str(e)) from e
                subrace = sub_obj.key if sub_obj else ""
            else:
                subrace = race.subraces[0].key

        self.race = race.key
        self.subrace = subrace
        sub_obj = race.resolve_subrace(subrace) if subrace else None
        label = f"{race.cn}·{sub_obj.cn}" if sub_obj else race.cn
        self.record(f"种族：{label}")
        self.advance()
        detail = f"速度 {race.speed} 尺，{race.size}"
        return f"已选择「{label}」（{detail}）。\n\n{creation_prompt(self.step, self)}"

    def choose_languages(self, text: str) -> str:
        """自选语言（第二章 2133：通用语之外再选两门，种族额外给的算进去）。"""
        self._require(CreationStep.LANGUAGES)
        count = self.language_count()
        pool = self.language_pool()
        raw = (text or "").strip()
        if raw.lower() in _SKIP_WORDS:
            self.languages = []
            self.record("语言：跳过")
            self.advance()
            return (
                f"已跳过语言（规则上你仍懂 {COMMON}，但自选的两门没记上）。"
                f"\n\n{creation_prompt(self.step, self)}"
            )
        picked = parse_multi_choice(raw, pool, count)
        self.languages = picked
        self.record("语言：" + "、".join(picked))
        self.advance()
        return (
            f"已记录语言：{COMMON}、{'、'.join(picked)}。"
            f"\n\n{creation_prompt(self.step, self)}"
        )

    def choose_profession(self, text: str) -> str:
        """选择职业。"""
        self._require(CreationStep.PROFESSION)
        name = _pick_one(text, list(class_choices()), "职业")
        profession = get_class(name)
        # 换职业后旧子职不再属于这个职业，清掉（3 级以上会重新问）
        if self.subclass_obj and self.subclass_obj.profession != profession.key:
            self.subclass = ""
        self.profession = profession.key
        self.record(f"职业：{profession.cn}")
        self.advance()
        saves = "、".join(get_ability(k).cn for k in profession.saves)
        return (
            f"已选择「{profession.cn}」（生命骰 {profession.hit_dice}，豁免：{saves}）。"
            f"\n\n{creation_prompt(self.step, self)}"
        )

    def choose_subclass(self, text: str) -> str:
        """选择子职（PHB 2024：所有职业 3 级获得，1~2 级不出现这一步）。"""
        self._require(CreationStep.SUBCLASS)
        options = self.subclass_options()
        if not options:
            raise CreationError("1~2 级还没有子职，这一步可以跳过。")
        names = [s.cn for s in options]
        name = _pick_one(text, names, "子职")
        sub = next(s for s in options if s.cn == name)
        self.subclass = sub.key
        self.record(f"子职：{sub.cn}")
        self.advance()
        unlocked = sub.features_up_to(self.level)
        lines = [f"已选择「{sub.cn}」（{sub.short}）。"]
        if sub.desc:
            lines.append(sub.desc)
        if unlocked:
            lines.append(
                "到 Lv."
                f"{self.level} 已解锁的特性："
                + "、".join(f"L{f.level} {f.name}" for f in unlocked)
            )
        else:
            lines.append(f"（3 级起才有子职特性，当前 Lv.{self.level} 还不用结算。）")
        return "\n".join(lines) + f"\n\n{creation_prompt(self.step, self)}"

    def choose_asi(self, text: str) -> str:
        """属性提升（ASI）：每次 +2 一点，或 +1 两点。

        一轮问一次：4 级 1 次、8 级 2 次、12 级 3 次、16 级 4 次
        （战士 6/14、游荡者 10 另加）。19 级是**传奇恩惠**，不在这里算。
        """
        self._require(CreationStep.ASI)
        pending = self.asi_pending()
        if pending <= 0:
            raise CreationError("没有待分配的属性提升，这一步可以跳过。")

        raw = text.strip()
        if raw.lower() in _SKIP_WORDS:
            self.asi_done = self.asi_count()
            self.record("属性提升：跳过")
            self.advance()
            return (
                "已跳过属性提升（规则给的加点没记上，属性会弱于同级角色）。"
                f"\n\n{creation_prompt(self.step, self)}"
            )
        if raw.lower() in _AUTO_WORDS:
            # 自动：把 +2 加到主属性上（没有主属性就加体质）
            profession = self.profession_obj
            abilities = (
                list(profession.primary) if profession and profession.primary else ["con"]
            )
            self._apply_asi({abilities[0]: 2})
            return self._asi_reply(f"{get_ability(abilities[0]).cn} +2")

        # 注意：不能用 _split_choices —— 它把「+」当分隔符，「力量+2」会被拆成两项
        parts = [
            p for p in re.split(r"[\s,，、;；]+", raw.replace("＋", "+")) if p
        ]
        merged: list[str] = []
        for part in parts:
            if re.fullmatch(r"[+-]?\d+", part) and merged:
                merged[-1] += part
            else:
                merged.append(part)

        named: list[tuple[str, int]] = []
        for item in merged[:2]:
            match = re.fullmatch(r"(.+?)\s*([+-]\s*\d+)?", item)
            name = (match.group(1) if match else item).strip()
            digits = (match.group(2) or "").replace(" ", "") if match else ""
            key = _match_ability(name, [a.key for a in ABILITIES])
            if key is None:
                raise CreationError(f"认不出「{name}」这个属性。")
            if any(key == k for k, _ in named):
                raise CreationError(f"{get_ability(key).cn} 在这一轮里重复了。")
            named.append((key, int(digits) if digits else 0))
        if len(merged) > 2:
            raise CreationError("每次属性提升最多加两项属性。")
        if not named:
            raise CreationError("请回复「力量 +2」或「力量 +1 体质 +1」。")

        if len(named) == 1:
            gains = {named[0][0]: named[0][1] or 2}
        elif any(delta for _, delta in named):
            gains = {key: (delta or 1) for key, delta in named}
        else:
            gains = {key: 1 for key, _ in named}
        if sum(gains.values()) != 2:
            raise CreationError(
                "每次属性提升是「一项 +2」或「两项各 +1」，总加值必须是 +2。"
            )
        for key, delta in gains.items():
            if self.final_score(key) + delta > ABILITY_SCORE_MAX:
                raise CreationError(
                    f"{get_ability(key).cn} 再加会超过 {ABILITY_SCORE_MAX}，换一项属性吧。"
                )
        self._apply_asi(gains)
        label = "、".join(f"{get_ability(k).cn} {v:+d}" for k, v in gains.items())
        return self._asi_reply(label)

    def _apply_asi(self, gains: dict[str, int]) -> None:
        """把一次属性提升并进会话（累计，上限在建卡时按 20 收口）。"""
        for key, delta in gains.items():
            self.asi[key] = int(self.asi.get(key, 0)) + int(delta)
        self.asi_done += 1
        self.record("属性提升：" + "、".join(f"{k}{v:+d}" for k, v in gains.items()))

    def _asi_reply(self, label: str) -> str:
        """属性提升的回执（还有剩余次数就继续问）。"""
        self.advance()
        left = self.asi_pending()
        tail = f"（还剩 {left} 次属性提升）" if left else ""
        return (
            f"已应用属性提升：{label}{tail}。\n"
            f"当前属性：{self.final_scores_text()}\n\n"
            f"{creation_prompt(self.step, self)}"
        )

    def choose_background(self, text: str) -> str:
        """选择背景（可跳过）。"""
        self._require(CreationStep.BACKGROUND)
        raw = text.strip()
        if raw.lower() in _SKIP_WORDS:
            self.background = ""
            self.boost = {}
            self.record("背景：跳过")
            self.advance()
            return (
                "已跳过背景（没有背景就没有三项属性提升，属性会弱于规则角色）。"
                f"\n\n{creation_prompt(self.step, self)}"
            )
        name = _pick_one(raw, list(background_choices()), "背景")
        background = get_background(name)
        self.background = background.key
        # 换背景后旧的提升可能落在新背景的三项之外，统一清空重选
        self.boost = {}
        self.record(f"背景：{background.cn}")
        self.advance()
        feat_line = (
            f"\n它给的起源专长：{background.feat}" if background.feat else ""
        )
        return (
            f"已选择「{background.cn}」——{background.desc}\n"
            f"它给你的三项属性提升：{background.bonus_text}{feat_line}"
            f"\n\n{creation_prompt(self.step, self)}"
        )

    def choose_boost(self, text: str) -> str:
        """背景属性提升：一项 +2 另一项 +1，或三项各 +1（PHB 2024）。"""
        self._require(CreationStep.BOOST)
        options = self.boost_options()
        if not options:
            raise CreationError("当前背景没有可提升的属性，这一步可以跳过。")

        raw = text.strip()
        if raw.lower() in _SKIP_WORDS:
            self.boost = {}
            self.record("背景属性提升：跳过")
            self.advance()
            return (
                "已跳过属性提升（规则给的 +3 点没有用上，属性会弱于同级角色）。"
                f"\n\n{creation_prompt(self.step, self)}"
            )

        tokens = _split_choices(raw)
        if not tokens:
            raise CreationError("请回复「1 智力 感知」或「2」（三项各 +1）。")

        plan = 0
        if tokens[0].isdigit():
            plan = int(tokens[0])
            tokens = tokens[1:]
            if plan not in (1, 2):
                raise CreationError("只有两种方案：回复「1 属性 属性」或「2」。")

        picked: list[str] = []
        for token in tokens:
            if token.isdigit():
                continue
            key = _match_ability(token, options)
            if key is None:
                raise CreationError(
                    f"「{token}」不在本背景的三项里，只能选："
                    + "、".join(get_ability(k).cn for k in options)
                )
            if key in picked:
                raise CreationError(f"「{token}」重复了：+2 和 +1 要落在两项不同的属性上。")
            picked.append(key)

        # 方案 2 / 只说了「三项 / 平均」→ 三项各 +1
        average_words = ("三项", "各", "平均", "均分")
        if plan == 2 or (
            not picked and any(word in raw for word in average_words)
        ):
            boost = dict.fromkeys(options, 1)
            label = "三项各 +1"
        else:
            if len(picked) != 2:
                raise CreationError(
                    "方案 1 要指定两项属性：前一项 +2、后一项 +1，"
                    "例如「1 智力 感知」。三项各 +1 请回复「2」。"
                )
            boost = {picked[0]: 2, picked[1]: 1}
            label = (
                f"{get_ability(picked[0]).cn} +2、{get_ability(picked[1]).cn} +1"
            )

        for key, delta in boost.items():
            if int(self.base_scores.get(key, 10)) + delta > ABILITY_SCORE_MAX:
                raise CreationError(
                    f"{get_ability(key).cn} 再加 {delta} 会超过 {ABILITY_SCORE_MAX}，"
                    "换一项属性吧。"
                )

        self.boost = boost
        self.record(f"背景属性提升：{label}")
        self.advance()
        return (
            f"已应用背景属性提升：{label}。\n属性（含提升）：{self.final_scores_text()}"
            f"\n\n{creation_prompt(self.step, self)}"
        )

    def background_skill_names(self) -> list[str]:
        """背景提供的技能中文名。"""
        background = self.background_obj
        if not background:
            return []
        return [get_skill(key).cn for key in background.skills]

    def choose_skills(self, text: str) -> str:
        """选择技能。"""
        self._require(CreationStep.SKILL)
        profession = self.profession_obj
        if not profession:
            raise CreationError("需要先选择职业。")
        names = list(profession.skill_names())
        picked = parse_multi_choice(text, names, profession.skill_count)
        keys = [get_skill(n).key for n in picked]
        self.skills = list(dict.fromkeys(keys))
        self.record(f"技能：{'、'.join(picked)}")
        self.advance()

        overlap = [n for n in picked if n in self.background_skill_names()]
        note = (
            f"\n注意：{'、'.join(overlap)} 与背景重复，熟练不会叠加。"
            if overlap
            else ""
        )
        return f"已选择技能：{'、'.join(picked)}。{note}\n\n{creation_prompt(self.step, self)}"

    def choose_feature(self, text: str) -> str:
        """处理「1 级特性选择」这一步的当前问题。"""
        self._require(CreationStep.FEATURES)
        pending = self.pending_features()
        if not pending:
            raise CreationError("1 级特性已经选完了。")
        item = pending[0]
        raw = (text or "").strip()

        if item in _FEATURE_MENUS:
            title, options = self._feature_menu(item)
            option = _pick_option(raw, options, title)
            self.feature_choices[item] = option.key
            label = f"{title}：{option.cn}"
        elif item == "expertise":
            pool = self.expertise_pool()
            if len(pool) < 2:
                raise CreationError("已熟练的技能不足两项，无法选择专精。")
            picked = parse_multi_choice(raw, [cn for _, cn in pool], 2)
            self.expertise = [key for key, cn in pool if cn in picked]
            label = "专精：" + "、".join(picked)
        elif item == "weapon_mastery":
            pool = self.mastery_pool()
            count = self.mastery_count()
            picked = parse_multi_choice(raw, pool, count)
            self.weapon_masteries = picked
            label = f"武器精通（{count} 种）：" + "、".join(picked)
        elif item == "extra_cantrip":
            pool = self.extra_cantrip_pool()
            count = self.extra_cantrip_count()
            picked = parse_multi_choice(raw, [s.cn for s in pool], count)
            self.extra_cantrips = [
                get_spell(name).key for name in picked if get_spell(name)
            ]
            label = "额外戏法：" + "、".join(picked)
        elif item == "magic_initiate":
            cantrips, level, count = self.magic_initiate_grant()
            pool = self.magic_initiate_pool()
            picked = parse_multi_choice(raw, [s.cn for s in pool], cantrips + count)
            spells = [get_spell(name) for name in picked]
            spells = [s for s in spells if s is not None]
            got_cantrips = sum(1 for s in spells if s.level == 0)
            got_level = sum(1 for s in spells if s.level == level)
            if got_cantrips != cantrips or got_level != count:
                raise CreationError(
                    f"魔法学徒要选 {cantrips} 道戏法和 {count} 道{level}环法术"
                    f"（当前：戏法 {got_cantrips}、{level}环 {got_level}），请重新选择。"
                )
            self.feat_spells = [s.key for s in spells]
            label = "魔法学徒：" + "、".join(s.cn for s in spells)
        else:  # pragma: no cover - feature_plan 与这里必须同步
            raise CreationError(f"未知的特性选择「{item}」。")

        self.record(label)
        if self.pending_features():
            return f"已选 {label}。\n\n{creation_prompt(CreationStep.FEATURES, self)}"
        self.advance()
        return f"已选 {label}。\n\n{creation_prompt(self.step, self)}"

    def choose_equipment(self, text: str) -> str:
        """选择起始装备包。"""
        self._require(CreationStep.EQUIPMENT)
        profession = self.profession_obj
        if not profession:
            raise CreationError("需要先选择职业。")
        packages = profession.equipment_packages

        raw = text.strip()
        if raw.lower() in _SKIP_WORDS:
            self.equipment_package = ""
            self.equipment = list(
                dict.fromkeys(expand_packs(profession.fixed_equipment))
            )
            self.pending_weapon = ""
            self.record("装备：默认")
            self.advance()
            return f"已使用默认装备。\n\n{creation_prompt(self.step, self)}"

        tokens = _split_choices(raw)
        index = _to_index(tokens[0]) if tokens else None
        if index is not None:
            if index < 0 or index >= len(packages):
                raise CreationError(f"序号超出范围，可选 1~{len(packages)}。")
            package = packages[index]
        else:
            matches = [p for p in packages if p.key == raw or (raw and raw in p.label)]
            if not matches:
                raise CreationError("请输入装备包序号，或回复「跳过」。")
            package = matches[0]

        items = list(package.items) + list(profession.fixed_equipment)
        self.equipment_package = package.key
        # 先把「探索者背包 / 艺人包 / 学者包 …」展开成包 + 内容物
        self.equipment = list(dict.fromkeys(expand_packs(items)))
        self.record(f"装备包：{package.label}")
        # 包里若还有「任意军用武器」这类待定项，先停下来让玩家挑具体武器
        self.pending_weapon = next(
            (item for item in self.equipment if _is_generic_weapon(item)), ""
        )
        if self.pending_weapon:
            return f"已选择装备：{package.label}。\n\n{self.pending_weapon_prompt()}"
        self.advance()
        return f"已选择装备：{package.label}。\n\n{creation_prompt(self.step, self)}"

    def pending_weapon_prompt(self) -> str:
        """「任意军用武器」需要选具体武器时的提示（带编号列表）。"""
        options = weapon_options_for(self.pending_weapon)
        lines = [f"起始包里的「{self.pending_weapon}」需要选一件具体武器："]
        lines.extend(f"{i}. {name}" for i, name in enumerate(options, 1))
        lines.append("回复序号（或直接回复武器名）。")
        return "\n".join(lines)

    def choose_package_weapon(self, text: str) -> str:
        """把起始包里的「任意X武器」落成玩家选的具体武器。"""
        self._require(CreationStep.EQUIPMENT)
        placeholder = self.pending_weapon
        if not placeholder:
            raise CreationError("当前没有待定的武器。")
        options = weapon_options_for(placeholder)
        raw = text.strip()
        tokens = _split_choices(raw)
        index = _to_index(tokens[0]) if tokens else None
        if index is not None:
            if index < 0 or index >= len(options):
                raise CreationError(f"序号超出范围，可选 1~{len(options)}。")
            picked = options[index]
        else:
            exact = [name for name in options if name == raw]
            fuzzy = [name for name in options if raw and raw in name]
            if not (exact or fuzzy):
                raise CreationError("请回复武器序号或武器名。")
            picked = (exact or fuzzy)[0]
        if placeholder in self.equipment:
            self.equipment[self.equipment.index(placeholder)] = picked
        self.record(f"武器：{placeholder} → {picked}")
        # 可能还有第二个待定项（如「两把任意军用武器」）
        self.pending_weapon = next(
            (item for item in self.equipment if _is_generic_weapon(item)), ""
        )
        if self.pending_weapon:
            return f"已选 {picked}。\n\n{self.pending_weapon_prompt()}"
        self.advance()
        return f"已选 {picked}。\n\n{creation_prompt(self.step, self)}"

    def choose_spells(self, text: str) -> str:
        """选择法术（戏法 + 一环法术）。"""
        self._require(CreationStep.SPELLS)
        profession = self.profession_obj
        if not profession:
            raise CreationError("需要先选择职业。")
        cantrips, known = self.spell_picks()
        if cantrips <= 0 and known <= 0:
            raise CreationError(f"{profession.cn}没有施法能力，不用选法术。")

        options = self.spell_options()
        if not options:
            raise CreationError(f"{profession.cn}在 0~3 环里没有可选法术。")

        raw = text.strip()
        if raw.lower() in _AUTO_WORDS or raw.lower() in _SKIP_WORDS:
            picked = [s for s in options if s.level == 0][:cantrips] + [
                s for s in options if s.level >= 1
            ][:known]
        else:
            names = parse_multi_choice(raw, [s.cn for s in options], cantrips + known)
            picked = [spell for spell in (get_spell(name) for name in names) if spell]

        chosen_cantrips = [s for s in picked if s.level == 0]
        chosen_spells = [s for s in picked if s.level >= 1]
        if len(chosen_cantrips) != cantrips or len(chosen_spells) != known:
            raise CreationError(
                f"需要选 戏法 {cantrips} 个 + 一环法术 {known} 个，"
                f"你现在选了 戏法 {len(chosen_cantrips)} 个 + 法术 {len(chosen_spells)} 个。"
                "（若要改动，重新把全部序号发一次即可）"
            )

        self.spells = [s.key for s in picked]
        label = "、".join(s.cn for s in picked)
        self.record(f"法术：{label}")
        self.advance()
        return f"已选择法术：{label}。\n\n{creation_prompt(self.step, self)}"

    def choose_alignment(self, text: str) -> str:
        """选择阵营（纯扮演标记，允许跳过）。"""
        self._require(CreationStep.ALIGNMENT)
        raw = text.strip()
        if raw.lower() in _SKIP_WORDS:
            self.alignment = ""
            self.record("阵营：跳过")
            self.advance()
            return (
                "已跳过阵营（随时可以用「/跑团.改卡 阵营」补上）。"
                f"\n\n{creation_prompt(self.step, self)}"
            )
        self.alignment = normalize_alignment(raw)
        self.record(f"阵营：{self.alignment}")
        self.advance()
        return f"阵营：{self.alignment}。\n\n{creation_prompt(self.step, self)}"

    # —— 收尾 ——

    def build(self) -> DndCharacter:
        """按当前状态生成角色卡。"""
        return build_character(self)

    def summary(self) -> str:
        """建卡完成的摘要。"""
        return self.build().summary()


def _align_score_rolls(session: CreationSession) -> dict[str, list[int]]:
    """把掷出的每组点数对应回具体属性，用于展示「这一项是哪几个骰子」。"""
    remaining = [list(g) for g in session.ability_groups]
    used = [False] * len(remaining)
    out: dict[str, list[int]] = {}
    for ability in ABILITIES:
        value = session.base_scores.get(ability.key)
        if value is None:
            continue
        for i, group in enumerate(remaining):
            if used[i]:
                continue
            if sum(sorted(group, reverse=True)[:3]) == value:
                used[i] = True
                out[ability.key] = group
                break
    return out


def session_from_card(card: DndCharacter) -> CreationSession:
    """把已保存的角色卡还原成建卡会话（「改卡」与**升级选择会话**共用）。

    生成角色卡时，背景提供的技能 / 装备已经合并进卡里；这里再拆出来，
    这样重做背景时不会残留旧背景的内容（``build_character`` 会重新合并）。

    ``asi_done`` 按**卡上加点总量 ÷ 2** 反推（每次分配恰好 +2 点）：跳过过的
    ASI 会少算，正好让它重新被问到——该补的加点不该因为跳过而永久丢失。
    """
    session = CreationSession(user_id=card.user_id)
    session.system = "DND 5e"
    session.name = card.name
    session.base_scores = dict(card.base_scores)
    session.boost = dict(card.ability_boost)
    session.asi = dict(card.asi)
    session.level = card.level
    session.ability_pool = sorted(card.base_scores.values(), reverse=True)
    session.race = card.race
    session.subrace = card.subrace
    session.profession = card.profession
    session.subclass = card.subclass
    session.background = card.background
    session.feature_choices = dict(card.feature_choices)
    session.expertise = list(card.expertise)
    session.weapon_masteries = list(card.weapon_masteries)
    # 语言：卡上已含通用语与种族给的，整体搬回会话（build_character 会去重重合并）
    session.languages = list(card.languages)

    background = card.background_obj
    bg_skills = set(background.skills) if background else set()
    bg_items = (
        {str(parse_item_stack(item)) for item in background.equipment}
        if background
        else set()
    )
    session.skills = [k for k in card.skill_proficiencies if k not in bg_skills]
    session.equipment = [i for i in card.equipment if i not in bg_items]
    session.equipment_package = card.equipment_package
    session.spells = list(card.spells)
    # 额外戏法 / 魔法学徒的法术在建卡时已并入卡上的 spells，卡上没有独立字段；
    # 还原成「已完成」——否则升级 / 改卡会话经过「特性」步会把它们**再问一遍**，
    # 重复的法术并进卡里。反正 build_character 是并集去重，填卡上已有键无副作用。
    session.extra_cantrips = list(card.spells)
    session.feat_spells = list(card.spells)
    session.alignment = card.alignment
    session.asi_done = round(sum(abs(v) for v in session.asi.values()) / 2)
    return session


def build_character(session: CreationSession) -> DndCharacter:
    """把建卡会话转成角色卡对象（合并背景技能与物品）。"""
    if not session.race or not session.profession:
        raise CreationError("种族与职业尚未确定，无法生成角色卡。")
    if not session.base_scores:
        raise CreationError("属性尚未分配，无法生成角色卡。")

    skills = list(session.skills)
    equipment = list(session.equipment)
    # 背景自带的钱袋（如「15 金币」）拆出来放进钱包，不混在装备列表里。
    # ⚠️ 这不是「把职业装备包换成金币」——职业装备包仍必须二选一，不能折钱。
    money: dict[str, int] = {}

    background = session.background_obj
    if background:
        skills.extend(background.skills)
        for item in background.equipment:
            coin = parse_coin_text(item)
            if coin:
                money = merge_coins(money, coin)
            else:
                equipment.append(item)

    profession = session.profession_obj
    if not equipment and profession:
        equipment = list(profession.fixed_equipment)
    # 兜底路径（没选包 / 旧会话）也要把容器包展开
    equipment = list(dict.fromkeys(expand_packs(equipment)))
    if any(_is_generic_weapon(item) for item in equipment):
        raise CreationError("起始包里的「任意武器」还没选定，请先选具体武器。")

    normalized = [str(parse_item_stack(item)) for item in equipment]

    card = DndCharacter.create(
        session.user_id,
        # 没填名字就用玩家在 lk 插件里绑定的名称（跑团里显示的就是这个名字），
        # 连 lk 都没绑定时退回「冒险者 + QQ 尾号」。
        name=session.name
        or lk_name(session.user_id)
        or f"冒险者{str(session.user_id)[-4:]}",
        race=session.race,
        subrace=session.subrace or None,
        profession=session.profession,
        subclass=session.subclass or None,
        level=session.level,
        background=session.background or None,
        base_scores=session.base_scores,
        ability_boost=dict(session.boost),
        asi=dict(session.asi),
        feature_choices=dict(session.feature_choices),
        expertise=list(session.expertise),
        weapon_masteries=list(session.weapon_masteries),
        score_method="4d6" if session.ability_groups else "standard",
        score_rolls=_align_score_rolls(session),
        skills=list(dict.fromkeys(skills)),
        equipment=list(dict.fromkeys(normalized)),
        equipment_package=session.equipment_package,
        # 法术：职业给的 + 原初职能额外的 + 背景起源专长（魔法学徒）给的
        spells=list(
            dict.fromkeys(
                [*session.spells, *session.extra_cantrips, *session.feat_spells]
            )
        ),
        languages=list(dict.fromkeys([COMMON, *session.languages])),
        alignment=session.alignment,
        money=money,
    )
    # 高等级开卡的起始经验：落在「刚好达到该等级」的累计经验上，
    # 否则卡上写着 Lv.5 却只有 0 XP，下一次升级提示会立刻自相矛盾。
    card.exp = LEVEL_XP_THRESHOLDS[max(0, min(card.level, MAX_LEVEL) - 1)]
    return card
