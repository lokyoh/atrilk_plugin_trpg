"""NPC 态度 / 忠诚 / 声望的存档 IO 与 ``tactic`` 子命令编排（agents 层）。

``core/dnd/relations.py`` 是纯规则层，不碰磁盘；这里把 ``RelationsState`` 落到
**该存档目录下** 的 ``relations.json``（与 ``combat.json`` / ``chase.json`` /
``journey.json`` 同级），再把 DM 写进 ``tactic(action=关系 …)`` 的子命令翻译成状态机调用。

边界：**都是可选规则**。系统只记账与掷骰，「该不该给声望」「NPC 是否觉得
被虐待」由 DM 裁定；忠诚值私下记，回执只有 DM 看得到，态度变化与收益必须
显式写进叙述。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...dnd.relations import (
    NpcRelation,
    RelationsState,
    apply_loyalty,
    apply_reputation,
    format_relations_rules,
    format_relations_state,
    initial_loyalty,
    loyalty_event,
    loyalty_max,
    loyalty_tier,
    major_reputation_options,
    mutiny_check,
    normalize_attitude,
    reputation_benefits,
    reputation_gain,
    roll_initial_attitude,
    teamwork_days,
)
from ...dnd.save_store import get_character, save_dir

__all__ = [
    "clear_relations",
    "do_relations",
    "load_relations",
    "relations_help",
    "save_relations",
]

RELATIONS_FILENAME = "relations.json"

_NOTE = "⚠️ 回执只有 DM 看得到：态度变化、忠诚与声望收益要显式写进叙述。"

_COMMANDS = (
    "帮助",
    "状态",
    "登记",
    "态度",
    "忠诚",
    "声望",
    "初始",
    "哗变",
    "规则",
    "删除",
    "清空",
)

_SPLIT = re.compile(r"[\s,，;；/]+")


def relations_path(save) -> Path:
    return save_dir(save.save_id) / RELATIONS_FILENAME


def load_relations(save) -> RelationsState:
    """读关系账本；没有 / 坏了就给一份空的（不静默编数据）。"""
    path = relations_path(save)
    if not path.is_file():
        return RelationsState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RelationsState()
    if not isinstance(data, dict):
        return RelationsState()
    return RelationsState.from_dict(data)


def save_relations(save, state: RelationsState) -> Path:
    """原子写回关系账本。"""
    path = relations_path(save)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def clear_relations(save) -> bool:
    path = relations_path(save)
    if not path.is_file():
        return False
    path.unlink()
    return True


def relations_help() -> str:
    return (
        "【关系】子命令（写在 tactic 的 action 里）：\n"
        "· 关系 状态 —— 看 NPC 态度 / 忠诚与团体声望（私下记）\n"
        "· 关系 登记 <名字> [友好/冷漠/敌对] [魅力 16] [目标 …] —— 建一条记录；"
        "不写魅力就取队伍角色卡的最高魅力\n"
        "· 关系 态度 <名字> <友好/冷漠/敌对> —— 改态度\n"
        "· 关系 忠诚 <名字> <目标/优待/违背/虐待> —— 掷 1d4 / 2d4 增减；"
        "也可写「设 12」「重算 16」（最高魅力变了）\n"
        "· 关系 声望 <团体> <推进/指定任务/重大任务 [3|4]/建设/建设完成/设 N/-N> —— "
        "按原文增减，声望不低于 0\n"
        "· 关系 初始 [普通/掠食者/旅人/热心] [名字] —— 掷初始态度，带名字就写回账本\n"
        "· 关系 哗变 <归零数>/<总数> [停泊] —— 判定船员哗变\n"
        "· 关系 规则 / 删除 <名字> / 清空；"
        "query_data(table=关系, name=忠诚/声望/态度/初始 热心/等级 11) 查明文规则"
    )


def _max_charisma(save) -> tuple[int, str]:
    """队伍最高魅力：优先从存档的所有角色卡读，读不到返回 0 让 DM 显式给。"""
    scores: list[int] = []
    for user_id in save.player_ids:
        card = get_character(save, user_id)
        if card is None:
            continue
        try:
            scores.append(int(card.abilities["cha"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not scores:
        return 0, "没读到角色卡"
    return max(scores), "角色卡"


def _int(word: str) -> int | None:
    try:
        return int(str(word).strip())
    except (TypeError, ValueError):
        return None


def _need_npc(state: RelationsState, name: str) -> tuple[NpcRelation | None, str]:
    rel = state.npcs.get(name)
    if rel is None:
        return None, f"账本上没有「{name}」。先写「关系 登记 {name}」。"
    return rel, ""


def do_relations(
    save,
    action_text: str,
    *,
    player: str = "",
    target: str = "",
    trigger: str = "",
    rng: Any = None,
) -> str:
    """把 ``tactic(action=关系 / 忠诚 / 声望 / 态度 …)`` 翻成状态机调用。"""
    text = str(action_text or "").strip()
    if text.startswith("关系"):
        text = text[2:].strip()
    if not text or text in ("帮助", "?"):
        return relations_help()

    cmd = next((c for c in _COMMANDS if text.startswith(c)), "")
    if not cmd:
        return f"没认出关系子命令「{text}」。\n{relations_help()}"
    rest = text[len(cmd) :].strip()
    words = [w for w in _SPLIT.split(" ".join(x for x in (rest, str(target or "")) if x)) if w]
    state = load_relations(save)

    if cmd == "规则":
        return format_relations_rules(rest)
    if cmd == "状态":
        return format_relations_state(state)
    if cmd == "清空":
        return (
            "已清空关系账本。" if clear_relations(save) else "本来就没有关系账本。"
        ) + "\n" + _NOTE

    if cmd == "登记":
        if not words:
            return "登记要写 NPC 名字，如「关系 登记 矿工头 冷漠」。"
        name = words[0]
        if name in state.npcs:
            return f"「{name}」已经在账本里了。改态度用「关系 态度 {name} 友好」。"
        attitude = next(
            (a for w in words[1:] if (a := normalize_attitude(w))), "冷漠"
        )
        charisma = 0
        source = ""
        for index, word in enumerate(words):
            if word == "魅力" and index + 1 < len(words):
                charisma = _int(words[index + 1]) or 0
                source = "DM 指定"
                break
        if not charisma:
            charisma, source = _max_charisma(save)
        if not charisma:
            return (
                "读不到队伍里任何角色卡的魅力值，没法算最高忠诚值。"
                "请显式写「魅力 16」，例如「关系 登记 矿工头 魅力 16」。"
            )
        goal = ""
        if "目标" in words:
            index = words.index("目标")
            goal = " ".join(words[index + 1 :])
        maximum = loyalty_max(charisma)
        state.npcs[name] = NpcRelation(
            name=name,
            attitude=attitude,
            loyalty=initial_loyalty(charisma),
            loyalty_max=maximum,
            goal=goal,
        )
        save_relations(save, state)
        return (
            f"登记【{name}】：态度 {attitude}，忠诚 "
            f"{state.npcs[name].loyalty}/{maximum}"
            f"（最高魅力 {charisma}，来源 {source}；起始取一半、向下取整）。\n{_NOTE}"
        )

    if cmd == "态度":
        if len(words) < 2:
            return "写「关系 态度 <名字> <友好/冷漠/敌对>」。"
        rel, error = _need_npc(state, words[0])
        if error:
            return error
        attitude = normalize_attitude(words[1])
        if attitude is None:
            return f"没认出态度「{words[1]}」，只有友好 / 冷漠 / 敌对。"
        old = rel.attitude
        rel.attitude = attitude
        save_relations(save, state)
        return f"【{rel.name}】态度 {old} → **{attitude}**。\n{_NOTE}"

    if cmd == "忠诚":
        if len(words) < 2:
            return "写「关系 忠诚 <名字> <目标/优待/违背/虐待/设 N/重算 N>」。"
        rel, error = _need_npc(state, words[0])
        if error:
            return error
        op = words[1]
        if op == "重算":
            new_max = _int(words[2]) if len(words) > 2 else None
            if new_max is None:
                return "重算要写新的最高魅力值，如「关系 忠诚 矿工头 重算 18」。"
            rel.loyalty_max = loyalty_max(new_max)
            rel.loyalty = apply_loyalty(rel.loyalty, 0, rel.loyalty_max)
        elif op == "设":
            value = _int(words[2]) if len(words) > 2 else None
            if value is None:
                return "设值要写数字，如「关系 忠诚 矿工头 设 12」。"
            rel.loyalty = apply_loyalty(0, value, rel.loyalty_max)
        else:
            event = loyalty_event(op, rng=rng)
            if event is None:
                return f"没认出忠诚事件「{op}」。可用：目标 / 优待 / 违背 / 虐待。"
            delta, note = event
            before = rel.loyalty
            rel.loyalty = apply_loyalty(before, delta, rel.loyalty_max)
            save_relations(save, state)
            tier = loyalty_tier(rel.loyalty)
            return (
                f"【{rel.name}】{note} → 掷出 {delta:+d}，忠诚 "
                f"{before} → **{rel.loyalty}/{rel.loyalty_max}**（{tier['cn']}）。"
                f"\n{tier['text']}\n{_NOTE}"
            )
        save_relations(save, state)
        return (
            f"【{rel.name}】忠诚现为 {rel.loyalty}/{rel.loyalty_max}"
            f"（{loyalty_tier(rel.loyalty)['cn']}）。\n{_NOTE}"
        )

    if cmd == "声望":
        if len(words) < 2:
            return "写「关系 声望 <团体> <推进/指定任务/重大任务/建设/设 N/-N>」。"
        group = words[0]
        op = words[1]
        current = state.reputation.get(group, 0)
        if op == "建设":
            days = teamwork_days(current)
            if days is None:
                return f"【{group}】声望 0：原文要先到 1+ 才能靠团队建设涨声望。"
            return (
                f"【{group}】当前声望 {current}，按原文需要花 **{days} 天**"
                "（当前声望 × 10）做团体次要任务与社交。过了这么多天再写"
                f"「关系 声望 {group} 建设完成」才 +1；系统不替你跳过时间。\n{_NOTE}"
            )
        if op == "建设完成":
            delta = 1
        elif op == "设":
            value = _int(words[2]) if len(words) > 2 else None
            if value is None:
                return "设值要写数字，如「关系 声望 议会 设 6」。"
            delta = value - current
        elif op.startswith("-") and _int(op) is not None:
            delta = _int(op) or 0
        elif op == "重大任务":
            low, high = major_reputation_options()
            delta = _int(words[2]) if len(words) > 2 else low
            if delta not in (low, high):
                return f"重大任务只能 +{low} 或 +{high}（DM 选），写「重大任务 4」即 +4。"
        else:
            delta = reputation_gain(op)
            if delta is None:
                return (
                    f"没认出声望操作「{op}」。可用：推进 / 指定任务 / 重大任务 / "
                    "建设 / 建设完成 / 设 N / -N。"
                )
        value = apply_reputation(current, delta)
        state.reputation[group] = value
        save_relations(save, state)
        benefits = reputation_benefits(value)
        return (
            f"【{group}】声望 {current} → **{value}**（{delta:+d}）。"
            + ("\n" + "\n".join(benefits) if benefits else "")
            + f"\n{_NOTE}"
        )

    if cmd == "初始":
        variant = words[0] if words else "普通"
        result = roll_initial_attitude(variant, rng=rng)
        if result is None:
            return (
                f"没认出初始态度变体「{variant}」。可用：普通 / 掠食者 / 旅人 / 热心。"
            )
        if len(words) > 1 and words[1] in state.npcs:
            state.npcs[words[1]].attitude = result.attitude
            save_relations(save, state)
            return (
                f"【{words[1]}】初始态度掷出 **{result.attitude}**"
                f"（{result.variant} {result.die_total} → {result.modified}），已写入账本。\n{_NOTE}"
            )
        return (
            f"【初始态度】{result.variant} 掷 {result.die_total} → {result.modified}"
            f"：**{result.attitude}**。{result.note}\n"
            "（只掷不改账本；想写进某位 NPC 就在后面加名字。）"
        )

    if cmd == "哗变":
        numbers = [int(n) for n in re.findall(r"\d+", " ".join(words))]
        if len(numbers) < 2:
            return "写「关系 哗变 3/8」（3 人忠诚归零、共 8 名船员），可加「停泊」。"
        verdict = mutiny_check(
            numbers[0], numbers[1], docked="停泊" in " ".join(words)
        )
        return f"【船员】{verdict}\n{_NOTE}"

    if cmd == "删除":
        if not words or words[0] not in state.npcs:
            return "删除要写账本上已有的 NPC 名字。"
        del state.npcs[words[0]]
        save_relations(save, state)
        return f"已从账本删掉「{words[0]}」。\n{_NOTE}"

    return relations_help()
