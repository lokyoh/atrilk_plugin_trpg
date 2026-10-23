"""DM（:class:`~...dnd.dm.DungeonMasterAgent`）可调用的 DND 功能。

功能自己从存储 / 静态数据里取数据：

- 角色卡来自 :mod:`...dnd.storage`（落盘位置由框架指定）；
- 种族 / 职业 / 技能 / 装备来自 :mod:`...dnd` 的静态数据表；
- 规则来自 :mod:`.rules`（插件内置的 ``res/rule.md``）。

因此功能只需要 :class:`~..thinking.AgentContext` 里的 ``user_id`` 就能定位数据，
场景 / 玩家数据仍以提示词形式内联给模型。
"""

from __future__ import annotations

import re
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ...dnd.attributes import ABILITIES, get_ability
from ...dnd.battlemap import (
    distance_feet,
    has_map,
    range_check,
)
from ...dnd.bestiary import bestiary_text, find_bestiary
from ...dnd.card import DndCharacter
from ...dnd.combat import (
    apply_damage as combat_damage,
)
from ...dnd.combat import (
    apply_condition,
    apply_threat_profile,
    auto_crit_on_hit,
    clear_combat,
    combat_text,
    combatant_conditions,
    effective_ac,
    find_combatant,
    heal_combatant,
    load_combat,
    move_combatant,
    parse_enemy_list,
    party_level,
    resource_of,
    roll_damage,
    save_combat,
    start_combat,
    use_resource,
)
from ...dnd.combat import (
    attack as combat_attack,
)
from ...dnd.conditions import (
    CONDITION_MAP,
    CONDITIONS,
    attack_advantage,
    combine_advantage,
    condition_text,
    merge_effects,
    normalize_conditions,
    save_auto_fail,
)
from ...dnd.difficulty import (
    DC_BANDS,
    DEFAULT_BASE_DC,
    SKILL_BASE_DC,
    dc_for,
    describe_dc,
)
from ...dnd.encounter import calibrate_specs
from ...dnd.equipment import ARMORS, SHIELDS, WEAPONS, get_armor, get_weapon
from ...dnd.evolution import note_scene_change
from ...dnd.items import ITEMS, format_item_list, get_item
from ...dnd.memory import (
    MEMORY_MAX,
    add_memory,
    current_round,
    list_memories,
    recall_memories,
    remember_from_tool,
    set_resolved,
)
from ...dnd.monster_index import index_text
from ...dnd.monster_rules import rules_of, save_advantage
from ...dnd.monsters import MONSTERS, get_monster, monster_names
from ...dnd.npcs import (
    NPC_KEY_PREFIX,
    NPC_TIERS,
    NpcRecord,
    build_npc,
    find_npc,
    get_tier,
    npc_monster,
    npc_xp,
    npcs_text,
    resolve_monster,
    tier_names,
    update_npc,
    upsert_npc,
)
from ...dnd.profession import CLASSES, get_class
from ...dnd.progression import (
    ASI_LEVELS,
    LEVEL_XP_THRESHOLDS,
    MAX_LEVEL,
    can_level_up,
    level_up,
    xp_progress_text,
)
from ...dnd.race import RACES, get_race
from ...dnd.save_store import (
    append_scene_change,
    find_character,
    get_character,
    list_scenes,
    load_round,
    load_save,
    read_scene,
    save_round,
    set_character,
)
from ...dnd.settlement import settle_combat
from ...dnd.skills import SKILLS, get_skill
from ...dnd.spells import (
    SPELLS,
    format_spell_lines,
    get_spell,
    spells_for_class,
    spells_of_level,
)
from ...dnd.storage import load_card, save_card
from ...dnd.world_store import (
    DOC_KIND_LABELS,
    DOC_KINDS,
    list_docs,
    list_events,
    load_doc,
    read_doc,
)
from ...trpg.check import roll_check
from ...trpg.dice import Advantage, DiceError, roll
from ..thinking import AgentContext, AgentFinishError, AgentTool, ToolArg
from .rules import match_rules

__all__ = [
    "DM_TOOLS",
    "AttackTool",
    "AwaitPlayersTool",
    "CastSpellTool",
    "CheckTool",
    "EndCombatTool",
    "GetRuleTool",
    "MoveSceneTool",
    "MoveTool",
    "NarrateTool",
    "NoteEventTool",
    "QueryDataTool",
    "ReadSceneTool",
    "RecallTool",
    "RememberTool",
    "RestTool",
    "RollDiceTool",
    "SecretRollTool",
    "StartCombatTool",
    "UpdateCharacterTool",
    "UpdateNpcTool",
    "UpdateSceneTool",
]


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #


def _parse_advantage(value: Any) -> Advantage:
    text = str(value or "").strip()
    if text in {"优势", "advantage", "adv"}:
        return Advantage.ADVANTAGE
    if text in {"劣势", "disadvantage", "dis"}:
        return Advantage.DISADVANTAGE
    return Advantage.NORMAL


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _load_character(
    context: AgentContext, player: str = ""
) -> tuple[DndCharacter | None, Any]:
    """取角色卡：优先用存档里的副本，没有存档时回退到源卡。

    :param player: 角色名或 user_id；留空则用 ``context.user_id``。一轮里多名玩家
        同时行动时，功能要用它指定「为哪名玩家」结算。
    :return: ``(角色卡, 存档)``；角色卡不存在时为 ``None``，回退源卡时存档为 ``None``。
    """
    target = str(player or "").strip()
    if context.save_id:
        save = load_save(context.save_id)
        if save is not None:
            if target:
                return find_character(save, target), save
            return get_character(save, context.user_id), save
    if target and target != str(context.user_id):
        return load_card(target), None
    return load_card(context.user_id), None


#: 清空状态时允许的写法
_CONDITION_CLEAR_WORDS = {"", "无", "无状态", "清空", "none", "clear"}


def _split_conditions(value: str) -> list[str]:
    """把「中毒、倒地」这类写法拆成状态列表（保留原写法，不做归一）。"""
    return [part.strip() for part in re.split(r"[、,，;；/]+", value) if part.strip()]


# --------------------------------------------------------------------------- #
# 掷骰 / 检定
# --------------------------------------------------------------------------- #


class RollDiceTool(AgentTool):
    """按表达式掷骰。"""

    function_name = "roll_dice"
    description = "按骰子表达式掷骰，返回点数明细与总和"
    args = (ToolArg("expr", "str", "骰子表达式，如 1d20、2d6+3、4d6"),)

    @staticmethod
    async def call(context: AgentContext, expr: str = "", **kwargs: Any) -> str:
        try:
            result = roll(expr, label="掷骰")
        except DiceError as e:
            return f"掷骰失败：{e}"
        return result.describe()


class SecretRollTool(AgentTool):
    """暗骰：结果只给 DM 看，用来决定 NPC 的行动与隐藏判定。"""

    function_name = "secret_roll"
    description = (
        "暗骰：结果只有你看得到。决定 NPC 行动与检定、隐藏事态、"
        "玩家是否被察觉，再据此叙述（别念骰点）"
    )
    args = (
        ToolArg("expr", "str", "骰子表达式，如 1d20+3"),
        ToolArg("reason", "str", "在判断什么，如「卫兵是否注意到翻墙的玩家」"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        expr: str = "1d20",
        reason: str = "",
        **kwargs: Any,
    ) -> str:
        try:
            result = roll(expr)
        except DiceError as e:
            return f"暗骰失败：{e}"
        why = str(reason or "").strip() or "未说明用途"
        context.data.setdefault("secret_log", []).append(
            {"expr": expr, "total": result.total, "reason": why}
        )
        return (
            f"【暗骰·玩家不可见】{result.describe()} —— 用于：{why}\n"
            "请据此决定 NPC / 隐藏事态的结果，叙述里不要出现骰点与「暗骰」字样。"
        )


def _resolve_check(card, kind: str, dc: int, advantage: Advantage, action: str = ""):
    """把检定对象解析成技能 / 属性 / 豁免并执行。

    技能名走 :func:`~..dnd.skills.get_skill`（支持「生存 / 求生」这类同义异写）；
    属性名支持中文 / 内部键 / 英文 / 缩写。认不出一律抛
    :class:`KeyError`，由调用方转成给 DM 看的提示。

    ``dc <= 0`` 表示 DM 留空：按 :func:`~..dnd.difficulty.dc_for` 以技能基准难度
    自动定档，而不是一律 15。``action`` 是玩家这一轮的行动原文——难度词
    （「悄悄」「拼尽全力」「翻越城墙」）要从里面认，跟规则判断器那条路径保持一致。
    """
    text = str(kind or "").strip()
    if not text:
        raise KeyError("（未提供检定对象）")
    is_save = "豁免" in text
    name = text.replace("豁免", "").strip()

    for ability in ABILITIES:
        if name in (ability.cn, ability.key, ability.abbr.lower(), ability.en.lower()):
            value = dc if dc > 0 else DEFAULT_BASE_DC
            if is_save:
                return card.check_save(ability, value, advantage=advantage)
            return card.check_ability(ability, value, advantage=advantage)
    if not is_save:
        # 技能名（含别名，如「生存」= survival、「游说」= persuasion）
        skill = get_skill(name)
        value = dc if dc > 0 else dc_for(action or text, skill.key)
        return card.check_skill(skill, value, advantage=advantage)
    raise KeyError(text)


class CheckTool(AgentTool):
    """用玩家角色卡做检定。"""

    function_name = "check"
    description = "用角色卡做技能 / 属性 / 豁免检定"
    args = (
        ToolArg(
            "kind",
            "str",
            "检定对象：技能名（如 察觉、隐匿、游说）或属性名（如 力量），"
            "豁免写作「力量豁免」",
        ),
        ToolArg("dc", "int", "难度值，留空由系统按技能基准 + 难度词自动定档"),
        ToolArg("advantage", "str", "普通 / 优势 / 劣势，默认普通"),
        ToolArg(
            "player",
            "str",
            "为哪名玩家检定：角色名或 user_id；只有一名玩家时可留空",
        ),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        kind: str = "",
        dc: Any = 0,
        advantage: Any = "普通",
        player: str = "",
        **kwargs: Any,
    ) -> str:
        card, _save = _load_character(context, player)
        if card is None:
            return "没找到该玩家的角色卡，无法检定"
        try:
            result = _resolve_check(
                card,
                kind,
                _parse_int(dc, 0),
                _parse_advantage(advantage),
                str(context.data.get("action_text", "") or ""),
            )
        except KeyError as e:
            # 把可用清单一起回给 DM：模型下一次调用就能自己纠对写法
            return (
                f"无法识别检定对象：{e}\n"
                "可用的技能："
                + "、".join(s.cn for s in SKILLS)
                + "\n可用的属性（写作「力量豁免」即为豁免检定）："
                + "、".join(a.cn for a in ABILITIES)
            )
        return f"【{card.name}】{result.describe()}"


# --------------------------------------------------------------------------- #
# 规则 / 数据查询
# --------------------------------------------------------------------------- #


class GetRuleTool(AgentTool):
    """查询规则条目。"""

    function_name = "get_rule"
    max_result_chars = 2500
    description = "查询 DND 5e 规则条目，输入主题关键词返回相关规则原文"
    args = (
        ToolArg(
            "topic",
            "str",
            "规则主题关键词，如 优势、死亡豁免、法术位、暴击、状态、专注",
        ),
    )

    @staticmethod
    async def call(context: AgentContext, topic: str = "", **kwargs: Any) -> str:
        return match_rules(topic)


def _query_race(name: str) -> str:
    if not name:
        return "种族：" + "、".join(r.cn for r in RACES)
    race = get_race(name)
    lines = [f"{race.cn}（{race.en}）", f"属性加值：{race.format_bonus()}"]
    lines.append(f"速度：{race.speed} 尺  体型：{race.size}")
    if race.traits:
        lines.append("特性：" + "；".join(race.traits))
    if race.languages:
        lines.append("语言：" + "、".join(race.languages))
    for sub in race.subraces:
        delta = "、".join(
            f"{get_ability(k).cn}{v:+d}" for k, v in sub.bonus.items() if v
        )
        detail = f"  亚种 {sub.cn}：属性加值 {delta or '无'}"
        if sub.traits:
            detail += "；" + "；".join(sub.traits)
        lines.append(detail)
    if race.desc:
        lines.append(race.desc)
    return "\n".join(lines)


def _query_profession(name: str) -> str:
    if not name:
        return "职业：" + "、".join(c.cn for c in CLASSES)
    cls = get_class(name)
    lines = [f"{cls.cn}（{cls.en}）"]
    lines.append(
        f"生命骰：1d{cls.hit_die}  主要属性："
        + "、".join(get_ability(k).cn for k in cls.primary)
    )
    lines.append("豁免熟练：" + "、".join(get_ability(k).cn for k in cls.saves))
    lines.append(f"可选技能（{cls.skill_count} 项）：" + "、".join(cls.skill_names()))
    if cls.features:
        lines.append("1 级特性：" + "；".join(cls.features))
    if cls.desc:
        lines.append(cls.desc)
    return "\n".join(lines)


def _query_skill(name: str) -> str:
    if not name:
        return "技能：" + "、".join(s.cn for s in SKILLS)
    skill = get_skill(name)
    return f"{skill.cn}（{skill.en}）：关联属性 {skill.ability_obj.cn}"


def _query_item(name: str) -> str:
    """查魔法物品：留空按大类列全部，给名字出完整描述。"""
    if not name:
        return (
            f"魔法物品（共 {len(ITEMS)} 件）：\n"
            + format_item_list(ITEMS)
            + "\n\n按名字查详情，比如「治疗药水」「+1 武器」。"
        )
    item = get_item(name)
    if item is None:
        raise KeyError(f"未知物品：{name}")
    return item.describe()


def _query_weapon(name: str) -> str:
    if not name:
        return "武器：" + "、".join(w.cn for w in WEAPONS)
    weapon = get_weapon(name)
    if weapon is None:
        raise KeyError(f"未知武器：{name}")
    lines = [f"{weapon.cn}（{weapon.category.value}）"]
    lines.append(f"伤害：{weapon.damage} {weapon.damage_type}")
    if weapon.is_versatile:
        lines.append(f"两用：双手 {weapon.two_handed_damage}")
    if weapon.properties:
        lines.append("属性：" + "、".join(weapon.properties))
    if weapon.is_ranged:
        lines.append(f"射程：{weapon.ranged_normal}/{weapon.ranged_max} 尺")
    if weapon.is_thrown:
        lines.append(f"投掷射程：{weapon.thrown_normal}/{weapon.thrown_max} 尺")
    if weapon.is_ammunition:
        lines.append("弹药：" + " / ".join(weapon.ammo_names) + "（每次攻击耗一枚）")
    return "\n".join(lines)


def _query_armor(name: str) -> str:
    if not name:
        return "护甲：" + "、".join(a.cn for a in (*ARMORS, *SHIELDS))
    armor = get_armor(name)
    if armor is None:
        raise KeyError(f"未知护甲：{name}")
    lines = [f"{armor.cn}（{armor.category.value}）", f"基础 AC：{armor.base_ac}"]
    if armor.max_dex_bonus is not None:
        lines.append(f"敏捷加值上限：{armor.max_dex_bonus}")
    if armor.strength_required:
        lines.append(f"力量要求：{armor.strength_required}")
    if armor.stealth_disadvantage:
        lines.append("潜行：劣势")
    return "\n".join(lines)


def _looks_like_category(text: str) -> bool:
    """这句话里有图鉴的分类词吗（类型 / 栖息地 / 群组）？有就走分类查询。"""
    from ...dnd.monster_index import GROUP_INDEX, HABITAT_INDEX, KIND_INDEX

    return any(
        token in KIND_INDEX or token in HABITAT_INDEX or token in GROUP_INDEX
        for token in text.split()
    )


def _query_monster(name: str) -> str:
    """查怪物。

    - 留空：按《怪物图鉴》的四套分类给总览（类型 / 栖息地 / 群组 / 挑战等级）；
    - **分类**（``森林`` ``亡灵`` ``类地精``，可组合）：按 CR 分档列名单；
    - ``随机 …``：系统掷一只，直接出数据卡；
    - ``cr 5``：列该挑战等级（带 AC / HP）；
    - 写名字：出数据卡。

    怪物表有五百多只（图鉴全量），全列出来会超出返回上限也读不过来——
    所以挑怪走分类，查某只走名字。
    """
    text = str(name or "").strip()
    if not text:
        return index_text()

    # 随机 / 分类查询（图鉴的四套分类）交给 monster_index：那里认得
    # 类型、栖息地、群组与 cr；认不出会抛 KeyError，正好是「查不到」的提示。
    if text.startswith("随机") or _looks_like_category(text):
        return index_text(text)

    lowered = text.lower()
    if lowered.startswith("cr") or text.replace("/", "").isdigit():
        raw = lowered.replace("cr", "").strip()
        try:
            cr = (
                float(raw.split("/")[0]) / float(raw.split("/")[1])
                if "/" in raw
                else float(raw)
            )
        except (ValueError, ZeroDivisionError):
            raise KeyError(f"看不懂这个挑战等级：{text}") from None
        hits = sorted((m for m in MONSTERS if abs(m.cr - cr) < 1e-6), key=lambda m: m.cn)
        if not hits:
            return f"CR {raw} 没有登记。"
        lines = [f"CR {raw} 共 {len(hits)} 只："]
        for monster in hits[:40]:
            lines.append(f"· {monster.cn}：AC {monster.ac}　HP {monster.hp}")
        if len(hits) > 40:
            lines.append(f"…还有 {len(hits) - 40} 只（写名字查详情）。")
        return "\n".join(lines)

    monster = get_monster(text)
    if monster is None:
        raise KeyError(f"未知怪物：{text}")
    return monster.stat_block()


def _query_bestiary(name: str) -> str:
    """查《怪物图鉴（2025）》的**名录**：名字 / 类型 / CR / 栖息地。

    只有名录没有数值——那份参考书的导出把每张数据卡都变成了「正在加载……」。
    顺手告诉 DM「这只本局打不打得了」，省得它拿着名录去开战。
    """
    text = bestiary_text(name)
    asked_cr = name.strip().lower().startswith("cr")
    hit = None if asked_cr else find_bestiary(name)
    if hit is not None:
        playable = get_monster(hit.name) is not None
        text += (
            "\n本局有它的数据卡，可以直接开战（query_data: table=monster）。"
            if playable
            else "\n本局**没有**它的数据卡——想用就得先补数值（res/dnd/monsters*.yaml）。"
        )
    return text


def _query_spell(name: str) -> str:
    """查法术：留空列全部，给职业名列该职业可用法术，给法术名出数据块。"""
    if not name:
        lines = [f"法术（共 {len(SPELLS)} 个，0~3 环）："]
        for level in (0, 1, 2, 3):
            names = [s.cn for s in spells_of_level(level)]
            lines.append(
                f"· {['戏法', '一环', '二环', '三环'][level]}（{len(names)}）："
                + "、".join(names)
            )
        return "\n".join(lines)

    # 职业名（精确）优先：不然「法师」会被「法师之手」的包含匹配抢走。
    try:
        cls = get_class(name)
    except KeyError:
        cls = None
    if cls is not None:
        spells = spells_for_class(cls.key)
        lines = [f"{cls.cn}可用法术（{len(spells)} 个）："]
        lines.append(format_spell_lines(spells))
        lines.append(
            f"施法属性：{cls.casting_text or '—'}　施法类型：{cls.caster_text or '—'}"
            f"　建卡可选：戏法 {cls.cantrips} 个 + 法术 {cls.spells_known} 个"
        )
        return "\n".join(lines)

    spell = get_spell(name)
    if spell is not None:
        classes = []
        for key in spell.classes:
            try:
                classes.append(get_class(key).cn)
            except KeyError:
                classes.append(key)
        return (
            spell.full_text()
            + "\n可用职业："
            + "、".join(classes)
            + f"\n伤害表达式：{spell.damage_expr(0) or '—'}"
        )
    raise KeyError(f"未知法术 / 职业：{name}")


def _query_npc_tier(name: str) -> str:
    """查人形 NPC 档位：留空列四档，给档位名给出 1/3/5/9 级队伍下的数值。"""
    if not name:
        lines = [
            "人形 NPC 档位（写法「名字:档位」，数值按队伍等级现算、没有正式 CR）："
        ]
        for tier in NPC_TIERS:
            lines.append(f"· {tier.cn}：{tier.desc}")
        lines.append("也可以用怪物表里的模板：名字:匪帮首领 / 名字:骑士 / 名字:卫兵……")
        return "\n".join(lines)
    tier = get_tier(name)
    if tier is None:
        raise KeyError(
            f"未知 NPC 档位：{name}（可用：" + "、".join(tier_names()) + "）"
        )
    lines = [f"{tier.cn}（{tier.en}）", tier.desc]
    for level in (1, 3, 5, 9):
        monster = npc_monster(tier, level)
        if monster is not None:
            key = f"{NPC_KEY_PREFIX}/{tier.key}/{level}"
            lines.append(f"· {monster.summary()}　（击败可得 {npc_xp(key)} XP）")
    lines.append("别名：" + "、".join(tier.aliases or ("（无）",)))
    return "\n".join(lines)


def _condition_effect_brief(effect: Any) -> str:
    """把一个 :class:`ConditionEffect` 翻译成一行人类可读的结算效果。"""
    parts: list[str] = []
    for label, adv in (
        ("自身攻击", effect.own_attack),
        ("被攻击", effect.vs_attack),
        ("属性 / 技能检定", effect.own_check),
        ("豁免", effect.own_save),
    ):
        if adv is Advantage.ADVANTAGE:
            parts.append(f"{label}有优势")
        elif adv is Advantage.DISADVANTAGE:
            parts.append(f"{label}有劣势")
    for ability in effect.auto_fail_saves:
        parts.append(f"{get_ability(ability).cn}豁免自动失败")
    for ability in effect.check_adv_abilities:
        parts.append(f"{get_ability(ability).cn}检定优势")
    for ability in effect.save_adv_abilities:
        parts.append(f"{get_ability(ability).cn}豁免优势")
    if effect.incapacitated:
        parts.append("无法行动也无法反应")
    if effect.breaks_concentration:
        parts.append("中断当前专注")
    if effect.speed_zero:
        parts.append("速度降为 0")
    elif effect.speed_scale != 1.0:
        parts.append(f"速度 ×{effect.speed_scale:g}")
    if effect.hp_max_scale != 1.0:
        parts.append(f"生命上限 ×{effect.hp_max_scale:g}")
    if effect.resistances:
        parts.append("伤害抗性：" + "、".join(effect.resistances))
    parts.extend(effect.notes)
    return "；".join(parts) or "（无机械效果，纯叙事状态）"


def _query_difficulty(name: str) -> str:
    """查 5e 难度档位与各技能基准 DC（DM 定 check 的 dc 时用）。"""
    text = str(name or "").strip()
    if not text:
        lines = ["5e 难度档位（DC）："]
        examples = {
            "非常简单": "大衣的扣子",
            "容易": "爬上矮墙",
            "中等": "攀爬普通屋顶、徒手推开木门",
            "困难": "跳过一丈宽的壕沟、把门连框拆下",
            "非常困难": "徒手折断铁栅",
            "极其困难": "一击劈开石墙",
        }
        for label, dc in DC_BANDS:
            lines.append(f"· {label} DC {dc}：{examples.get(label, '')}")
        lines.append(
            "\n系统会按「技能基准 DC + 叙述里的难度词」自动定档："
            "偷偷 / 小心 / 轻松 → 降一档，徒手 / 拼尽全力 / 硬闯 → 升一档；"
            "玩家显式写 dc=N 时以他为准。定完档记得在叙述里说清难在哪。"
        )
        return "\n".join(lines)
    from ...dnd.skills import get_skill

    try:
        skill = get_skill(text)
    except KeyError:
        return (
            f"没有叫「{text}」的技能。留空可列出全部难度档位，"
            "技能表用 query_data（table=skill）查。"
        )
    base = SKILL_BASE_DC.get(skill.key, DEFAULT_BASE_DC)
    return (
        f"{skill.cn}（{skill.label}）的基准难度：{describe_dc(base)} DC {base}。\n"
        "难一档是「非常困难」，容易一档是「容易」；"
        "描述词（如偷偷、徒手、拼尽全力）会在这之上调整。"
    )


def _query_rule(name: str) -> str:
    """查探索 / 环境规则（坠落伤害、旅行步调、强行军、攀爬游泳）。"""
    from ...dnd import environment

    return environment.rule_lookup(str(name or "").strip())


def _query_condition(name: str) -> str:
    """查状态条件：留空列全部，给名字出结算效果（支持同义写法 / 力竭）。"""
    text = str(name or "").strip()
    if not text:
        lines = ["状态条件（update_character 的 conditions 字段可直接设置）："]
        for cond in CONDITIONS:
            lines.append(f"· {cond.cn}（{cond.en}）：{cond.desc}")
        lines.append(
            "· 力竭（Exhaustion，1~6 级累加）：属性检定劣势 → 速度减半 → "
            "攻击与全部豁免劣势 → 生命上限减半 → 速度归零 → 死亡"
        )
        return "\n".join(lines)
    if text in {"力竭", "疲劳", "exhaustion", "exhausted"}:
        return (
            "力竭（Exhaustion）按等级累加：\n"
            "1 级：属性检定劣势；2 级：速度减半；3 级：攻击检定与全部豁免劣势；"
            "4 级：生命上限减半；5 级：速度降为 0；6 级：死亡。\n"
            "完成一次长休可降低 1 级。"
        )
    normalized = normalize_conditions(text)
    cond = None
    for item in normalized:
        cond = CONDITION_MAP.get(next((c.key for c in CONDITIONS if c.cn == item), ""))
        if cond is not None:
            break
    if cond is None:
        known = "、".join(c.cn for c in CONDITIONS)
        raise KeyError(f"未知状态：{text}（可用：{known}；力竭写「力竭」）")
    effect = merge_effects([cond.cn])
    return (
        f"{cond.cn}（{cond.en}）\n{cond.desc}\n"
        f"结算效果：{_condition_effect_brief(effect)}"
    )


#: 数据表名（含中文别名）-> 查询函数
_DATA_TABLES = {
    "race": _query_race,
    "profession": _query_profession,
    "skill": _query_skill,
    "weapon": _query_weapon,
    "armor": _query_armor,
    "monster": _query_monster,
    "bestiary": _query_bestiary,
    "item": _query_item,
    "spell": _query_spell,
    "npc_tier": _query_npc_tier,
    "condition": _query_condition,
    "difficulty": _query_difficulty,
    "rule": _query_rule,
}

_DATA_ALIASES = {
    "race": "race",
    "种族": "race",
    "profession": "profession",
    "职业": "profession",
    "class": "profession",
    "skill": "skill",
    "技能": "skill",
    "weapon": "weapon",
    "武器": "weapon",
    "armor": "armor",
    "armour": "armor",
    "护甲": "armor",
    "monster": "monster",
    "怪物": "monster",
    "怪物图鉴": "monster",
    "bestiary": "bestiary",
    "图鉴": "bestiary",
    "名录": "bestiary",
    "图鉴名录": "bestiary",
    "敌人": "monster",
    "item": "item",
    "items": "item",
    "物品": "item",
    "魔法物品": "item",
    "道具": "item",
    "宝物": "item",
    "spell": "spell",
    "spells": "spell",
    "法术": "spell",
    "法术表": "spell",
    "npc_tier": "npc_tier",
    "tier": "npc_tier",
    "档位": "npc_tier",
    "npc档位": "npc_tier",
    "npc": "npc",
    "npc名单": "npc",
    "名单": "npc",
    "人物": "npc",
    "熟人": "npc",
    "condition": "condition",
    "conditions": "condition",
    "状态": "condition",
    "状态条件": "condition",
    "difficulty": "difficulty",
    "dc": "difficulty",
    "难度": "difficulty",
    "难度表": "difficulty",
        "rule": "rule",
        "规则": "rule",
        "探索规则": "rule",
        "坠落": "rule",
        "旅行": "rule",
        "强行军": "rule",
    # —— 全局世界库文档 ——
    "world": "world",
    "世界": "world",
    "世界地图": "world",
    "event": "event",
    "事件": "event",
    "事件卡": "event",
    "realm": "realm",
    "王国": "realm",
    "地区": "realm",
    "王国/地区": "realm",
    "province": "province",
    "区域": "province",
    "省": "province",
    "省份": "province",
    "city": "city",
    "据点": "city",
    "城镇": "city",
    "城市": "city",
    "place": "place",
    "野外地点": "place",
    "自然地点": "place",
    "地点": "place",
    "dungeon": "dungeon",
    "地下城": "dungeon",
    "scene": "scene",
    "场景": "scene",
}


def _query_world_doc(context: AgentContext, kind: str, name: str) -> str:
    """读当前存档所属世界里的文档：给定名字返回正文，留空则列出该类文档。"""
    label = DOC_KIND_LABELS.get(kind, kind)
    save = load_save(context.save_id) if context.save_id else None
    world_key = save.world_key if save is not None else "main"

    if kind == "event":
        # 事件卡的键是自动生成的 ID（形如 ch3_20261021_ab12），按标题找更符合使用习惯
        cards = list_events(world_key)
        if not name:
            if not cards:
                return "这个世界里还没有事件卡"
            return "事件卡：" + "；".join(
                f"{c.title}（{c.arc_label}"
                + (f"· CR {c.target_cr}" if c.target_cr else "")
                + "）"
                for c in cards
            )
        picked = next(
            (c for c in cards if name in (c.key, c.title)),
            None,
        )
        if picked is None:
            return f"没有找到事件卡「{name}」。现有：" + (
                "、".join(c.title or c.key for c in cards) or "（空）"
            )
        text = load_doc(world_key, "event", picked.key)
        return text or f"事件卡「{picked.title}」的文档是空的"

    keys = list_docs(world_key, kind).get(kind, [])
    if not name:
        if not keys:
            return f"世界库里还没有{label}文档"
        return f"{label}文档：" + "、".join(keys)
    doc = read_doc(world_key, kind, name)
    if doc is None:
        return f"没有找到{label}「{name}」。现有：" + ("、".join(keys) or "（空）")
    return doc.content


class QueryDataTool(AgentTool):
    """查询 DND 静态数据表。"""

    function_name = "query_data"
    max_result_chars = 2500
    description = (
        "查 DND 数据表（race/profession/skill/weapon/armor/monster/bestiary/item/"
        "spell/condition/difficulty/rule/npc_tier/npc）或世界库文档（world/realm/"
        "province/city/place/dungeon/scene/event），table 可写中文；"
        "bestiary=图鉴名录（cr 5 按 CR 挑）；monster 按类型/栖息地/群组查或写"
        "「随机 幽暗地域 亡灵x3」；rule 查探索规则（坠落/旅行步调/强行军）"
    )
    args = (
        ToolArg("table", "str", "表名 / 文档类型"),
        ToolArg("name", "str", "条目或文档名；留空列出全部名称"),
    )

    @staticmethod
    async def call(
        context: AgentContext, table: str = "", name: str = "", **kwargs: Any
    ) -> str:
        key = _DATA_ALIASES.get(str(table or "").strip().lower())
        text = str(name or "").strip()
        if key in DOC_KINDS:
            return _query_world_doc(context, key, text)

        if key == "npc":
            save = load_save(context.save_id) if context.save_id else None
            if save is None:
                return "当前没有存档，读不到 NPC 名单"
            if not text:
                return npcs_text(save)
            record = find_npc(save, text)
            if record is None:
                return f"名单上没有「{text}」。\n" + npcs_text(save)
            return record.brief()

        handler = _DATA_TABLES.get(key or "")
        if handler is None:
            return (
                "未知数据表，可用：race / profession / skill / weapon / armor / monster / "
                "bestiary / item / spell / condition / difficulty / rule / npc_tier / npc，"
                "或世界库文档：world / realm / province / city / place / dungeon / scene / event"
            )
        try:
            return handler(text)
        except KeyError as e:
            return str(e)


# --------------------------------------------------------------------------- #
# 改卡 / 输出
# --------------------------------------------------------------------------- #


class UpdateCharacterTool(AgentTool):
    """修改（存档里的）角色卡字段并落盘。"""

    function_name = "update_character"
    description = "改玩家角色卡并落盘（字段见参数）"
    args = (
        ToolArg(
            "field",
            "str",
            "hp / temp_hp / conditions / concentrating / 死亡豁免 / exhaustion / exp / "
            "level / name / notes / features / tags / spells / equipment / attune（同调）",
        ),
        ToolArg(
            "value",
            "str",
            "新值；hp 可写「满血」；conditions/spells/equipment 用、分隔（可写「清空」）；"
            "concentrating 写法术名（换专注会顶替旧法术）或「中断」；"
            "features/tags 追加；死亡豁免可写 清空/复活/稳定/死亡/成功N/失败N；"
            "attune 写物品名同调、终止+物品名解除（至多 3 件）",
        ),
        ToolArg("player", "str", "改谁：角色名或 user_id；单人可留空"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        field: str = "",
        value: Any = "",
        player: str = "",
        **kwargs: Any,
    ) -> str:
        card, save = _load_character(context, player)
        if card is None:
            return "没找到该玩家的角色卡"
        field = str(field or "").strip().lower()
        text = "" if value is None else str(value).strip()

        # 战斗中的生命值与死亡豁免只由系统结算——伤害、治疗、倒地、死亡豁免
        # 在 attack / cast_spell / heal 的回执里都写清楚了。
        # 从前 DM 能直接写 hp=0、死亡豁免=死亡：已经倒地的人被再写一遍，
        # 才 0 次失败的玩家被提前判死。这里把这两个字段在战斗中封掉，
        # 并把该走哪条路指出来（战报 2026-10-23 的教训）。
        if load_combat(save) is not None:
            if field in {"hp", "current_hp", "生命值", "当前hp"}:
                return (
                    "战斗中不能手改生命值：伤害由 attack / cast_spell 结算，"
                    "治疗走 heal（会自动恢复意识并清空死亡豁免进度）。"
                    "想让他掉血，就让他正常挨一次攻击。"
                )
            if (
                field in {"死亡豁免", "death_save", "death_saves"}
                and text not in {"复活", "revive", "复苏"}
            ):
                return (
                    f"战斗中不能手改死亡豁免（{card.name} 现在 "
                    f"{card.death_saves_success} 成功 / {card.death_saves_failures} 失败）："
                    "轮到他时系统自动掷 DC 10，3 次成功才伤势稳定、3 次失败才死亡；"
                    "倒地期间挨打、暴击、单次伤害超过生命上限也会自动累计。"
                    "要救他用 heal；剧情上的复活（复活术 / 神迹）才写「复活」。"
                )

        if field in {"hp", "current_hp", "生命值", "当前hp"}:
            was_downed = card.is_downed
            if text.lower() in {"满", "满血", "max", "full"}:
                card.current_hp = None
            else:
                card.current_hp = max(0, _parse_int(text, card.hp))
            message = f"当前生命值已设为 {card.hp}/{card.max_hp}"
            if card.hp <= 0:
                if not card.is_dead and not card.is_stable_down:
                    card.mark_downed()
                    message += (
                        f"（倒地濒死：死亡豁免 {card.death_saves_success} 成功 / "
                        f"{card.death_saves_failures} 失败）"
                    )
            elif was_downed and not card.is_dead:
                card.clear_death_state()
                message += "（恢复意识，死亡豁免清零）"
            if card.is_dead and card.hp > 0:
                message += (
                    "（注意：该角色仍标记为「死亡」，要用 死亡豁免=复活 才算复活）"
                )
        elif field in {"temp_hp", "临时hp", "临时生命"}:
            card.temp_hp = max(0, _parse_int(text, card.temp_hp))
            message = f"临时生命值已设为 {card.temp_hp}"
        elif field in {"conditions", "状态"}:
            if text.lower() in _CONDITION_CLEAR_WORDS:
                card.conditions = []
                message = "状态已清空"
            else:
                # 自由文本进、规范名出：「俯伏」→「倒地」、「恐惧」→「恐慌」；
                # 认不出的原样保留成自定义备注
                card.conditions = normalize_conditions(text)
                message = "状态已设为：" + ("、".join(card.conditions) or "（空）")
            effect_text = condition_text(card.conditions, exhaustion=card.exhaustion)
            if effect_text:
                message += f"\n效果：{effect_text}"
            # 失能类状态（震慑 / 麻痹 / 昏迷 / 石化 / 失能）会中断专注（5e）
            if (
                card.concentrating
                and merge_effects(card.conditions).breaks_concentration
            ):
                message += "；" + card.break_concentration("陷入失能")
        elif field in {"concentrating", "concentration", "专注"}:
            if text.lower() in _CONDITION_CLEAR_WORDS or text in {
                "中断",
                "放弃",
                "停止",
            }:
                message = card.break_concentration("主动放弃") or "当前没有在维持专注"
            else:
                spell, note = card.start_concentration(text)
                if spell is None:
                    message = f"没认出发术「{text}」，专注没有变化"
                else:
                    message = f"开始维持「{spell.cn}」的专注{note}"
        elif field in {"死亡豁免", "death_save", "death_saves"}:
            key = text.lower()
            if key in {"复活", "revive", "复苏"}:
                message = "复活：" + card.revive()
            elif key in {"稳定", "stabilize", "稳住"}:
                card.mark_stable()
                message = "伤势已稳定（仍 0 血昏迷，等治疗 / 长休）"
            elif key in {"死亡", "dead", "阵亡"}:
                card.mark_dead()
                message = "已标记为死亡"
            elif key in _CONDITION_CLEAR_WORDS:
                card.clear_death_state()
                message = "死亡豁免进度已清空"
            elif key.startswith(("成功", "失败")):
                value_num = max(0, min(3, _parse_int(re.sub(r"[^\d]", "", text), 0)))
                if key.startswith("成功"):
                    card.death_saves_success = value_num
                else:
                    card.death_saves_failures = value_num
                if card.is_dead:
                    card.mark_dead()
                elif card.is_downed and card.death_saves_success >= 3:
                    card.mark_stable()
                elif card.is_downed:
                    card.mark_downed()
                message = (
                    f"死亡豁免：{card.death_saves_success} 成功 / "
                    f"{card.death_saves_failures} 失败"
                )
            else:
                return (
                    "看不懂的死亡豁免写法，可用：清空 / 复活 / 稳定 / 死亡 / "
                    "成功N / 失败N"
                )
        elif field in {"exhaustion", "力竭"}:
            card.exhaustion = max(0, min(6, _parse_int(text, card.exhaustion)))
            message = f"力竭等级已设为 {card.exhaustion}"
        elif field == "exp":
            # 支持「=300」直接设与「+300 / -300」增减；达阈值自动走升级结算
            before = card.exp
            if text.startswith(("+", "-")):
                card.exp = max(0, card.exp + _parse_int(text, 0))
            else:
                card.exp = max(0, _parse_int(text, card.exp))
            message = f"经验值 {before} → {card.exp} XP；{xp_progress_text(card)}"
            while can_level_up(card):
                message += "\n" + level_up(card)
        elif field == "level":
            before = card.level
            target = max(1, _parse_int(text, card.level))
            card.level = min(MAX_LEVEL, target)
            floor = LEVEL_XP_THRESHOLDS[min(card.level, MAX_LEVEL) - 1]
            if card.level < before:
                # 降级时把经验一起下调，否则会出现「3 级但 50000 经验」的自相矛盾
                card.exp = min(card.exp, floor)
            else:
                # 升级时把经验抬到该级的下限，避免「等级 5 但经验 0」
                card.exp = max(card.exp, floor)
            message = (
                f"等级 Lv.{before} → Lv.{card.level}"
                f"（生命上限 {card.max_hp}，熟练加值 +{card.proficiency}）"
            )
            if target > MAX_LEVEL:
                message += f"；等级上限为 {MAX_LEVEL}，已取上限"
            if card.level in ASI_LEVELS:
                message += (
                    f"（Lv.{card.level} 是属性提升等级：请用 features 落盘属性点 / "
                    "职业特性选择）"
                )
        elif field == "name":
            card.name = text
            message = f"姓名已设为 {card.name or '（空）'}"
        elif field == "notes":
            card.notes = text
            message = "备注已更新"
            # 备注是 DM 手写的关键事实，顺手摘一条剧情记忆（零额外模型成本）
            if save is not None and text:
                remember_from_tool(
                    save,
                    "update_character",
                    {"field": "notes", "value": text, "player": card.name},
                    round=current_round(save),
                )
        elif field == "features":
            card.features.append(text)
            message = f"已追加特性：{text}"
        elif field == "tags":
            if text and text not in card.tags:
                card.tags.append(text)
            message = f"已追加标签：{text}"
        elif field in {"spells", "spell", "法术"}:
            if text.lower() in _CONDITION_CLEAR_WORDS:
                card.spells = []
                message = "法术列表已清空"
            else:
                wanted = _split_conditions(text)
                learned: list[str] = []
                unknown: list[str] = []
                for item in wanted:
                    spell = get_spell(item)
                    if spell is None:
                        unknown.append(item)
                    else:
                        learned.append(spell.key)
                card.spells = list(dict.fromkeys(learned))
                known = "、".join(s.cn for s in card.known_spells())
                message = f"法术列表已设为：{known or '（空）'}"
                if unknown:
                    message += f"；没认出这些写法：{'、'.join(unknown)}"
                if not card.is_caster:
                    message += "（注意：该职业没有施法能力）"
        elif field in {"equipment", "equip", "装备"}:
            # 战利品里的装备走这里；认得的直接换成规范名，认不出的照原样收下
            if text.lower() in _CONDITION_CLEAR_WORDS:
                card.equipment = []
                message = "装备栏已清空"
            else:
                known, unknown = card.equip(_split_conditions(text))
                message = "装备已设为：" + "、".join(card.equipment)
                if unknown:
                    message += f"；没认出这些写法（已按原名收下）：{'、'.join(unknown)}"
        elif field in {"attune", "attunement", "同调"}:
            # 同调（5e2014）：短休中专注一件物品完成；至多 3 件，不认重复的相同物品
            if text.lower() in _CONDITION_CLEAR_WORDS:
                dropped = len(card.attuned)
                card.attuned = []
                message = f"同调已全部终止（{dropped} 件）"
            elif text.startswith(("终止", "解除", "放弃")):
                target = text[2:].strip(" ：:")
                ok, note = card.unattune(target)
                if not ok:
                    return f"【{card.name}】{note}" if card.name else note
                message = note
            else:
                ok, note = card.attune(text)
                if not ok:
                    return f"【{card.name}】{note}" if card.name else note
                message = note
        else:
            return (
                "不支持的字段，可用：hp / temp_hp / conditions / concentrating / "
                "死亡豁免 / exhaustion / exp / level / name / notes / features / "
                "tags / spells / equipment / attune（同调）"
            )

        if save is not None:
            set_character(save, card)
        else:
            save_card(card)
        prefix = f"【{card.name}】" if card.name else ""
        return f"{prefix}{message}"


class AwaitPlayersTool(AgentTool):
    """声明下一轮要等哪些玩家提交行动。"""

    function_name = "await_players"
    description = "声明下一轮等哪些玩家提交（不调用沿用上轮，开局默认全员）"
    args = (ToolArg("players", "str", "用、分隔写角色名 / user_id；也可写 全员 / 无"),)
    #: 记账：只记下「下一轮等谁」，叙述不必等它
    bookkeeping = True

    @staticmethod
    async def call(context: AgentContext, players: Any = "", **kwargs: Any) -> str:
        text = str(players or "").strip()
        # 本轮真正的落库在 game.run_dm_round 结束时进行，这里只把 DM 的意思记下来并校验。
        context.data["await_players"] = text
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return f"已记下：下一轮等待「{text or '沿用当前名单'}」"
        if not text:
            return "已记下：下一轮沿用当前等待名单。"

        from .game import resolve_players

        ids, unknown = resolve_players(save, text)
        if unknown:
            return (
                "没找到这些玩家："
                + "、".join(unknown)
                + "。请按角色名或 user_id 再写一次（也可以用「全员」表示所有玩家）。"
            )
        if not ids:
            return "已记下：下一轮不等任何人，谁先提交行动就立刻由你主持。"
        labels = []
        for uid in ids:
            card = get_character(save, uid)
            labels.append(card.name if card is not None and card.name else uid)
        return f"已记下：下一轮等待 {'、'.join(labels)} 提交行动。"


class NarrateTool(AgentTool):
    """输出主持叙述并结束本次思考。"""

    function_name = "narrate"
    description = (
        "把叙述发给玩家并结束思考；必须最后调用（依赖检定 / 攻击结果时"
        "要等返回后再单独调；只写账的功能可与它同批）"
    )
    args = (ToolArg("text", "str", "要发给玩家的叙述正文"),)

    @staticmethod
    async def call(context: AgentContext, text: Any = "", **kwargs: Any) -> str:
        raise AgentFinishError(str(text or "").strip())


def _travel_hint(world_key: str, origin: str, destination: str) -> str:
    """两地之间要走多久（同省几小时，跨省按两侧区域的跨越难度）；认不出就不加。

    **同一个场所里挪位置**（主甲板 → 船长室）单独一档：「片刻」——
    那不是赶路，是在一个地方里走了几步。
    """
    if not origin or origin == destination:
        return ""
    try:
        from ...dnd.travel import travel_span
        from ...dnd.world_store import (
            get_world_map,
            province_crossing,
            province_index,
            scene_site_index,
        )

        span = travel_span(
            origin,
            destination,
            places=province_index(world_key),
            adjacency=get_world_map(world_key)[1],
            crossing=province_crossing(world_key),
            sites=scene_site_index(world_key),
        )
        return f"（从「{origin}」过来大约 {span}）" if span else ""
    except Exception:  # 旅行提示是锦上添花，算不出就不说
        return ""


def _event_entry_line(slot) -> str:
    """刚挂上的那件事件，DM 该怎么进门：**入口场景 + 一句流言**。

    **给 DM 的一定是可定位的场景名**（图节点的 ``at``），不是 ``stage``——
    stage 是「风骸岛 · 巨龙之憩」这种给人看的说法，世界库里没有它，
    拿去 move_scene 必然定位不到（2026-10-24 修）。
    """
    where = ""
    nodes = getattr(getattr(slot, "graph", None), "nodes", None) or {}
    for node in nodes.values():
        if getattr(node, "requires", None):
            continue
        at = str(getattr(node, "at", "") or "").strip()
        if at:
            where = at
            break
    parts: list[str] = []
    if where:
        parts.append(f"入口：{where}")
    try:
        rumor = slot.rumor_for(int(getattr(slot, "since_day", 1) or 1))
    except Exception:  # 流言只是点缀，挑不出就不给
        rumor = ""
    if rumor:
        parts.append(f"风声：{rumor}")
    return "；".join(parts)


class MoveSceneTool(AgentTool):
    """切换场景（缺什么生成什么），并构建出本局专属的场景副本存进存档。"""

    function_name = "move_scene"
    description = (
        "切到某地点 / 场景（没生成过会自动补齐并本局化）；**剧情事件**："
        "返回后必须在叙述里交代离场与入场"
    )
    args = (
        ToolArg("location", "str", "地点 / 场景名，如「白港」"),
        ToolArg("kind", "str", "可选：城镇据点 / 自然地点 / 地下城"),
        ToolArg("region", "str", "可选：所在区域名"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        location: str = "",
        kind: str = "",
        region: str = "",
        **kwargs: Any,
    ) -> str:
        from .game import open_scene

        if not context.save_id:
            return "当前没有存档，无法切换场景"
        save = load_save(context.save_id)
        if save is None:
            return "当前存档不见了，无法切换场景"
        if load_combat(save) is not None:
            return (
                "战斗还没结束，不能切换场景——敌人还在场上。"
                "要脱离战斗请先结束它（end_combat，如撤退 / 敌人溃散），再 move_scene。"
            )
        name = str(location or "").strip()
        if not name:
            return "请给出要前往的地点 / 场景名"

        origin = str(save.current_scene or "")
        result = await open_scene(save, name, kind=kind, region=region)
        if result is None:
            return (
                f"无法定位地点「{name}」，且没能自动生成。"
                "请换一个更明确的名称，或补上所在区域（region）与类型（kind）。"
            )
        key, text = result
        span = _travel_hint(save.world_key, origin, key)
        # **落地了**：把这一省的预写事件挂上。
        #
        # 开局时玩家没落地，按省筛一件也筛不出来——池子是空的，
        # DM 的开场白里只有「眼下没有值得追的事」。等玩家真走到地方，
        # 眼前才该有事先挂上。
        #
        # **必须在 step_move 之前**：``step_move`` 判「走进事件」看的是
        # 池子当前引导的那件——玩家第一脚就踩在事件场景上时，
        # 事件得先在池子里，否则这一脚判不到「进入」。
        #
        # 幂等：装过的、了结过的都跳过；池满（MAX_OPEN_SLOTS）就停。
        #
        # **先给 ``journey`` 一个初值**：装不上（池满 / 这一省没有预写事件 /
        # 世界库读不出来）时下面那句 ``report.dm_hint() or journey`` 会读它——
        # 不给初值就是 UnboundLocalError，而它又会被自己的 except 再踩一次，
        # 整个 move_scene 直接抛出去（第一次之后的每次移动都会这样）。
        journey = ""
        try:
            from .event_flow import install_preset_events_for_province

            installed = install_preset_events_for_province(save)
            if installed:
                first = installed[0]
                where = f"（{first.stage}）" if first.stage else ""
                # 给 DM 的是**入口场景**（可定位），不是 stage 那一串场面话
                journey = f"\n【{first.title}{where}】{_event_entry_line(first)}"
        except Exception as e:  # 装不上不该让人走不动
            log.warning(f"装预写事件失败：{str_traceback(e)}")
        # 世界时间与路上的事在这里算：每走一跳判一次（见 event_flow.step_move）
        report = None
        try:
            from .event_flow import step_move

            report = step_move(save, key, origin=origin)
            # **两件事都要说**：「这一趟走了多久、路上出了什么」与「刚挂上了哪件事」。
            # 早先写的是 ``journey = report.dm_hint() or journey``——
            # step_move 的回执几乎总是非空，于是刚装上的事件（含入口场景）
            # 每次都被覆盖掉，DM 眼前只剩「这一趟」，不知道该往哪儿走。
            moved = report.dm_hint()
            journey = (moved + journey) if moved else journey
        except Exception as e:  # 事件系统出问题不该让人走不动
            log.warning(f"推进世界时间失败：{str_traceback(e)}")
            journey = journey or ""
        # 放弃掉的事件要收尾（推刻度 / 推进化轨 / 判纪元）。发称号要调模型，
        # 所以这一步只能在 async 里做——塞进 step_move 会把它逼成 async 函数。
        if report is not None and report.abandoned_slots:
            try:
                from .event_flow import after_move
                from .gen_epoch import epithet_agent

                await after_move(save, report, agent=epithet_agent)
            except Exception as e:  # 收尾失败不该让人走不动
                log.warning(f"放弃事件后的收尾失败：{str_traceback(e)}")
        # 走够了还没走进当前事件 → 织一件新的。
        # 模型只写内容，场景候选 / 规模 / id 由程序定，validate 不过就重织。
        if report is not None and report.weave_due:
            try:
                from .event_flow import weave_event

                woven = await weave_event(save)
                if woven.ok and woven.slot is not None:
                    where = f"（{woven.slot.stage}）" if woven.slot.stage else ""
                    journey += f"\n【新的事】{woven.slot.title}{where}"
                    if woven.notice:
                        journey += f"\n　{woven.notice}"
                elif woven.problems:
                    journey += "\n【没织成】" + "；".join(woven.problems[:2])
            except Exception as e:  # 织不出新事件也不该让人走不动
                log.warning(f"织新事件失败：{str_traceback(e)}")
        return (
            f"已切换到场景「{key}」。{span}\n"
            f"{journey}\n"
            "注意：切换场景是剧情事件，必须由你的叙述来合理化——请在接下来的叙述里"
            "先用一两句交代玩家**如何离开上一处（离场）**，再描述**如何抵达这里（入场）**，"
            "让这次移动自然合理，然后给出新场景的第一印象与 2~3 个可回应的切入点。\n\n"
            f"【{key}】\n{text}"
        )


class UpdateSceneTool(AgentTool):
    """在当前场景的存档副本文末追加一条变化记录。"""

    function_name = "update_scene"
    description = "在当前场景副本文末追加一条变化（破坏 / 人物 / 物品 / 环境）"
    args = (
        ToolArg("note", "str", "变化内容，如「大门被玩家砸开」"),
        ToolArg("kind", "str", "类型（可选），默认 变化"),
    )
    #: 记账：往副本文末追加一行，叙述不必等它
    bookkeeping = True

    @staticmethod
    async def call(
        context: AgentContext, note: Any = "", kind: Any = "变化", **kwargs: Any
    ) -> str:
        if not context.save_id:
            return "当前没有存档，无法记录场景变化"
        save = load_save(context.save_id)
        if save is None or not save.current_scene:
            return "当前还没有进入任何场景，无法记录场景变化"
        text = str(note or "").strip()
        if not text:
            return "变化内容为空，未记录"
        label = str(kind or "变化").strip() or "变化"
        ok = append_scene_change(save, save.current_scene, text, kind=label)
        if not ok:
            return "当前场景没有存档副本，无法记录变化"
        # 顺手摘一条剧情记忆（零额外模型成本）：场景变化本身就是「已发生」的事。
        remember_from_tool(
            save,
            "update_scene",
            {"note": text},
            round=current_round(save),
        )
        # 顺手给地点记一条痕迹（同样零额外成本）：「这座城烧过」「这里驻过军」
        # 是世界演化轨的一半输入，另一半由势力推进自动产生。认不出的 kind
        # 会被换成按序取的措辞，不会丢信息。
        note_scene_change(save, save.current_scene, label, text)
        return f"已在场景「{save.current_scene}」记录变化：{text}"


class ReadSceneTool(AgentTool):
    """读取本局场景的完整文档（提示词里只给简介，细节按需查）。"""

    function_name = "get_scene"
    #: 场景文档要整篇给模型看，别截
    max_result_chars = 6000
    description = (
        "读取场景完整文档：布局 / 通道、可交互元素、在场者、隐藏信息、变化记录。"
        "提示词只有简介，要细节（玩家看到什么、藏着什么、出口在哪）时用它"
    )
    args = (ToolArg("name", "str", "场景名；留空读当前场景"),)

    @staticmethod
    async def call(context: AgentContext, name: Any = "", **kwargs: Any) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法读取场景"
        key = str(name or "").strip() or (save.current_scene or "").strip()
        if not key:
            return "当前还没有进入任何场景"
        body = read_scene(save, key)
        if body is None:
            doc = read_doc(save.world_key, "scene", key)
            body = doc.content if doc is not None else ""
        if not body:
            known = "、".join(list_scenes(save.save_id)) or "（无）"
            return f"没有找到场景「{key}」。本局已有场景：{known}"
        return f"【{key}】（本局场景副本）\n{body}"


# --------------------------------------------------------------------------- #
# 战斗
# --------------------------------------------------------------------------- #


class StartCombatTool(AgentTool):
    """宣布开战：掷先攻、排顺序、开始逐回合结算。"""

    function_name = "start_combat"
    #: 返回里有战场地图与开战指引，放宽
    max_result_chars = 4000
    description = "开战：掷先攻排序并接管回合（敌人回合由系统自动结算）"
    args = (
        ToolArg(
            "enemies",
            "str",
            "敌人清单：地精x3 / 3只地精；具名 NPC 写「名字:档位」，如 卡尔:老兵、打手x2:士兵；"
            "懒得挑就写「随机 幽暗地域 亡灵x3」（按类型/栖息地/群组/CR 抽）",
        ),
        ToolArg("note", "str", "起因（可选），如「在酒馆后巷伏击」"),
        ToolArg(
            "terrain",
            "str",
            "战场环境（可选），如「雨夜的窄巷」；留空按当前场景生成",
        ),
        ToolArg(
            "daylight",
            "str",
            "有无日光（可选，是/否）：地牢、室内、夜间写「否」，野外白天写「是」；"
            "只影响日照敏感的怪（狗头人 / 卓尔……），留空则由你裁定",
        ),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        enemies: Any = "",
        note: Any = "",
        terrain: Any = "",
        daylight: Any = "",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法开战"
        running = load_combat(save)
        if running is not None:
            return "战斗已经在进行中：\n" + combat_text(save, running)

        specs, unknown = parse_enemy_list(str(enemies or ""), level=party_level(save))
        if not specs:
            text = "没认出任何敌人"
            if unknown:
                text += "：" + "、".join(unknown)
            else:
                text += "（清单是空的）"
            text += (
                "。请写怪物名或 NPC 档位。可用怪物："
                + "、".join(monster_names())
                + "；人形 NPC 档位："
                + "、".join(tier_names())
                + "（写法「名字:档位」，如「打手x2:士兵」）"
            )
            return text
        if not save.player_ids:
            return "存档里还没有玩家，无法开战"

        # CR 校准：单只怪 = 队伍平均等级，成群按数量下调（见 core/dnd/encounter.py）
        level = party_level(save)
        specs, calibration = calibrate_specs(
            specs, level, party_size=max(1, len(save.player_ids))
        )

        brief = "；".join(
            part
            for part in (str(note or "").strip(), str(terrain or "").strip())
            if part
        )
        state = start_combat(
            save,
            specs,
            current=str(context.user_id or ""),
            note=brief,
            daylight=str(daylight or ""),
        )
        context.data["combat_started"] = True

        # 威胁层级（后期加压，走规则层不动数值）：专攻目标 / 增援 / 首领传奇行动 / 场地规则
        threat_note = ""
        try:
            from ...dnd.threat import profile_for
            from .event_flow import current_event_no

            threat_note = apply_threat_profile(
                state,
                profile_for(
                    event_no=current_event_no(save),
                    level=level,
                    party_size=max(1, len(save.player_ids)),
                ),
            )
        except Exception as e:  # 威胁层是增强，不该让开战失败
            log.warning(f"应用威胁层级失败（继续开战）：{str_traceback(e)}")

        lines = ["⚔️ 战斗开始，先攻已掷好。"]
        if calibration:
            lines.append(
                f"（CR 校准：队伍平均等级 {level} —— " + "；".join(calibration) + "）"
            )
        if threat_note:
            lines.append("（威胁层级：" + threat_note.replace("\n", "；") + "）")
        if unknown:
            lines.append("（没认出来的写法已忽略：" + "、".join(unknown) + "）")
        lines.append("")
        lines.append(combat_text(save, state))
        named = [c.name for c in state.order if c.is_enemy and c.is_named_npc]
        if named:
            lines.append("")
            lines.append(
                "（" + "、".join(named) + " 是具名 NPC，已记进本局名单，"
                "下次出场会带着这次的旧伤与恩怨）"
            )
        lines.append("")
        lines.append(
            "**开战的第一段叙述必须先把战场讲清楚**：地形与光线是什么样的、掩体与障碍在"
            "哪、双方各自站在什么位置、彼此相距多远（就照上面的【战场】说），然后再写谁先"
            "动手。距离与坐标**一律照【战场】里的数字写**，不要按场景描述另算一个数——"
            "玩家会拿它和系统判定对照，说错就会显得前后矛盾。之后每次攻击 / 施法系统都会校验"
            "距离与视线：够不着会被打回，先用 move 靠近；掩体已经自动算进 AC；"
            "敌人行动前先确认它是否在触及 / 射程内。"
        )
        lines.append(
            "按上面的顺序叙述：标记「现在」的那位就是本轮该行动的玩家。"
            "玩家攻击用 attack、要位置用 move、施法用 cast_spell（都由系统掷命中与伤害）；"
            "敌人回合由系统自动结算，你只写叙述。不要替还没轮到的玩家决定行动。"
        )
        lines.append(
            "怪物特性里**能算的那些由系统自动结算**，不用你记也不用你写："
            "集群战术 / 血腥狂怒（优势）、魔法抗性（法术豁免优势）、传奇抗性（失败改成功，"
            "每日有次数）、再生（轮到它时回血）、不死坚韧（0 血豁免）、自爆（死亡时波及半径内）、"
            "状态免疫（免疫的状态挂不上）、日照敏感（有日光则劣势，开战可用 daylight=是/否 定）。"
            "结算文案里会写明触发了哪一条，照着叙述即可。"
        )
        return "\n".join(lines)


class MoveTool(AgentTool):
    """按棋盘移动参战者（系统算路径、消耗与剩余移动）。"""

    function_name = "move"
    description = (
        "移动参战者：靠近目标或走到某格（坐标 1 起算），系统算路径与消耗并扣本回合移动；"
        "够不着时先用它靠近；**敌人上前也用它**（player 写敌人名）"
    )
    args = (
        ToolArg("target", "str", "靠近谁，与 pos 二选一"),
        ToolArg("pos", "str", "目标格坐标，写 列,行，如 7,4"),
        ToolArg("player", "str", "谁在移动：角色名 / user_id / 敌人名；单人可留空"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        target: Any = "",
        pos: Any = "",
        player: Any = "",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法移动"
        state = load_combat(save)
        if state is None:
            return "现在不在战斗中；剧情里的移动直接叙述即可，不需要本功能"
        ref = str(player or "").strip() or str(context.user_id or "")
        return move_combatant(
            save, state, ref, target=str(target or ""), pos=str(pos or "")
        )


class AttackTool(AgentTool):
    """玩家攻击：自动掷命中、掷伤害并扣敌人生命。"""

    function_name = "attack"
    description = "玩家攻击：自动掷命中与伤害并扣血（武器默认取卡上第一件）"
    args = (
        ToolArg("target", "str", "攻击目标，如 地精2"),
        ToolArg("weapon", "str", "武器（可选）"),
        ToolArg("player", "str", "谁在攻击：角色名或 user_id；单人可留空"),
        ToolArg("advantage", "str", "普通 / 优势 / 劣势"),
        ToolArg("two_handed", "str", "两用武器双手持用：是/否（默认否）"),
        ToolArg("off_hand", "str", "副手攻击（双武器，伤害不加大幅值）：是/否"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        target: Any = "",
        weapon: Any = "",
        player: Any = "",
        advantage: Any = "普通",
        two_handed: Any = "",
        off_hand: Any = "",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法结算攻击"
        state = load_combat(save)
        if state is None:
            return "现在不在战斗中，先用 start_combat 开战"
        card, _save = _load_character(context, str(player or ""))
        if card is None:
            return "没找到该玩家的角色卡，无法结算攻击"
        text, _hit = combat_attack(
            save,
            card,
            state,
            str(target or ""),
            str(weapon or ""),
            advantage=_parse_advantage(advantage),
            two_handed=str(two_handed or "").strip() in {"是", "双手", "yes", "y", "true"},
            off_hand=str(off_hand or "").strip() in {"是", "副手", "yes", "y", "true"},
        )
        # 行动经济（软提示）：真正掷过攻击才记一次「动作」；超支只提醒，不拦截。
        if f"【{card.name}】" in text:
            note = use_resource(save, state, card.user_id, "action")
            if note:
                text += f"\n{note}"
        return text


class HealTool(AgentTool):
    """治疗：药水、绷带、神术、药水瓶——都能走它。"""

    function_name = "heal"
    description = (
        "治疗参战者并写回生命值（药水 / 绷带 / 法术都走它）。"
        "**治疗倒地濒死者会让他立刻恢复意识并清空死亡豁免**；已阵亡者无效"
    )
    args = (
        ToolArg("target", "str", "被治疗者：玩家角色名 / user_id，或敌人名"),
        ToolArg("amount", "str", "治疗量点数，如 8；写「d8」按骰子掷；-3 表示扣血"),
        ToolArg(
            "reason",
            "str",
            "用了什么（可选），如「喝下治疗药水」；会写进回执供叙述引用",
        ),
    )
    #: 回执里带死亡豁免进度等明细，别用默认的600 字截掉
    max_result_chars = 900

    @staticmethod
    async def call(
        context: AgentContext,
        target: Any = "",
        amount: Any = "",
        reason: Any = "",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法治疗"
        name = str(target or "").strip()
        if not name:
            return "请给出被治疗者的名字（玩家角色名或敌人名）"

        # 治疗量：支持「d8」骰子表达式与「+8 / -3」增减
        expr = str(amount or "").strip()
        if not expr:
            return "请给出治疗量点数（如 8，或 d8+2）"
        if re.fullmatch(r"[+-]?\d{1,3}", expr):
            value = int(expr)
        else:
            value, _detail = roll_damage(expr, rng=None)

        state = load_combat(save)
        combatant = None
        if state is not None:
            combatant = find_combatant(state, name)
        if combatant is not None:
            text = heal_combatant(save, state, combatant, value)
        else:
            # 不在战斗里也能治（剧情中休整、用道具），直接写角色卡
            card = get_character(save, name) if name in save.player_ids else None
            if card is None:
                for uid in save.player_ids:
                    found = get_character(save, uid)
                    if found is not None and found.name == name:
                        card = found
                        break
            if card is None:
                return (
                    f"没找到叫「{name}」的参战者。治疗需要战斗中先用 move / start_combat "
                    "把人放上场，或用角色名。"
                )
            if card.is_dead:
                return (
                    f"{card.name} 已经阵亡，普通治疗无效；复活需要复活术 / 神迹这类"
                    "剧情手段，可用 update_character 的「死亡豁免」字段写 value=复活。"
                )
            was_down = card.hp <= 0
            if value < 0:
                card.current_hp = max(0, card.hp + value)
            else:
                card.current_hp = min(card.max_hp, card.hp + value)
            text = f"{card.name} 剩余 {card.hp}/{card.max_hp}"
            if was_down and card.hp > 0:
                card.clear_death_state()
                text += "，恢复意识（死亡豁免清零）"
            set_character(save, card)
            text += "。"

        why = str(reason or "").strip()
        if why:
            text = f"（{why}）{text}"
        return text


class EndCombatTool(AgentTool):
    """结束当前战斗。"""

    function_name = "end_combat"
    description = (
        "结束当前战斗（敌人全灭 / 玩家撤退 / 谈判成功），自动结算经验与战利品，"
        "清掉先攻顺序并回到普通推进"
    )
    args = (ToolArg("result", "str", "战斗结果（可选），如「敌人溃散」「双方议和」"),)
    #: 结算文本含每人经验 / 升级明细 / 战利品清单，别用默认的 600 字把它截掉
    max_result_chars = 2500

    @staticmethod
    async def call(context: AgentContext, result: Any = "", **kwargs: Any) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法结束战斗"
        combat = load_combat(save)
        if combat is None:
            return "现在不在战斗中"
        # 先结算再清战斗：结算要用 combat.defeated 里的战果
        report = settle_combat(save, combat)
        clear_combat(save)
        state = load_round(save)
        state.waiting = None
        state.actions = {}
        save_round(save, state)
        tail = f"（{result}）" if str(result or "").strip() else ""
        return (
            f"⚔️ 战斗结束{tail}，先攻顺序已清空。\n{report.text()}\n"
            "接下来回到普通推进：下一轮默认等待所有玩家（也可以用 await_players 指定名单）。"
        )


# --------------------------------------------------------------------------- #
# 法术
# --------------------------------------------------------------------------- #


def _spell_targets(save, state, refs: str) -> tuple[list[Any], list[str]]:
    """把 DM 写的目标串解析成参战者 / 角色卡列表。

    战斗中优先按参战者（敌人 + 玩家）匹配，其次按存档里的角色卡匹配
    （治疗类法术在战斗外也能用）。
    """
    found: list[Any] = []
    unknown: list[str] = []
    for part in re.split(r"[、,，;；/\n]+", str(refs or "")):
        ref = part.strip()
        if not ref:
            continue
        combatant = find_combatant(state, ref) if state is not None else None
        if combatant is not None:
            found.append(combatant)
            continue
        card = find_character(save, ref)
        if card is not None:
            found.append(card)
            continue
        unknown.append(ref)
    return found, unknown


def _target_name(target: Any) -> str:
    return getattr(target, "name", "") or str(getattr(target, "user_id", ""))


def _spell_range_feet(text: str) -> int:
    """把法术射程文字解析成尺：``-1`` 表示不限 / 特殊。

    ``自身`` 单独出现表示只能作用自己（0）；``自身（15 尺半径）`` 这类范围法术
    取括号里的尺数当可及距离。
    """
    raw = str(text or "")
    if any(word in raw for word in ("触碰", "接触", "触及")):
        return 5
    if any(word in raw for word in ("视线", "特殊", "不限", "见描述")):
        return -1
    match = re.search(r"(\d+)\s*(?:尺|feet|ft|foot)", raw)
    if match:
        return int(match.group(1))
    if "里" in raw:
        miles = re.search(r"(\d+)\s*里", raw)
        if miles:
            return int(miles.group(1)) * 5280
    if any(word in raw for word in ("自身", "自己", "self")):
        return 0
    return -1


def _spell_range_block(state, caster, targets, spell_obj) -> str:
    """法术射程校验：够不着就返回原因（空串表示通过）。"""
    if state is None or not has_map(state):
        return ""
    limit = _spell_range_feet(spell_obj.range_text)
    if limit < 0:
        return ""
    me = find_combatant(state, str(caster.user_id))
    for target in targets:
        if isinstance(target, DndCharacter):
            if str(target.user_id) == str(caster.user_id):
                continue
            return f"{spell_obj.cn}够不到 {target.name}：{target.name}不在战场上"
        if limit == 0:
            return f"{spell_obj.cn}只能作用于自己"
        gate = range_check(
            state,
            me,
            target,
            reach=5,
            normal=0 if limit <= 5 else limit,
            max_range=0 if limit <= 5 else limit,
        )
        if not gate.ok:
            return f"{spell_obj.cn}够不到 {target.name}：{gate.reason}"
    return ""


def _target_ac(save, state, target: Any) -> int:
    if isinstance(target, DndCharacter):
        return target.armor_class
    return effective_ac(save, target, state)


def _target_save_bonus(save, target: Any, ability: str) -> int:
    """目标的某项豁免加值（敌人查怪物数据 / NPC 模板）。"""
    if isinstance(target, DndCharacter):
        return target.save_bonus(ability)
    if target.kind == "player":
        card = get_character(save, target.cid)
        return card.save_bonus(ability) if card is not None else 0
    monster = resolve_monster(target.monster)
    return monster.save_bonus(ability) if monster is not None else 0


def _target_save(
    save,
    target: Any,
    ability: str,
    dc: int,
    *,
    state: Any = None,
    magical: bool = False,
) -> Any:
    """目标的豁免检定：自动套用目标身上的状态**与怪物特性**。

    - 束缚给敏捷豁免劣势、力竭 3 级起全部豁免劣势（中毒不影响豁免）；
    - 麻痹 / 震慑 / 昏迷 / 石化的力量与敏捷豁免**自动失败**；
    - **魔法抗性**：对抗法术与其他魔法效应的豁免有优势（只对 ``magical=True``
      的检定生效——火球术算，龙息不算）；
    - **传奇抗性**：豁免失败时改为成功，每日有次数（龙、恶魔、首领都有）。

    :param magical: 这次豁免是不是法术 / 魔法效应带来的。
    """
    if isinstance(target, DndCharacter):
        return target.check_save(ability, dc)
    if getattr(target, "kind", "") == "player":
        card = get_character(save, target.cid)
        if card is not None:
            return card.check_save(ability, dc)
    conds = combatant_conditions(save, target)
    effect = merge_effects(conds)
    monster = resolve_monster(target.monster)
    rules = rules_of(monster)
    result = roll_check(
        _target_save_bonus(save, target, ability),
        dc,
        kind=f"{get_ability(ability).cn}豁免",
        advantage=combine_advantage(
            effect.own_save, save_advantage(rules, magical=magical)
        ),
    )
    if save_auto_fail(conds, ability):
        # 状态导致的自动失败：照掷骰（给玩家看点数），但结果强制为失败
        result.auto_critical = False
        result.forced_failure = True
    # 传奇抗性：失败了就花一次改成成功（次数用完了就老实认）
    if not result.success and rules.legendary_resistance > 0:
        used = int(getattr(target, "legendary_resistance_used", 0) or 0)
        if used < rules.legendary_resistance:
            target.legendary_resistance_used = used + 1
            result.forced_success = True
            if state is not None:
                save_combat(save, state)  # 次数要跟着战斗存档走
    return result


def _target_down(save, target: Any) -> bool:
    if isinstance(target, DndCharacter):
        return target.hp <= 0
    if target.kind == "player":
        card = get_character(save, target.cid)
        return card is None or card.hp <= 0
    return target.out or target.hp <= 0


def _target_conditions(save, target: Any) -> list[str]:
    """目标当前的状态列表（玩家读角色卡，敌人读战斗状态）。"""
    if isinstance(target, DndCharacter):
        return list(target.conditions)
    return combatant_conditions(save, target)


def _auto_crit(save, target: Any, *, melee: bool, distance: int) -> bool:
    """命中麻痹 / 昏迷的目标是否自动暴击（目标在战场外（角色卡）时不适用）。"""
    if isinstance(target, DndCharacter):
        return False
    return auto_crit_on_hit(save, target, melee=melee, distance=distance)


def _spell_damage(
    save, state, target: Any, amount: int, damage_type: str = "", *, crit: bool = False
) -> str:
    """对目标结算法术伤害（玩家写回角色卡，战斗外用角色卡）。

    与战斗内伤害保持同一套规则：临时生命 → 抗性 / 免疫 / 易伤 → 扣血 →
    0 血倒地昏迷 → 受伤后掷专注维持豁免。
    """
    amount = max(0, int(amount))
    if isinstance(target, DndCharacter):
        absorbed = min(target.temp_hp, amount)
        target.temp_hp -= absorbed
        incoming = amount - absorbed
        incoming, res_note = target.apply_resistance(incoming, damage_type)
        before = target.hp
        target.current_hp = max(0, target.hp - incoming)
        note = f"{target.name} 剩余 {target.hp}/{target.max_hp}"
        if absorbed:
            note += f"（临时生命吸收 {absorbed}）"
        if res_note:
            note += f"（{res_note}）"
        if before <= 0 and incoming > 0:
            note += "（" + target.add_death_failure(2 if crit else 1) + "）"
        elif target.hp <= 0:
            target.mark_downed()
            note += "，倒地进入濒危"
        if incoming > 0 and target.concentrating:
            if target.hp <= 0:
                note += "；" + target.break_concentration("生命归零")
            else:
                _kept, conc_text = target.concentration_save(incoming)
                note += f"；{conc_text}"
        set_character(save, target)
        return note + "。"
    return combat_damage(
        save, state, target, amount, damage_type=damage_type, crit=crit
    )


def _spell_heal(save, state, target: Any, amount: int, *, temp: bool = False) -> str:
    """对目标结算法术治疗（``temp`` 为真时给临时生命，不叠加只取高者）。"""
    amount = max(0, int(amount))
    if isinstance(target, DndCharacter):
        if temp:
            target.temp_hp = max(target.temp_hp, amount)
            set_character(save, target)
            return f"{target.name} 获得 {target.temp_hp} 点临时生命。"
        target.current_hp = min(target.max_hp, target.hp + amount)
        set_character(save, target)
        return f"{target.name} 剩余 {target.hp}/{target.max_hp}。"
    if temp and target.kind == "player":
        card = get_character(save, target.cid)
        if card is not None:
            card.temp_hp = max(card.temp_hp, amount)
            set_character(save, card)
            return f"{target.name} 获得 {card.temp_hp} 点临时生命。"
    return heal_combatant(save, state, target, amount)


class CastSpellTool(AgentTool):
    """施法：自动结算法术攻击 / 目标豁免 / 治疗，并扣除法术位。"""

    function_name = "cast_spell"
    #: 范围法术会一次结算多个目标，放宽
    max_result_chars = 1500
    description = (
        "施法：自动结算法术攻击 / 目标豁免 / 治疗并扣法术位"
        "（戏法不消耗，位不够或够不着会被打回）"
    )
    args = (
        ToolArg("spell", "str", "法术名，如 火焰箭 / 火球术（见 spell 表）"),
        ToolArg(
            "target", "str", "目标，多个用、分隔；范围法术写全需豁免的人；留空为自己"
        ),
        ToolArg("player", "str", "谁在施法：角色名或 user_id；单人可留空"),
        ToolArg("slot", "str", "用第几环（可选，默认本法环阶）"),
        ToolArg("advantage", "str", "普通 / 优势 / 劣势"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        spell: Any = "",
        target: Any = "",
        player: Any = "",
        slot: Any = "",
        advantage: Any = "普通",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法结算施法"
        card, save = _load_character(context, str(player or ""))
        if card is None:
            return "没找到该玩家的角色卡，无法结算施法"

        spell_obj = get_spell(spell)
        if spell_obj is None:
            return (
                f"没认出来这个法术：{spell}。可用 query_data（table=spell）查法术名，"
                "或先让玩家在建卡 / 改卡时把法术记进卡里。"
            )
        if not card.is_caster:
            return f"{card.name} 的职业没有施法能力，无法施展法术。"

        # 5e：着装不具有熟练项的护甲 / 盾牌时不能施法（先脱甲再施）
        castable, cast_block = card.can_cast()
        if not castable:
            return (
                f"{card.name} {cast_block}——无法施展 {spell_obj.cn}。"
                "（先用 update_character 换下护甲 / 盾牌，或让 DM 按叙述裁决）"
            )

        warn = ""
        if not card.knows_spell(spell_obj):
            warn = f"（注意：{spell_obj.cn} 不在 {card.name} 的法术表里，若是卷轴 / 神赐请自行解释）\n"

        # —— 射程校验（先查够不够得着，够不着就别花法术位）——
        pre_state = load_combat(save)
        refs = str(target or "").strip()
        if refs.lower() in {"", "自己", "自身", "self"}:
            probe: list[Any] = [card]
        else:
            probe, _unknown = _spell_targets(save, pre_state, refs)
        blocked = _spell_range_block(pre_state, card, probe, spell_obj)
        if blocked:
            return warn + blocked

        # —— 法术位 ——
        slot_level = spell_obj.level
        requested = _parse_int(str(slot or "").strip(), 0)
        if requested > 0:
            slot_level = max(spell_obj.level, requested)
        if spell_obj.level > 0:
            if not card.slot_available(slot_level):
                if not str(slot or "").strip():
                    highest = card.highest_slot()
                    if highest >= spell_obj.level:
                        slot_level = highest
                if not card.slot_available(slot_level):
                    return (
                        f"{card.name} 没有可用的 {slot_level} 环法术位"
                        f"（现有：{card.spell_slots_text()}），无法施展 {spell_obj.cn}。"
                    )
            card.spend_slot(slot_level)
            set_character(save, card)
            if slot_level > spell_obj.level:
                upcast = f"（升环：用 {slot_level} 环施展）" + (
                    f"，{spell_obj.upcast}" if spell_obj.upcast else ""
                )
            else:
                upcast = ""
            slot_note = f"　法术位 {card.spell_slots_text()}{upcast}"
        else:
            slot_note = "　（戏法，不消耗法术位）"

        lines = [f"【{card.name}】施展 {spell_obj.label()}{slot_note}"]
        if spell_obj.concentration:
            # 登记专注（5e 同时只能维持一个，新的会顶替旧的）
            _kept, conc_note = card.start_concentration(spell_obj)
            set_character(save, card)
            lines.append(
                "（需专注：受伤要掷体质豁免维持；同时只能维持一个专注法术）"
                + (conc_note or "")
            )

        state = load_combat(save)
        # 行动经济（软提示）：按施法时间记下这次占用的动作 / 附赠动作 / 反应
        note = use_resource(
            save, state, card.user_id, resource_of(spell_obj.casting_time)
        )
        if note:
            lines.append(note)

        refs = str(target or "").strip()
        if refs.lower() in {"", "自己", "自身", "self"}:
            targets: list[Any] = [card]
            unknown: list[str] = []
        else:
            targets, unknown = _spell_targets(save, state, refs)
        if unknown:
            lines.append("（没找到这些目标：" + "、".join(unknown) + "）")

        casting_mod = card.modifier(card.casting_ability) if card.casting_ability else 0

        # —— 治疗类 ——
        if spell_obj.heal and not spell_obj.damage and not spell_obj.attack:
            expr, temp, add_mod = spell_obj.heal_info()
            if not expr:
                lines.append(
                    f"治疗：{spell_obj.heal}——由你按效果裁决（可用 update_character 改生命值）。"
                )
                return warn + "\n".join(lines)
            result = roll(expr)
            amount = result.total + (casting_mod if add_mod else 0)
            note = result.describe()
            if add_mod:
                note += f" + 施法属性 {casting_mod:+d}"
            lines.append(f"治疗量 {amount}（{note}）")
            if not targets:
                lines.append("没有指定目标，请在 target 里写治疗对象。")
                return warn + "\n".join(lines)
            for item in targets:
                lines.append("· " + _spell_heal(save, state, item, amount, temp=temp))
            return warn + "\n".join(lines)

        # —— 非战斗效果 ——
        if not spell_obj.attack and not spell_obj.save and not spell_obj.damage:
            lines.append(spell_obj.desc or "（无直接战斗数值）")
            if spell_obj.conditions and targets:
                for item in targets:
                    cond_note = apply_condition(
                        save,
                        item,
                        spell_obj.conditions,
                        rounds=spell_obj.condition_rounds,
                    )
                    if cond_note:
                        lines.append(f"· {_target_name(item)}：{cond_note}")
            lines.append(
                "请按法术效果叙述；需要时用 roll_dice / update_character 辅助。"
            )
            return warn + "\n".join(lines)

        # —— 不立刻结算的伤害：命中后追加的 / 生命值阈值 / 区域触发 ——
        if (
            spell_obj.damage
            and not spell_obj.attack
            and not spell_obj.save
            and not spell_obj.auto_damage
        ):
            lines.append(
                f"伤害 / 生命值阈值：{spell_obj.damage} {spell_obj.damage_type_text}（不立刻结算）"
            )
            lines.append(
                (spell_obj.desc or "")
                + "——由你按法术效果裁决（后续命中追加、按生命值放倒、区域滞留等）；"
                "要直接扣血就用 update_character。"
            )
            return warn + "\n".join(lines)

        if state is None:
            return (
                warn
                + "\n".join(lines)
                + f"\n现在不在战斗中，{spell_obj.cn} 的伤害 / 豁免需要先开战（start_combat）；"
                "若只是对单人使用，可用 roll_dice + update_character 手算。"
            )
        if not targets:
            lines.append("没有指定目标，请在 target 里写目标（多个用、分隔）。")
            return warn + "\n".join(lines)

        # —— 法术攻击 ——
        if spell_obj.attack:
            lines.append(f"法术攻击 {card.spell_attack_bonus:+d}")
            # 戏法按**角色等级**成长（5e2014：17 级的火焰箭是 4d10）
            damage_expr = spell_obj.damage_expr(casting_mod, level=card.level)
            # 触碰类法术攻击算近战（麻痹 / 昏迷的目标在 5 尺内被命中即暴击）
            melee_spell = any(
                word in str(spell_obj.range_text or "")
                for word in ("触碰", "接触", "触及", "自身")
            )
            caster_self = find_combatant(state, str(card.user_id))
            for item in targets:
                if _target_down(save, item):
                    lines.append(f"· {_target_name(item)} 已经倒下了，跳过。")
                    continue
                ac = _target_ac(save, state, item)
                # 目标身上的状态（倒地 / 目盲 / 束缚 / 麻痹…）改优势姿态
                dist = distance_feet(caster_self, item)
                t_adv, t_reason = attack_advantage(
                    [],
                    _target_conditions(save, item),
                    melee=melee_spell,
                    distance=dist,
                )
                adv = combine_advantage(_parse_advantage(advantage), t_adv)
                result = card.check_spell_attack(
                    ac,
                    label=f"{spell_obj.cn}攻击",
                    advantage=adv,
                )
                lines.append(f"· {_target_name(item)}：{result.describe()}（AC {ac}）")
                if t_reason:
                    lines.append(f"  　（{t_reason}）")
                if not result.success:
                    lines.append("  　未命中。")
                    continue
                crit = result.nat20 or _auto_crit(
                    save, item, melee=melee_spell, distance=dist
                )
                damage, detail = roll_damage(damage_expr, crit=crit)
                crit_text = "（暴击，伤害骰翻倍）" if crit else ""
                lines.append(
                    f"  　命中！{detail} {spell_obj.damage_type_text}{crit_text}"
                )
                lines.append(
                    "  　"
                    + _spell_damage(
                        save,
                        state,
                        item,
                        damage,
                        damage_type=spell_obj.damage_type_text,
                        crit=crit,
                    )
                )
            return warn + "\n".join(lines)

        # —— 自动命中（如魔法飞弹 / 匕首之云）——
        if spell_obj.auto_damage:
            damage, detail = roll_damage(
                spell_obj.damage_expr(casting_mod, level=card.level)
            )
            lines.append(f"自动命中：伤害 {detail} {spell_obj.damage_type_text}")
            for item in targets:
                if _target_down(save, item):
                    lines.append(f"· {_target_name(item)} 已经倒下了，跳过。")
                    continue
                lines.append(
                    f"· {_target_name(item)}："
                    + _spell_damage(
                        save,
                        state,
                        item,
                        damage,
                        damage_type=spell_obj.damage_type_text,
                    )
                )
            return warn + "\n".join(lines)

        # —— 目标豁免 ——
        dc = card.spell_save_dc
        lines.append(f"法术豁免 DC {dc}（{spell_obj.save_text}）")
        damage = 0
        detail = ""
        if spell_obj.damage:
            damage, detail = roll_damage(
                spell_obj.damage_expr(casting_mod, level=card.level)
            )
            lines.append(f"伤害 {detail} {spell_obj.damage_type_text}")
        cond_applied = False
        for item in targets:
            if _target_down(save, item):
                lines.append(f"· {_target_name(item)} 已经倒下了，跳过。")
                continue
            # 豁免检定：自动套目标状态（束缚劣势 / 麻痹等自动失败）
            # 法术的豁免：**魔法效应**，所以魔法抗性在这里生效（龙息则不算）
            result = _target_save(save, item, spell_obj.save, dc, state=state, magical=True)
            # 豁免失败 → 把法术附带的状态直接挂上（定身 = 麻痹、恐惧 = 恐慌……）。
            # 以前这里只写一句「请按法术效果叙述」：DM 说了算，引擎不认，
            # 于是被定住的怪下一轮照样行动、近战也不按麻痹算暴击。
            cond_note = ""
            if spell_obj.conditions and not result.success:
                cond_note = apply_condition(
                    save,
                    item,
                    spell_obj.conditions,
                    rounds=spell_obj.condition_rounds,
                )
                cond_applied = cond_applied or bool(cond_note)
            if not spell_obj.damage:
                lines.append(
                    f"· {_target_name(item)}：{result.describe()} → "
                    + (
                        "通过（法术未能奏效）"
                        if result.success
                        else "失败（请按法术效果叙述）"
                    )
                )
                if cond_note:
                    lines.append(f"  　{cond_note}")
                continue
            if result.success:
                if spell_obj.half_on_save:
                    lines.append(
                        f"· {_target_name(item)}：{result.describe()} → 成功豁免，伤害减半"
                    )
                    lines.append(
                        "  　"
                        + _spell_damage(
                            save,
                            state,
                            item,
                            damage // 2,
                            damage_type=spell_obj.damage_type_text,
                        )
                    )
                else:
                    lines.append(
                        f"· {_target_name(item)}：{result.describe()} → 成功豁免，不受伤害"
                    )
            else:
                lines.append(
                    f"· {_target_name(item)}：{result.describe()} → 豁免失败，受到全额伤害"
                )
                lines.append(
                    "  　"
                    + _spell_damage(
                        save,
                        state,
                        item,
                        damage,
                        damage_type=spell_obj.damage_type_text,
                    )
                )
            if cond_note:
                lines.append(f"  　{cond_note}")
        # 敌人的状态记在 combat.json 里：不落盘的话下一轮读到的还是没被控住的样子
        if cond_applied:
            save_combat(save, state)
        return warn + "\n".join(lines)


# --------------------------------------------------------------------------- #
# 休息 / NPC 名单
# --------------------------------------------------------------------------- #


class RestTool(AgentTool):
    """结算休息：长休回满生命与法术位、恢复一半已耗生命骰；短休可花生命骰回血。"""

    function_name = "rest"
    description = (
        "结算休息：长休回满生命与法术位、清临时生命、力竭 -1、恢复一半已耗生命骰；"
        "短休回契约法术位"
    )
    args = (
        ToolArg("kind", "str", "长休 / 短休"),
        ToolArg("player", "str", "为谁结算；留空 = 全员"),
        ToolArg(
            "hit_dice",
            "str",
            "短休时花几颗生命骰回血（每颗 = 1d生命骰 + 体质调整值，至少 0）；"
            "长休忽略这项",
        ),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        kind: Any = "长休",
        player: Any = "",
        hit_dice: Any = "0",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法结算休息"
        text = str(kind or "").strip()
        long_rest = text not in {"短休", "short", "short rest"}
        cards: list[DndCharacter] = []
        target = str(player or "").strip()
        if target:
            card = find_character(save, target)
            if card is None:
                return f"存档里没找到「{target}」的角色卡"
            cards.append(card)
        else:
            for user_id in save.player_ids:
                card = get_character(save, user_id)
                if card is not None:
                    cards.append(card)
        if not cards:
            return "存档里还没有玩家，无法结算休息"

        lines = ["🛏️ 长休结束：" if long_rest else "☕ 短休结束："]
        for card in cards:
            notes: list[str] = []
            if card.is_dead:
                notes.append(
                    "已经阵亡：休息无法复活（需要复活术 / 神迹，"
                    "可用 update_character 的「死亡豁免 = 复活」裁决）"
                )
                lines.append(f"· {card.name or card.user_id}：" + "；".join(notes))
                continue
            if long_rest:
                was_down = card.is_downed
                card.current_hp = None
                card.temp_hp = 0
                # 口粮：长休要吃一顿。**5e 的力竭恢复要求进食饮水**，
                # 所以饿着睡不恢复力竭；挨饿的计时按参考文档「粮食与水」：
                # 连续 **3 + 体质调整值** 天不进食才开始每日 +1 力竭
                # （不是「一天不吃就过豁免」——那条是 2024 的口径，2014 没有）。
                if card.rations > 0:
                    card.rations -= 1
                    card.hunger_days = 0
                    notes.append(f"吃掉 1 份口粮（剩 {card.rations} 份）")
                    if card.exhaustion:
                        card.exhaustion = max(0, card.exhaustion - 1)
                        notes.append(f"力竭降至 {card.exhaustion} 级")
                else:
                    card.hunger_days += 1
                    limit = card.hunger_limit()
                    if card.hunger_days > limit:
                        card.exhaustion = min(6, card.exhaustion + 1)
                        notes.append(
                            f"没有口粮：挨饿第 {card.hunger_days} 天"
                            f"（体质撑不过 {limit} 天），力竭升到 {card.exhaustion} 级"
                        )
                    else:
                        notes.append(
                            f"没有口粮，今天没吃东西"
                            f"（还能再撑 {limit - card.hunger_days} 天）"
                        )
                    if card.exhaustion:
                        notes.append("饿着睡不恢复力竭")
                before = card.spell_slots_text()
                card.restore_slots(kind="long")
                card.clear_death_state()
                notes.append(f"HP {card.hp}/{card.max_hp}")
                if was_down:
                    notes.append("从倒地中恢复（死亡豁免清零）")
                if card.is_caster:
                    notes.append(
                        f"法术位回满（{card.spell_slots_text()}，原 {before}）"
                    )
                # 5e：长休恢复「已耗生命骰的一半（至少 1 颗）」
                back = card.regain_hit_dice(max(1, card.hit_dice_total // 2))
                if card.hit_dice_used or back:
                    notes.append(
                        f"生命骰恢复 {back} 颗（剩 {card.hit_dice_left}/"
                        f"{card.hit_dice_total}）"
                    )
                if card.conditions:
                    notes.append("状态未自动清除：" + "、".join(card.conditions))
            else:
                before = card.spell_slots_text()
                card.restore_slots(kind="short")
                notes.append(f"HP {card.hp}/{card.max_hp}")
                if card.is_caster:
                    notes.append(f"法术位（{card.spell_slots_text()}，原 {before}）")
                    cls = card.profession_obj
                    if cls is not None and cls.caster_kind == "pact":
                        notes.append("契约法术位已回满")
                # 短休花生命骰：每颗 = 1d生命骰 + 体质调整值（每颗至少回 0）
                want = _parse_int(str(hit_dice or "0"), 0)
                if want > 0:
                    spent = card.spend_hit_dice(want)
                    if spent == 0:
                        notes.append("没有可花费的生命骰了")
                    else:
                        con_mod = card.modifier("con")
                        profession = card.profession_obj
                        die = int(getattr(profession, "hit_die", 8) or 8)
                        total = 0
                        details: list[str] = []
                        for _ in range(spent):
                            result = roll(f"1d{die}", label="生命骰")
                            gain = max(0, result.total + con_mod)
                            total += gain
                            details.append(f"{result.total}{con_mod:+d}")
                        healed = min(total, card.max_hp - card.hp)
                        was_down = card.is_downed
                        card.current_hp = card.hp + healed
                        notes.append(
                            f"花 {spent} 颗生命骰回血 {healed}"
                            f"（{'、'.join(details)}；剩 {card.hit_dice_left} 颗）"
                        )
                        if was_down and card.hp > 0:
                            card.clear_death_state()
                            notes.append("从倒地中恢复（死亡豁免清零）")
                else:
                    notes.append("生命骰消耗与治疗由你裁决（可用 hit_dice 指定颗数）")
            set_character(save, card)
            lines.append(f"· {card.name or card.user_id}：" + "；".join(notes))
        if long_rest:
            lines.append("（中毒 / 疾病 / 诅咒等是否解除由你判断）")
        else:
            lines.append("（短休不回复法术位——契约魔法除外；状态不会自动清除）")
        # 休息要占掉时间：长休 8 小时、短休 1 小时。世界时钟靠这个走，
        # 否则「打三场仗、长休两次，世界日还是第 1 天」。
        try:
            from .event_flow import rest_time

            lines.extend(rest_time(save, long=long_rest))
        except Exception as e:  # 时间推进失败不该毁掉一次休息
            log.warning(f"推进休息时间失败：{str_traceback(e)}")
        if not long_rest:
            # 5e2014：同调在短休中专注完成——这里点给 DM，别让他忘了这条入口
            holders = [c for c in cards if c.attuned]
            if holders:
                lines.append(
                    "同调中：" + "；".join(f"{c.name} {c.attunement_text()}" for c in holders)
                )
            lines.append(
                "（短休里可以同调魔法物品：update_character 的 attune=物品名，"
                "同时至多 3 件）"
            )
        return "\n".join(lines)


class UpdateNpcTool(AgentTool):
    """维护本局 NPC 名单（关系、状态、旧伤、所在地）。"""

    function_name = "update_npc"
    description = "维护 NPC 名单（见参数）；未登记的带上 template 补记"
    args = (
        ToolArg("name", "str", "NPC 名，如 黑心商人卡尔"),
        ToolArg("template", "str", "平民/士兵/老兵/首领 或怪物模板（可选）"),
        ToolArg("hp", "str", "当前生命（可选）"),
        ToolArg("status", "str", "存活/受伤/倒下/逃跑/被俘/失踪"),
        ToolArg(
            "note",
            "str",
            "身份、关系、欠的账、**说话风格**（口头禅/语气/称呼；不写下次就换了个人，可选）",
        ),
        ToolArg("place", "str", "最后出现的地点"),
    )
    #: 记账：把这人记进名单，叙述不必等它（这一轮怎么演，DM 心里已经有数了）
    bookkeeping = True

    @staticmethod
    async def call(
        context: AgentContext,
        name: Any = "",
        template: Any = "",
        hp: Any = "",
        status: Any = "",
        note: Any = "",
        place: Any = "",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法维护 NPC 名单"
        npc_name = str(name or "").strip()
        if not npc_name:
            return "请给出 NPC 的名字"

        # **战斗中一律不维护名单**：参战者的血量、状态、退场只认战场。
        #
        # 战场由 attack / cast_spell / 系统自动结算的敌人回合直接写，
        # 打完再由 ``sync_roster_after_combat`` 一次性回填。名单是按模板合并的
        # （三只「溺亡水手1/2/3」在 npcs.json 里只有一条「溺亡水手」），
        # 边打边写必然两套账——实际跑过：名单 15/22「存活」，
        # 战场上它只剩 9、隔壁那只早倒了。
        if load_combat(save) is not None:
            return (
                f"战斗中不维护 NPC 名单（{npc_name}）：参战者的血量与退场由战场结算"
                "（attack / cast_spell / 系统自动的敌人回合），"
                "战斗结束后会自动同步回名单。这一场之外的人物，等打完再记。"
            )

        record = find_npc(save, npc_name)
        template_text = str(template or "").strip()

        if record is None:
            if not template_text:
                return (
                    f"名单上还没有「{npc_name}」。要登记请给模板（"
                    + "、".join(tier_names())
                    + "，或用怪物表里的模板如 匪帮首领 / 骑士）。\n"
                    + npcs_text(save)
                )
            built = build_npc(template_text, party_level(save))
            if built is None:
                return (
                    f"不认识的模板：{template_text}。可用档位："
                    + "、".join(tier_names())
                    + "，或怪物表里的名字（query_data 的 monster 表）。"
                )
            monster, key = built
            hp_value = _parse_int(str(hp or ""), monster.hp)
            record = upsert_npc(
                save,
                NpcRecord(
                    name=npc_name,
                    template=key,
                    level=party_level(save),
                    hp=max(0, hp_value),
                    max_hp=monster.hp,
                    place=str(place or save.current_scene or ""),
                    met=1,
                ),
            )
            if status or note:
                update_npc(
                    save,
                    npc_name,
                    status=str(status or "") or None,
                    note=str(note or "") or None,
                )
                record = find_npc(save, npc_name) or record
            remember_from_tool(
                save,
                "update_npc",
                {"name": npc_name, "status": status, "note": note},
                round=current_round(save),
            )
            return (
                f"已把 {npc_name} 记进 NPC 名单。\n  {record.brief()}\n"
                "（首次登记：顺便用 note 记下他的说话风格——口头禅、语气、"
                "他怎么称呼玩家。下次遇到时系统会提醒你照着写。）"
            )

        changes: dict[str, Any] = {}
        if template_text:
            changes["template"] = template_text
        if str(hp or "").strip():
            changes["hp"] = max(0, _parse_int(str(hp), record.hp))
        if str(status or "").strip():
            changes["status"] = str(status).strip()
        if str(note or "").strip():
            changes["note"] = str(note).strip()
        if str(place or "").strip():
            changes["place"] = str(place).strip()
        if not changes:
            return record.brief()
        updated = update_npc(save, npc_name, **changes)
        if updated is None:
            return f"更新 {npc_name} 失败"
        remember_from_tool(
            save,
            "update_npc",
            {
                "name": npc_name,
                "status": changes.get("status"),
                "note": changes.get("note"),
            },
            round=current_round(save),
        )
        return "已更新：\n  " + updated.brief()


# --------------------------------------------------------------------------- #
# 剧情记忆
# --------------------------------------------------------------------------- #


class RememberTool(AgentTool):
    """手动记一条剧情记忆（可选；系统也会从别的工具里自动摘）。"""

    function_name = "remember"
    description = "记一条剧情记忆"
    args = (
        ToolArg("text", "str", "一句话，40 字以内"),
        ToolArg(
            "kind",
            "str",
            "fact=已发生 / foreshadow=待回收 / relation=关系 / resolved=标记已回收",
        ),
    )
    #: 记账：写进剧情记忆，叙述不必等它
    bookkeeping = True

    @staticmethod
    async def call(
        context: AgentContext,
        text: Any = "",
        kind: Any = "fact",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法记录剧情记忆"
        body = str(text or "").strip()
        if not body:
            return "记忆内容为空，未记录"
        kind_text = str(kind or "fact").strip().lower()
        kind_map = {
            "fact": "fact",
            "事实": "fact",
            "已发生": "fact",
            "foreshadow": "foreshadow",
            "伏笔": "foreshadow",
            "待回收": "foreshadow",
            "relation": "relation",
            "关系": "relation",
        }
        # kind=resolved：这条伏笔已经兑现了，标掉旧的那条
        if kind_text in {"resolved", "已回收", "回收"}:
            hit = set_resolved(save, body, resolved=True)
            if hit is not None:
                return f"已把记忆标记为「已回收」：{hit.text}"
            kind_text = "fact"
        real_kind = kind_map.get(kind_text, "fact")
        memory = add_memory(
            save,
            body,
            kind=real_kind,
            round=current_round(save),
            source="dm",
        )
        if memory is None:
            return "记忆内容为空，未记录"
        label = memory.label
        extra = f"（共 {len(list_memories(save))}/{MEMORY_MAX} 条）"
        return f"已记下【{label}】{extra}：{memory.text}"


class RecallTool(AgentTool):
    """按关键词检索剧情记忆（按需调用，平时零开销）。"""

    function_name = "recall"
    description = "查剧情记忆"
    args = (
        ToolArg("topic", "str", "关键词；留空取最近的"),
        ToolArg("limit", "int", "最多几条（默认 5）"),
    )

    @staticmethod
    async def call(
        context: AgentContext, topic: Any = "", limit: Any = 5, **kwargs: Any
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，查不到剧情记忆"
        count = _parse_int(limit, 5)
        count = max(1, min(count, MEMORY_MAX))
        hits = recall_memories(save, str(topic or ""), limit=count)
        if not hits:
            return "还没有任何剧情记忆"
        lines = [item.line() for item in hits]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #


class NoteEventTool(AgentTool):
    """记一条线索；事件进行中写局面名即结算该节点，写「新局面 {json}」现场造节点。"""

    function_name = "note_event"
    description = (
        "记一条线索或物件名；事件里写局面名即结算、写「新局面 {json}」现场造节点"
        "接进图（过校验才接），事实由程序发"
    )
    args = (
        ToolArg(
            "clue",
            "str",
            '线索或局面名；或 新局面 {"title":"…","grants":["…"]}'
            "（at/kind/requires/cost 可选）；「失败：」前缀=谈崩",
        ),
    )

    @staticmethod
    async def call(context: AgentContext, clue: Any = "", **kwargs: Any) -> str:
        from ...dnd.event_progress import _load, note_clue

        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档"
        body = str(clue or "").strip()
        if not body:
            return "线索内容为空"

        # 现场造节点：写「新局面 {json}」→ 过校验才接进图。
        # **要在剥「失败：」之前判**：造节点的 clue 不是局面名，剥了前缀反而认不出。
        from .event_flow import grow_graph

        grown = grow_graph(save, body)
        if grown is not None:
            return grown

        # 事件节点结算：前缀「失败：」= 谈崩（走 on_fail，失败也前进）
        ok = True
        ref = body
        if ref.startswith(("失败：", "失败:")):
            ok = False
            ref = ref[3:].strip()
        from .event_flow import current_slot, load_pool, settle_node

        # 结算前先记下正在跑的是哪一件——结算可能顺手把它关掉。
        slot_before = current_slot(load_pool(save))
        slot_id = slot_before.id if slot_before is not None else ""
        settled = settle_node(save, ref, ok=ok)
        if settled is not None:
            if slot_id:
                after = load_pool(save).slots.get(slot_id)
                if after is not None and not after.open:
                    # 这件事了结了：走统一收尾（推刻度 + 推进化轨 + 判纪元），
                    # 别在这里另写一套——两条路各算一份，纪元与难度轨就对不上了。
                    try:
                        from .event_flow import finish_event
                        from .gen_epoch import epithet_agent

                        lines = await finish_event(
                            save, after, note=ref, agent=epithet_agent
                        )
                        if lines:
                            settled = f"{settled}\n\n" + "\n".join(lines)
                    except Exception as e:  # 纪元结算是增强，不该吞掉局面的结算结果
                        log.warning(
                            f"事件了结后的纪元结算失败（忽略）：{str_traceback(e)}"
                        )
            return settled
        if not ok:
            return "当前没有事件在进行，「失败：」前缀用不上——这条没有记录。"
        note_clue(save, body)
        state = _load(save)
        tail = ""
        if state.current:
            tail = (
                f"；眼下这件事还开着 {len(state.open_nodes)} 个局面，"
                f"已经了结 {len(state.done_nodes)} 个"
            )
        return f"已记下线索「{body}」{tail}"


# --------------------------------------------------------------------------- #
# 结档
# --------------------------------------------------------------------------- #


class WrapUpTool(AgentTool):
    """为这一局写下结局并结档（只有玩家先 /跑团.结档 才允许）。"""

    function_name = "wrap_up"
    description = (
        "写结局并结档（保留存档与复盘）。**只有玩家 /跑团.结档 之后才可调用**，"
        "否则会被打回；不要与 narrate 同批（先 wrap_up，下一轮再 narrate 结局）"
    )
    args = (ToolArg("note", "str", "结局一句话（可选）"),)

    @staticmethod
    async def call(context: AgentContext, note: Any = "", **kwargs: Any) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法结档"
        if not save.progress.get("wrapping_up"):
            return (
                "玩家还没有决定结档，系统不允许你自行结束这一局。"
                "继续主持，并可以在叙述里提醒玩家：想收尾就发送 /跑团.结档。"
            )
        context.data["wrap_up"] = True
        tail = f"（{str(note).strip()}）" if str(note or "").strip() else ""
        return (
            f"🏁 已记录结档{tail}。这一轮的叙述就是本局的收束：请把伏笔收拢、给每个人"
            "一个交代，写出像样的结局（存档与复盘都会保留），下一轮不再继续。"
        )


#: DM 主持可用的全部功能
DM_TOOLS: tuple[type[AgentTool], ...] = (
    RollDiceTool,
    SecretRollTool,
    CheckTool,
    GetRuleTool,
    QueryDataTool,
    ReadSceneTool,
    UpdateCharacterTool,
    MoveSceneTool,
    UpdateSceneTool,
    StartCombatTool,
    MoveTool,
    AttackTool,
    CastSpellTool,
    UpdateNpcTool,
    HealTool,
    EndCombatTool,
    RestTool,
    RememberTool,
    RecallTool,
    NoteEventTool,
    AwaitPlayersTool,
    WrapUpTool,
    NarrateTool,
)
