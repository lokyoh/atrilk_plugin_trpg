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

from ...dnd.attributes import ABILITIES, get_ability
from ...dnd.card import DndCharacter
from ...dnd.combat import (
    apply_damage as combat_damage,
)
from ...dnd.combat import (
    attack as combat_attack,
)
from ...dnd.combat import (
    clear_combat,
    combat_text,
    effective_ac,
    enemy_attack,
    find_combatant,
    heal_combatant,
    load_combat,
    parse_enemy_list,
    party_level,
    resource_of,
    roll_damage,
    start_combat,
    update_enemy,
    use_resource,
)
from ...dnd.equipment import ARMORS, SHIELDS, WEAPONS, get_armor, get_weapon
from ...dnd.monsters import MONSTERS, get_monster, monster_names
from ...dnd.npcs import (
    NPC_TIERS,
    NpcRecord,
    build_npc,
    find_npc,
    get_tier,
    npc_monster,
    npcs_text,
    resolve_monster,
    tier_names,
    update_npc,
    upsert_npc,
)
from ...dnd.profession import CLASSES, get_class
from ...dnd.race import RACES, get_race
from ...dnd.save_store import (
    append_scene_change,
    find_character,
    get_character,
    load_round,
    load_save,
    save_round,
    set_character,
)
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
    get_story,
    list_docs,
    list_stories,
    load_doc,
    read_doc,
)
from ...trpg.check import roll_check
from ...trpg.dice import Advantage, DiceError, roll
from ..thinking import AgentContext, AgentFinish, AgentTool, ToolArg
from .rules import match_rules

__all__ = [
    "DM_TOOLS",
    "AttackTool",
    "AwaitPlayersTool",
    "CastSpellTool",
    "CheckTool",
    "EndCombatTool",
    "EnemyAttackTool",
    "GetRuleTool",
    "MoveSceneTool",
    "NarrateTool",
    "QueryDataTool",
    "RestTool",
    "RollDiceTool",
    "StartCombatTool",
    "UpdateCharacterTool",
    "UpdateEnemyTool",
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
    """把「中毒、倒地」这类写法拆成状态列表。"""
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


def _resolve_check(card, kind: str, dc: int, advantage: Advantage):
    """把检定对象解析成技能 / 属性 / 豁免并执行。"""
    text = str(kind or "").strip()
    if not text:
        raise KeyError("（未提供检定对象）")
    is_save = "豁免" in text
    name = text.replace("豁免", "").strip()

    for ability in ABILITIES:
        if name in (ability.cn, ability.key, ability.abbr.lower(), ability.en.lower()):
            if is_save:
                return card.check_save(ability, dc, advantage=advantage)
            return card.check_ability(ability, dc, advantage=advantage)
    if not is_save:
        for skill in SKILLS:
            if name in (skill.cn, skill.key, skill.en.lower()):
                return card.check_skill(skill, dc, advantage=advantage)
    raise KeyError(text)


class CheckTool(AgentTool):
    """用玩家角色卡做检定。"""

    function_name = "check"
    description = "用玩家角色卡进行技能 / 属性 / 豁免检定，返回成败"
    args = (
        ToolArg(
            "kind",
            "str",
            "检定对象：技能名（如 察觉、隐匿）或属性名（如 力量），豁免写作「力量豁免」",
        ),
        ToolArg("dc", "int", "难度值，默认 15"),
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
        dc: Any = 15,
        advantage: Any = "普通",
        player: str = "",
        **kwargs: Any,
    ) -> str:
        card, _save = _load_character(context, player)
        if card is None:
            return "没找到该玩家的角色卡，无法检定"
        try:
            result = _resolve_check(
                card, kind, _parse_int(dc, 15), _parse_advantage(advantage)
            )
        except KeyError as e:
            return f"无法识别检定对象：{e}"
        return f"【{card.name}】{result.describe()}"


# --------------------------------------------------------------------------- #
# 规则 / 数据查询
# --------------------------------------------------------------------------- #


class GetRuleTool(AgentTool):
    """查询规则条目。"""

    function_name = "get_rule"
    description = "查询 DND 5e 规则条目，输入主题关键词返回相关规则原文"
    args = (
        ToolArg(
            "topic",
            "str",
            "规则主题关键词，如 优势、死亡豁免、法术位、先攻、暴击",
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


def _query_weapon(name: str) -> str:
    if not name:
        return "武器：" + "、".join(w.cn for w in WEAPONS)
    weapon = get_weapon(name)
    if weapon is None:
        raise KeyError(f"未知武器：{name}")
    lines = [f"{weapon.cn}（{weapon.category.value}）"]
    lines.append(f"伤害：{weapon.damage} {weapon.damage_type}")
    if weapon.properties:
        lines.append("属性：" + "、".join(weapon.properties))
    if weapon.is_ranged:
        lines.append(f"射程：{weapon.ranged_normal}/{weapon.ranged_max} 尺")
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


def _query_monster(name: str) -> str:
    if not name:
        lines = [f"怪物（共 {len(MONSTERS)} 只，按 CR 升序）："]
        for monster in sorted(MONSTERS, key=lambda item: item.cr):
            first = monster.attacks[0] if monster.attacks else None
            attack = (
                f"{first.name} {monster.attack_bonus(first):+d}/{first.damage}"
                if first
                else "—"
            )
            lines.append(
                f"· {monster.cn}（CR {monster.cr_text}）AC {monster.ac}  "
                f"HP {monster.hp}  攻击 {attack}"
            )
        return "\n".join(lines)
    monster = get_monster(name)
    if monster is None:
        raise KeyError(f"未知怪物：{name}")
    return monster.stat_block()


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
        lines = ["人形 NPC 档位（写法「名字:档位」，数值按队伍等级现算）："]
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
            lines.append("· " + monster.summary())
    lines.append("别名：" + "、".join(tier.aliases or ("（无）",)))
    return "\n".join(lines)


#: 数据表名（含中文别名）-> 查询函数
_DATA_TABLES = {
    "race": _query_race,
    "profession": _query_profession,
    "skill": _query_skill,
    "weapon": _query_weapon,
    "armor": _query_armor,
    "monster": _query_monster,
    "spell": _query_spell,
    "npc_tier": _query_npc_tier,
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
    "敌人": "monster",
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
    # —— 全局世界库文档 ——
    "world": "world",
    "世界": "world",
    "世界地图": "world",
    "story": "story",
    "剧情": "story",
    "area": "area",
    "区域": "area",
    "city": "city",
    "城镇": "city",
    "城市": "city",
    "place": "place",
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

    if kind == "story":
        # 故事的键是自动生成的 ID，按「键或标题」展示与查找更友好。
        stories = list_stories(world_key)
        if not name:
            if not stories:
                return "这个世界里还没有故事文档"
            return "故事文档：" + "；".join(
                f"{s.title}" + (f"（{s.intro}）" if s.intro else "") for s in stories
            )
        story = get_story(world_key, name)
        if story is None:
            return f"没有找到故事「{name}」。现有：" + (
                "、".join(s.title for s in stories) or "（空）"
            )
        text = load_doc(world_key, "story", story.key)
        return text or f"故事「{story.title}」的文档是空的"

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
    description = (
        "查询 DND 静态数据表（种族 / 职业 / 技能 / 武器 / 护甲 / 怪物 / 法术 / NPC 档位 / "
        "本局 NPC 名单）或全局世界库文档（世界 / 剧情 / 区域 / 城镇 / 地点 / 地下城 / 场景）"
    )
    args = (
        ToolArg(
            "table",
            "str",
            "数据表 race/profession/skill/weapon/armor/monster/spell/npc_tier/npc（可用中文），"
            "或世界库 world/story/area/city/place/dungeon/scene（可用中文）",
        ),
        ToolArg("name", "str", "条目 / 文档名；留空则列出该表或该类文档的全部名称"),
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
                "spell / npc_tier / npc，或世界库文档："
                "world / story / area / city / place / dungeon / scene"
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
    description = (
        "修改存档里玩家角色卡的字段并落盘"
        "（生命值、状态、死亡豁免、经验、等级、姓名、备注、特性、标签、法术）"
    )
    args = (
        ToolArg(
            "field",
            "str",
            "字段：hp / temp_hp / conditions / 死亡豁免 / exhaustion / exp / level / name / "
            "notes / features / tags / spells",
        ),
        ToolArg(
            "value",
            "str",
            "新值；hp 可写「满血」；conditions 用、分隔（也支持「清空」）；features、tags 追加一条；"
            "spells 用、分隔（覆盖整张法术表，写「清空」则清空）；"
            "死亡豁免可写 清空 / 复活 / 稳定 / 死亡 / 成功N / 失败N，其余覆盖",
        ),
        ToolArg(
            "player",
            "str",
            "改哪名玩家的卡：角色名或 user_id；只有一名玩家时可留空",
        ),
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
                card.conditions = _split_conditions(text)
                message = "状态已设为：" + "、".join(card.conditions)
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
            card.exp = _parse_int(text, card.exp)
            message = f"经验值已设为 {card.exp}"
        elif field == "level":
            card.level = max(1, _parse_int(text, card.level))
            message = f"等级已设为 {card.level}"
        elif field == "name":
            card.name = text
            message = f"姓名已设为 {card.name or '（空）'}"
        elif field == "notes":
            card.notes = text
            message = "备注已更新"
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
        else:
            return (
                "不支持的字段，可用：hp / temp_hp / conditions / 死亡豁免 / exhaustion / "
                "exp / level / name / notes / features / tags / spells"
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
    description = (
        "声明本轮结束后，下一轮要等待哪些玩家提交行动（由你决定，例如只等在场的玩家）；"
        "不调用则沿用上一轮的名单，开局默认等待所有玩家"
    )
    args = (
        ToolArg(
            "players",
            "str",
            "要等待的玩家，用、分隔，写角色名或 user_id；「全员」= 所有玩家；「无」= 谁先提交就立刻主持",
        ),
    )

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
        "把主持叙述发给玩家并结束本次思考；必须调用且放在最后。"
        "**必须单独一轮调用**：叙述里要用到检定 / 掷骰 / 攻击等功能的结果时，"
        "先调那些功能、等返回后再单独调用本功能；与其它功能同批调用时叙述不会发送，"
        "你会被要求结合结果重写"
    )
    args = (ToolArg("text", "str", "要发给玩家的主持叙述正文"),)

    @staticmethod
    async def call(context: AgentContext, text: Any = "", **kwargs: Any) -> str:
        raise AgentFinish(str(text or "").strip())


class MoveSceneTool(AgentTool):
    """切换场景（缺什么生成什么），并构建出本局专属的场景副本存进存档。"""

    function_name = "move_scene"
    description = (
        "让玩家切换到一个地点 / 场景；该地点还没有生成时自动生成通用场景"
        "（区域 → 城镇据点 / 自然地点 / 地下城 → 通用场景），再结合本局剧情构建出"
        "本局专属的场景副本存进存档。**这是剧情事件**：返回后你必须在叙述中交代"
        "玩家如何离场与入场，使其合理"
    )
    args = (
        ToolArg("location", "str", "地点 / 场景名，如「白港」「霜牙地窟」「北境」"),
        ToolArg("kind", "str", "可选：城镇据点 / 自然地点 / 地下城；不确定就留空"),
        ToolArg("region", "str", "可选：所在区域名，用于定位；不确定就留空"),
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

        result = await open_scene(save, name, kind=kind, region=region)
        if result is None:
            return (
                f"无法定位地点「{name}」，且没能自动生成。"
                "请换一个更明确的名称，或补上所在区域（region）与类型（kind）。"
            )
        key, text = result
        return (
            f"已切换到场景「{key}」。\n"
            "注意：切换场景是剧情事件，必须由你的叙述来合理化——请在接下来的叙述里"
            "先用一两句交代玩家**如何离开上一处（离场）**，再描述**如何抵达这里（入场）**，"
            "让这次移动自然合理，然后给出新场景的第一印象与 2~3 个可回应的切入点。\n\n"
            f"【{key}】\n{text}"
        )


class UpdateSceneTool(AgentTool):
    """在当前场景的存档副本文末追加一条变化记录。"""

    function_name = "update_scene"
    description = (
        "在当前场景的存档副本文末追加一条变化记录"
        "（场景被破坏、NPC 死亡、物品被拿走、环境改变等）"
    )
    args = (
        ToolArg("note", "str", "变化内容，如「大门被玩家砸开」"),
        ToolArg("kind", "str", "可选变化类型，如 破坏 / 人物 / 物品 / 环境，默认 变化"),
    )

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
        return f"已在场景「{save.current_scene}」记录变化：{text}"


# --------------------------------------------------------------------------- #
# 战斗
# --------------------------------------------------------------------------- #


class StartCombatTool(AgentTool):
    """宣布开战：掷先攻、排顺序、开始逐回合结算。"""

    function_name = "start_combat"
    description = (
        "宣布开战：按你列的敌人清单掷先攻并排出先攻顺序；开战后由系统按顺序推进回合"
        "（轮到玩家时该玩家行动、轮到敌人时你用 enemy_attack 结算）"
    )
    args = (
        ToolArg(
            "enemies",
            "str",
            "敌人清单，如「地精x3、大地精」；数量写法 地精x3 / 3只地精 / 地精 3；"
            "剧情里的具名 NPC 写「名字:档位」或「名字:怪物模板」，如「黑心商人卡尔:老兵、打手x2:士兵」",
        ),
        ToolArg("note", "str", "起因 / 环境（可选），如「在酒馆后巷伏击」"),
    )

    @staticmethod
    async def call(
        context: AgentContext, enemies: Any = "", note: Any = "", **kwargs: Any
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

        state = start_combat(
            save, specs, current=str(context.user_id or ""), note=str(note or "")
        )
        context.data["combat_started"] = True

        lines = ["⚔️ 战斗开始，先攻已掷好。"]
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
            "按上面的顺序叙述：标记「现在」的那位就是本轮该行动的玩家。"
            "玩家攻击用 attack、施法用 cast_spell，敌人反击用 enemy_attack"
            "（都由系统掷命中与伤害）；不要替还没轮到的玩家决定行动。"
        )
        return "\n".join(lines)


class AttackTool(AgentTool):
    """玩家攻击：自动掷命中、掷伤害并扣敌人生命。"""

    function_name = "attack"
    description = (
        "玩家攻击敌人：按角色卡的武器与攻击加值掷命中，命中则掷伤害并扣敌人生命"
        "（武器默认取角色卡里第一件，也可指定；指定了不在装备里的武器会提醒你）"
    )
    args = (
        ToolArg("target", "str", "攻击目标，如 地精2 / 地精"),
        ToolArg("weapon", "str", "使用的武器（可选，默认用角色卡里的第一件武器）"),
        ToolArg("player", "str", "谁在攻击：角色名或 user_id；只有一名玩家时可留空"),
        ToolArg("advantage", "str", "普通 / 优势 / 劣势，默认普通"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        target: Any = "",
        weapon: Any = "",
        player: Any = "",
        advantage: Any = "普通",
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
        )
        # 行动经济（软提示）：真正掷过攻击才记一次「动作」；超支只提醒，不拦截。
        if f"【{card.name}】" in text:
            note = use_resource(save, state, card.user_id, "action")
            if note:
                text += f"\n{note}"
        return text


class EnemyAttackTool(AgentTool):
    """敌人攻击玩家：按怪物数据自动结算。"""

    function_name = "enemy_attack"
    description = (
        "敌人攻击玩家：按怪物数据的攻击加值掷命中，命中则掷伤害并扣该玩家的生命"
    )
    args = (
        ToolArg("enemy", "str", "发动攻击的敌人，如 地精1"),
        ToolArg("target", "str", "被攻击的玩家：角色名或 user_id"),
        ToolArg("attack_name", "str", "用哪项攻击（可选，默认第一项），如 短弓 / 爪击"),
        ToolArg("advantage", "str", "普通 / 优势 / 劣势，默认普通"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        enemy: Any = "",
        target: Any = "",
        attack_name: Any = "",
        advantage: Any = "普通",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法结算攻击"
        state = load_combat(save)
        if state is None:
            return "现在不在战斗中，先用 start_combat 开战"
        text, _hit = enemy_attack(
            save,
            state,
            str(enemy or ""),
            str(target or ""),
            str(attack_name or ""),
            advantage=_parse_advantage(advantage),
        )
        return text


class UpdateEnemyTool(AgentTool):
    """直接改敌人状态（陷阱、环境、逃跑、投降、被擒……）。"""

    function_name = "update_enemy"
    description = (
        "直接改某个敌人的状态：生命值增减 / 设定、退场（倒地、逃跑、被擒）、附注；"
        "用于陷阱、环境伤害、投降等不走攻击判定的情况"
    )
    args = (
        ToolArg("name", "str", "敌人名，如 地精1"),
        ToolArg("hp", "str", "生命值：新值（12）或增减（-5 / +8），留空不改"),
        ToolArg("out", "str", "是否退场：是 / 否，留空不改"),
        ToolArg("note", "str", "附注（可选），如「被推下悬崖」"),
    )

    @staticmethod
    async def call(
        context: AgentContext,
        name: Any = "",
        hp: Any = "",
        out: Any = "",
        note: Any = "",
        **kwargs: Any,
    ) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法修改敌人状态"
        state = load_combat(save)
        if state is None:
            return "现在不在战斗中，无需修改敌人状态"
        return update_enemy(
            save,
            state,
            str(name or ""),
            hp=str(hp or ""),
            out=str(out or ""),
            note=str(note or ""),
        )


class EndCombatTool(AgentTool):
    """结束当前战斗。"""

    function_name = "end_combat"
    description = (
        "结束当前战斗（敌人全灭 / 玩家撤退 / 谈判成功），清掉先攻顺序并回到普通推进"
    )
    args = (ToolArg("result", "str", "战斗结果（可选），如「敌人溃散」「双方议和」"),)

    @staticmethod
    async def call(context: AgentContext, result: Any = "", **kwargs: Any) -> str:
        save = load_save(context.save_id) if context.save_id else None
        if save is None:
            return "当前没有存档，无法结束战斗"
        if load_combat(save) is None:
            return "现在不在战斗中"
        clear_combat(save)
        state = load_round(save)
        state.waiting = None
        state.actions = {}
        save_round(save, state)
        tail = f"（{result}）" if str(result or "").strip() else ""
        return (
            f"⚔️ 战斗结束{tail}，先攻顺序已清空。"
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


def _target_ac(save, state, target: Any) -> int:
    if isinstance(target, DndCharacter):
        return target.armor_class
    return effective_ac(save, target)


def _target_save_bonus(save, target: Any, ability: str) -> int:
    """目标的某项豁免加值（敌人查怪物数据 / NPC 模板）。"""
    if isinstance(target, DndCharacter):
        return target.save_bonus(ability)
    if target.kind == "player":
        card = get_character(save, target.cid)
        return card.save_bonus(ability) if card is not None else 0
    monster = resolve_monster(target.monster)
    return monster.save_bonus(ability) if monster is not None else 0


def _target_down(save, target: Any) -> bool:
    if isinstance(target, DndCharacter):
        return target.hp <= 0
    if target.kind == "player":
        card = get_character(save, target.cid)
        return card is None or card.hp <= 0
    return target.out or target.hp <= 0


def _spell_damage(save, state, target: Any, amount: int) -> str:
    """对目标结算法术伤害（玩家写回角色卡，战斗外用角色卡）。"""
    amount = max(0, int(amount))
    if isinstance(target, DndCharacter):
        absorbed = min(target.temp_hp, amount)
        target.temp_hp -= absorbed
        target.current_hp = max(0, target.hp - (amount - absorbed))
        set_character(save, target)
        note = f"{target.name} 剩余 {target.hp}/{target.max_hp}"
        if absorbed:
            note += f"（临时生命吸收 {absorbed}）"
        if target.hp <= 0:
            note += "，已倒地"
        return note + "。"
    return combat_damage(save, state, target, amount)


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
    description = (
        "施法者施展一个法术：按规则自动结算法术攻击（对抗 AC）、目标豁免（法术豁免 DC）、"
        "治疗量，并扣除法术位（戏法不消耗；法术位不够会被打回）"
    )
    args = (
        ToolArg(
            "spell",
            "str",
            "法术名，如 火焰箭 / 魔法飞弹 / 火球术 / 治疗伤害（见 query_data 的 spell 表）",
        ),
        ToolArg(
            "target",
            "str",
            "目标：敌人名 / 玩家名，多个用、分隔（范围法术就把需要用豁免的人都写上）；留空默认自己",
        ),
        ToolArg("player", "str", "谁在施法：角色名或 user_id；只有一名玩家时可留空"),
        ToolArg(
            "slot",
            "str",
            "用第几环法术位施展（可选，默认法术本身环阶；升环会消耗更高环）",
        ),
        ToolArg("advantage", "str", "法术攻击用：普通 / 优势 / 劣势，默认普通"),
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

        warn = ""
        if not card.knows_spell(spell_obj):
            warn = f"（注意：{spell_obj.cn} 不在 {card.name} 的法术表里，若是卷轴 / 神赐请自行解释）\n"

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
            lines.append("（需专注：之后受伤害或分心时别忘了掷体质豁免维持）")

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
                "要直接扣血就用 update_enemy / update_character。"
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
            damage_expr = spell_obj.damage_expr(casting_mod)
            for item in targets:
                if _target_down(save, item):
                    lines.append(f"· {_target_name(item)} 已经倒下了，跳过。")
                    continue
                ac = _target_ac(save, state, item)
                result = card.check_spell_attack(
                    ac,
                    label=f"{spell_obj.cn}攻击",
                    advantage=_parse_advantage(advantage),
                )
                lines.append(f"· {_target_name(item)}：{result.describe()}（AC {ac}）")
                if not result.success:
                    lines.append("  　未命中。")
                    continue
                damage, detail = roll_damage(damage_expr, crit=result.nat20)
                crit_text = "（暴击，伤害骰翻倍）" if result.nat20 else ""
                lines.append(
                    f"  　命中！{detail} {spell_obj.damage_type_text}{crit_text}"
                )
                lines.append("  　" + _spell_damage(save, state, item, damage))
            return warn + "\n".join(lines)

        # —— 自动命中（如魔法飞弹 / 匕首之云）——
        if spell_obj.auto_damage:
            damage, detail = roll_damage(spell_obj.damage_expr(casting_mod))
            lines.append(f"自动命中：伤害 {detail} {spell_obj.damage_type_text}")
            for item in targets:
                if _target_down(save, item):
                    lines.append(f"· {_target_name(item)} 已经倒下了，跳过。")
                    continue
                lines.append(
                    f"· {_target_name(item)}："
                    + _spell_damage(save, state, item, damage)
                )
            return warn + "\n".join(lines)

        # —— 目标豁免 ——
        dc = card.spell_save_dc
        lines.append(f"法术豁免 DC {dc}（{spell_obj.save_text}）")
        damage = 0
        detail = ""
        if spell_obj.damage:
            damage, detail = roll_damage(spell_obj.damage_expr(casting_mod))
            lines.append(f"伤害 {detail} {spell_obj.damage_type_text}")
        for item in targets:
            if _target_down(save, item):
                lines.append(f"· {_target_name(item)} 已经倒下了，跳过。")
                continue
            bonus = _target_save_bonus(save, item, spell_obj.save)
            result = roll_check(bonus, dc, kind=f"{spell_obj.save_text}豁免")
            if not spell_obj.damage:
                lines.append(
                    f"· {_target_name(item)}：{result.describe()} → "
                    + (
                        "通过（法术未能奏效）"
                        if result.success
                        else "失败（请按法术效果叙述）"
                    )
                )
                continue
            if result.success:
                if spell_obj.half_on_save:
                    lines.append(
                        f"· {_target_name(item)}：{result.describe()} → 成功豁免，伤害减半"
                    )
                    lines.append("  　" + _spell_damage(save, state, item, damage // 2))
                else:
                    lines.append(
                        f"· {_target_name(item)}：{result.describe()} → 成功豁免，不受伤害"
                    )
            else:
                lines.append(
                    f"· {_target_name(item)}：{result.describe()} → 豁免失败，受到全额伤害"
                )
                lines.append("  　" + _spell_damage(save, state, item, damage))
        return warn + "\n".join(lines)


# --------------------------------------------------------------------------- #
# 休息 / NPC 名单
# --------------------------------------------------------------------------- #


class RestTool(AgentTool):
    """结算休息：长休回满生命与法术位，短休回满契约法术位。"""

    function_name = "rest"
    description = (
        "结算一次休息：长休 → 生命回满、法术位回满、临时生命清零、力竭 -1；"
        "短休 → 邪术师契约法术位回满（生命骰由你按规则裁决，可用 update_character 改 HP）"
    )
    args = (
        ToolArg("kind", "str", "长休 / 短休，默认长休"),
        ToolArg("player", "str", "为哪名玩家结算：角色名或 user_id；留空 = 全员"),
    )

    @staticmethod
    async def call(
        context: AgentContext, kind: Any = "长休", player: Any = "", **kwargs: Any
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
                if card.exhaustion:
                    card.exhaustion = max(0, card.exhaustion - 1)
                    notes.append(f"力竭降至 {card.exhaustion} 级")
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
                    else:
                        notes.append(
                            "生命骰消耗与治疗由你裁决（可用 update_character 改 HP）"
                        )
            set_character(save, card)
            lines.append(f"· {card.name or card.user_id}：" + "；".join(notes))
        if long_rest:
            lines.append("（中毒 / 疾病 / 诅咒等是否解除由你判断）")
        return "\n".join(lines)


class UpdateNpcTool(AgentTool):
    """维护本局 NPC 名单（关系、状态、旧伤、所在地）。"""

    function_name = "update_npc"
    description = (
        "维护本局 NPC 名单：记下具名 NPC 的关系、状态、所在地、当前生命（旧伤），"
        "或把还没登记过的 NPC 记进名单（要给模板，如 老兵 / 匪帮首领）"
    )
    args = (
        ToolArg("name", "str", "NPC 名字，如 黑心商人卡尔"),
        ToolArg(
            "template",
            "str",
            "档位或怪物模板（可选）：平民 / 士兵 / 老兵 / 首领，或 匪帮首领 / 骑士……",
        ),
        ToolArg("hp", "str", "当前生命值（可选）"),
        ToolArg(
            "status", "str", "状态（可选）：存活 / 受伤 / 倒下 / 逃跑 / 被俘 / 失踪……"
        ),
        ToolArg("note", "str", "备注（可选）：关系、身份、欠的账……"),
        ToolArg("place", "str", "最后出现的地点 / 场景（可选）"),
    )

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
            return f"已把 {npc_name} 记进 NPC 名单。\n  {record.brief()}"

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
        return "已更新：\n  " + updated.brief()


# --------------------------------------------------------------------------- #
# 结档
# --------------------------------------------------------------------------- #


class WrapUpTool(AgentTool):
    """为这一局写下结局并结档（只有玩家先 /跑团.结档 才允许）。"""

    function_name = "wrap_up"
    description = (
        "为本次跑团写下结局并结档（保留存档与复盘）。**只有玩家先发送 /跑团.结档** 之后"
        "才允许调用，否则会被系统打回；没收到结档状态时你只能提醒玩家可以结档。"
        "调用后本轮思考即告结束，**不要与 narrate 写在同一批**：先 wrap_up，"
        "下一轮再 narrate 结局"
    )
    args = (ToolArg("note", "str", "结局一句话（可选），如「众人带着宝藏回到白港」"),)

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
    CheckTool,
    GetRuleTool,
    QueryDataTool,
    UpdateCharacterTool,
    MoveSceneTool,
    UpdateSceneTool,
    StartCombatTool,
    AttackTool,
    CastSpellTool,
    EnemyAttackTool,
    UpdateEnemyTool,
    UpdateNpcTool,
    EndCombatTool,
    RestTool,
    AwaitPlayersTool,
    WrapUpTool,
    NarrateTool,
)
