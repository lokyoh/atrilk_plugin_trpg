"""建卡流程。

对应 ``.agents/new_plusin_lktrpg.md`` 的「建卡」一节：

.. code-block:: text

    选择模式 → roll 属性 → 种族 → 职业 → 技能 → 装备包 → 选法术 → 开启故事

「选法术」只对施法职业出现（野蛮人 / 战士 / 武僧 / 游荡者会自动跳过；
圣武士 / 游侠 1 级没有法术位，也会跳过）。

流程以 :class:`CreationSession` 承载状态，每一步都由 :func:`creation_prompt`
给出指引、由 :meth:`CreationSession.handle` 解析玩家回复。会话对象可序列化成
``dict`` 直接存进 matcher 的 ``state``，因此不需要额外的会话存储。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..trpg.dice import roll_4d6_drop_lowest
from ..trpg.text import bullet
from .attributes import (
    ABILITIES,
    ABILITY_SCORE_MAX,
    ABILITY_SCORE_MIN,
    STANDARD_ARRAY,
    get_ability,
)
from .background import Background, background_choices, get_background
from .card import DndCharacter
from .equipment import parse_item_stack
from .names import lk_name
from .profession import DndClass, class_choices, get_class
from .race import Race, get_race, race_choices
from .skills import get_skill
from .spells import Spell, get_spell, spells_for_class

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
]


class CreationError(Exception):
    """玩家输入无法解析时抛出，由 handler 捕获后重新提示。"""


class CreationStep(Enum):
    """建卡步骤。"""

    MODE = "选择模式"
    ABILITY = "roll属性"
    RACE = "选择种族"
    PROFESSION = "选择职业"
    BACKGROUND = "选择背景"
    SKILL = "选择技能"
    EQUIPMENT = "选择装备包"
    SPELLS = "选择法术"
    DONE = "完成"


#: 建卡步骤顺序（与需求文档一致：属性 → 种族 → 职业 → 技能 → 装备包 → 法术）
CREATION_STEPS: tuple[CreationStep, ...] = (
    CreationStep.MODE,
    CreationStep.ABILITY,
    CreationStep.RACE,
    CreationStep.PROFESSION,
    CreationStep.BACKGROUND,
    CreationStep.SKILL,
    CreationStep.EQUIPMENT,
    CreationStep.SPELLS,
    CreationStep.DONE,
)

#: 兼容命名
STEPS = CREATION_STEPS

#: 需要从选项列表里挑一个的步骤
CHOICE_STEPS: frozenset[CreationStep] = frozenset(
    {
        CreationStep.MODE,
        CreationStep.RACE,
        CreationStep.PROFESSION,
        CreationStep.BACKGROUND,
        CreationStep.SKILL,
        CreationStep.EQUIPMENT,
        CreationStep.SPELLS,
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
            bullet(f"{i}. {name}" for i, name in enumerate(race_choices(), 1)),
        ]
        if session and session.base_scores:
            lines.append("")
            lines.append("当前属性：" + session.assigned_scores_text())
        lines.append("回复序号或种族名；有亚种的种族可用「精灵 高等精灵」指定亚种。")
        return "\n".join(lines)

    if step is CreationStep.PROFESSION:
        return "\n".join(
            [
                "选择职业：",
                bullet(f"{i}. {name}" for i, name in enumerate(class_choices(), 1)),
                "回复序号或职业名。",
            ]
        )

    if step is CreationStep.BACKGROUND:
        return "\n".join(
            [
                "选择背景（可回复「跳过」）：",
                bullet(
                    f"{i}. {name}" for i, name in enumerate(background_choices(), 1)
                ),
                "背景会额外提供技能熟练与起始物品，与职业技能不重复。",
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

    if step is CreationStep.EQUIPMENT:
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
        lines = [
            f"选择法术（{profession.cn}：戏法 {cantrips} 个 + 一环法术 {known} 个，"
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

    return "建卡完成！"


@dataclass
class CreationSession:
    """一次建卡会话的状态。"""

    user_id: str
    step: CreationStep = CreationStep.MODE
    system: str = ""
    name: str = ""
    #: 掷出的候选属性值（降序）
    ability_pool: list[int] = field(default_factory=list)
    #: 每组属性的原始 4d6 点数，按掷出顺序保存，用于回放展示
    ability_groups: list[list[int]] = field(default_factory=list)
    #: roll 时掷出的候选属性组（每组 6 个值，按属性展示顺序），等玩家挑一组
    ability_candidates: list[list[int]] = field(default_factory=list)
    #: 候选属性组对应的 4d6 原始点数，与 ``ability_candidates`` 一一对应
    ability_set_groups: list[list[list[int]]] = field(default_factory=list)
    #: 已分配属性：属性键 -> 值
    base_scores: dict[str, int] = field(default_factory=dict)
    race: str = ""
    subrace: str = ""
    profession: str = ""
    background: str = ""
    skills: list[str] = field(default_factory=list)
    equipment_package: str = ""
    equipment: list[str] = field(default_factory=list)
    #: 选好的法术（法术 key，含戏法）
    spells: list[str] = field(default_factory=list)
    #: 交互日志（记录玩家每一步的选择，便于回放）
    log: list[str] = field(default_factory=list)

    # —— 序列化（存进 matcher.state） ——

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "step": self.step.name,
            "system": self.system,
            "name": self.name,
            "ability_pool": list(self.ability_pool),
            "ability_groups": [list(g) for g in self.ability_groups],
            "ability_candidates": [list(s) for s in self.ability_candidates],
            "ability_set_groups": [
                [list(g) for g in groups] for groups in self.ability_set_groups
            ],
            "base_scores": dict(self.base_scores),
            "race": self.race,
            "subrace": self.subrace,
            "profession": self.profession,
            "background": self.background,
            "skills": list(self.skills),
            "equipment_package": self.equipment_package,
            "equipment": list(self.equipment),
            "spells": list(self.spells),
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
            race=data.get("race", ""),
            subrace=data.get("subrace", ""),
            profession=data.get("profession", ""),
            background=data.get("background", ""),
            skills=list(data.get("skills", [])),
            equipment_package=data.get("equipment_package", ""),
            equipment=list(data.get("equipment", [])),
            spells=list(data.get("spells", [])),
            log=list(data.get("log", [])),
        )

    # —— 规则对象 ——

    @property
    def race_obj(self) -> Race | None:
        return get_race(self.race) if self.race else None

    @property
    def profession_obj(self) -> DndClass | None:
        return get_class(self.profession) if self.profession else None

    @property
    def background_obj(self) -> Background | None:
        return get_background(self.background) if self.background else None

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

    # —— 流程推进 ——

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
        """进入下一步（施法职业才会经过「选择法术」）。"""
        index = CREATION_STEPS.index(self.step)
        while True:
            index = min(index + 1, len(CREATION_STEPS) - 1)
            self.step = CREATION_STEPS[index]
            if self.step is CreationStep.SPELLS and self.spell_picks() == (0, 0):
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
        if step is CreationStep.PROFESSION:
            return self.choose_profession(raw)
        if step is CreationStep.BACKGROUND:
            return self.choose_background(raw)
        if step is CreationStep.SKILL:
            return self.choose_skills(raw)
        if step is CreationStep.EQUIPMENT:
            return self.choose_equipment(raw)
        if step is CreationStep.SPELLS:
            return self.choose_spells(raw)
        return "建卡已经完成了，回复「看卡」可以查看角色卡。"

    # —— 各步骤的输入处理 ——

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
        """选择种族，可同时指定亚种（``精灵 高等精灵``）。"""
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
        detail = f"{race.format_bonus(sub_obj)}，速度 {race.speed} 尺，{race.size}"
        return f"已选择「{label}」（{detail}）。\n\n{creation_prompt(self.step, self)}"

    def choose_profession(self, text: str) -> str:
        """选择职业。"""
        self._require(CreationStep.PROFESSION)
        name = _pick_one(text, list(class_choices()), "职业")
        profession = get_class(name)
        self.profession = profession.key
        self.record(f"职业：{profession.cn}")
        self.advance()
        saves = "、".join(get_ability(k).cn for k in profession.saves)
        return (
            f"已选择「{profession.cn}」（生命骰 {profession.hit_dice}，豁免：{saves}）。"
            f"\n\n{creation_prompt(self.step, self)}"
        )

    def choose_background(self, text: str) -> str:
        """选择背景（可跳过）。"""
        self._require(CreationStep.BACKGROUND)
        raw = text.strip()
        if raw.lower() in _SKIP_WORDS:
            self.background = ""
            self.record("背景：跳过")
            self.advance()
            return f"已跳过背景。\n\n{creation_prompt(self.step, self)}"
        name = _pick_one(raw, list(background_choices()), "背景")
        background = get_background(name)
        self.background = background.key
        self.record(f"背景：{background.cn}")
        self.advance()
        return f"已选择「{background.cn}」（{background.desc}）\n\n{creation_prompt(self.step, self)}"

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
            self.equipment = list(profession.fixed_equipment)
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
        self.equipment = list(dict.fromkeys(items))
        self.record(f"装备包：{package.label}")
        self.advance()
        return f"已选择装备：{package.label}。\n\n{creation_prompt(self.step, self)}"

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


def build_character(session: CreationSession) -> DndCharacter:
    """把建卡会话转成角色卡对象（合并背景技能与物品）。"""
    if not session.race or not session.profession:
        raise CreationError("种族与职业尚未确定，无法生成角色卡。")
    if not session.base_scores:
        raise CreationError("属性尚未分配，无法生成角色卡。")

    skills = list(session.skills)
    equipment = list(session.equipment)

    background = session.background_obj
    if background:
        skills.extend(background.skills)
        equipment.extend(background.equipment)

    profession = session.profession_obj
    if not equipment and profession:
        equipment = list(profession.fixed_equipment)

    normalized = [str(parse_item_stack(item)) for item in equipment]

    return DndCharacter.create(
        session.user_id,
        # 没填名字就用玩家在 lk 插件里绑定的名称（跑团里显示的就是这个名字），
        # 连 lk 都没绑定时退回「冒险者 + QQ 尾号」。
        name=session.name
        or lk_name(session.user_id)
        or f"冒险者{str(session.user_id)[-4:]}",
        race=session.race,
        subrace=session.subrace or None,
        profession=session.profession,
        background=session.background or None,
        base_scores=session.base_scores,
        score_method="4d6" if session.ability_groups else "standard",
        score_rolls=_align_score_rolls(session),
        skills=list(dict.fromkeys(skills)),
        equipment=list(dict.fromkeys(normalized)),
        equipment_package=session.equipment_package,
        spells=list(session.spells),
    )
