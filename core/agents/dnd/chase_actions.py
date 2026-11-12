"""追逐的存档 IO 与 ``tactic`` 子命令编排（agents 层）。

``core/dnd/chase.py`` 是纯规则层，不碰磁盘；这里负责把 ``ChaseState``
落到 **该存档目录下** 的 ``chase.json``（与 ``combat.json`` 同一层），
再把 DM 写进 ``tactic(action=追逐 …)`` 的子命令翻译成状态机调用。

数值一律从角色卡 / 图鉴自动取，不让 DM 手填：程序算得出的绝不交给模型。
取不到（纯叙述 NPC）才退回默认值，并在回执里如实说明。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...dnd.chase import (
    HUNTER,
    PREY,
    ChaseParticipant,
    ChaseState,
    advance_turn,
    chase_help,
    escape_phase,
    format_chase,
    format_chase_rules,
    move_chase,
    roll_complication,
    start_chase,
    take_dash,
)
from ...dnd.monsters import get_monster
from ...dnd.save_store import find_character, save_dir
from ...trpg.dice import roll

__all__ = [
    "clear_chase",
    "do_chase",
    "load_chase",
    "save_chase",
]

CHASE_FILENAME = "chase.json"

#: 玩家可见性提醒（只在开 / 看账本时附一次，别刷每条回执）
_VISIBILITY_NOTE = "⚠️ 这份账本只有 DM 看得到：距离 / 力竭 / 逃脱要显式写进叙述，玩家才知道。"

_SIDE_WORDS = {
    "猎物": PREY,
    "被追捕者": PREY,
    "被追捕": PREY,
    "prey": PREY,
    "追击者": HUNTER,
    "猎手": HUNTER,
    "追兵": HUNTER,
    "hunter": HUNTER,
}
_SPLIT = re.compile(r"[,，;；、\s]+")


def chase_path(save) -> Path:
    return save_dir(save.save_id) / CHASE_FILENAME


def load_chase(save) -> ChaseState | None:
    """读追逐状态；没在追逐返回 ``None``。"""
    path = chase_path(save)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return ChaseState.from_dict(data)


def save_chase(save, state: ChaseState) -> Path:
    """原子写回追逐状态。"""
    path = chase_path(save)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def clear_chase(save) -> bool:
    path = chase_path(save)
    if not path.is_file():
        return False
    path.unlink()
    return True


def _participant(save, name: str, side: str, *, rng: Any = None) -> tuple[ChaseParticipant, str]:
    """按名字取一名参与者；返回 ``(参与者, 数值来源说明)``。"""
    label = str(name or "").strip()
    card = find_character(save, label)
    if card is not None:
        dex = card.abilities.modifier("dex")
        return (
            ChaseParticipant(
                cid=label,
                name=card.name or label,
                side=side,
                initiative=roll("1d20", rng=rng).total + dex,
                speed=card.speed,
                con_mod=card.abilities.modifier("con"),
                stealth_mod=card.skill_bonus("隐匿"),
                passive_perception=card.passive_score("察觉"),
            ),
            "角色卡",
        )
    monster = get_monster(label)
    if monster is not None:
        wis = monster.modifier("wis")
        return (
            ChaseParticipant(
                cid=label,
                name=monster.cn or label,
                side=side,
                initiative=roll("1d20", rng=rng).total + monster.modifier("dex"),
                speed=monster.speed,
                con_mod=monster.modifier("con"),
                stealth_mod=monster.modifier("dex"),
                passive_perception=10 + wis,
            ),
            "怪物图鉴",
        )
    return (
        ChaseParticipant(
            cid=label,
            name=label,
            side=side,
            initiative=roll("1d20", rng=rng).total,
            note="数值按默认（速度 30 / 调整值 0 / 被动察觉 10）",
        ),
        "默认值",
    )


def _parse_roster(text: str) -> list[tuple[str, str]]:
    """解析 ``名字:猎物,名字:追击者``；认不出角色的一律略过。"""
    result: list[tuple[str, str]] = []
    for chunk in _SPLIT.split(str(text or "").strip()):
        if not chunk:
            continue
        if ":" in chunk or "：" in chunk:
            parts = re.split(r"[:：]", chunk, maxsplit=1)
            name = parts[0]
            role = parts[1] if len(parts) > 1 else ""
        else:
            name, role = chunk, ""
        side = _SIDE_WORDS.get(role.strip().lower())
        if side and name.strip():
            result.append((name.strip(), side))
    return result


def _factor_words(*parts: str) -> list[str]:
    """把「因素」文本切成词；整句也留着，``escape_advantage`` 会认表里的词。"""
    words: list[str] = []
    for part in parts:
        for chunk in _SPLIT.split(str(part or "").strip()):
            if chunk:
                words.append(chunk)
    return words


def _who(state: ChaseState, tokens: list[str], index: int, player: str) -> str:
    """子命令没写名字时用当前回合的人（DM 最常这么用）。"""
    if len(tokens) > index and tokens[index]:
        return tokens[index]
    if str(player or "").strip():
        return str(player).strip()
    current = state.current()
    return current.name if current else ""


def _need_state(save) -> tuple[ChaseState | None, str]:
    state = load_chase(save)
    if state is None:
        return None, "当前没有进行中的追逐。开一场：tactic(action=追逐 开始 城市 60, target=小偷:猎物,守卫:追击者)"
    return state, ""


def do_chase(
    save,
    action_text: str,
    *,
    player: str = "",
    target: str = "",
    trigger: str = "",
    rng: Any = None,
) -> str:
    """处理 ``tactic`` 转过来的追逐子命令。"""
    tokens = str(action_text or "").split()
    cmd = tokens[1].strip() if len(tokens) > 1 else ""
    rest = tokens[2:]

    # 不需要已有追逐的分支
    if cmd in ("", "帮助", "help"):
        state = load_chase(save)
        return "\n".join(
            [chase_help(), format_chase(state) + "\n" + _VISIBILITY_NOTE]
            if state
            else [chase_help()]
        )
    if cmd == "规则":
        keys = [w for w in rest if w not in ("规则", "原文")]
        key = keys[0] if keys else ""
        text = format_chase_rules(key)
        return text or (
            "追逐规则可查：开始 / 疾走 / 攻击 / 结束 / 逃脱 / 意外 / 城市 / 荒野"
            "（留空给全表）"
        )
    if cmd == "开始":
        if load_chase(save) is not None:
            return "已经有一场追逐在跑；先写「追逐 结束」或用「追逐 状态」看当前局面"
        terrain = next((w for w in rest if w in ("城市", "荒野")), "城市")
        numbers = [int(w) for w in rest if w.isdigit()]
        distance = numbers[0] if numbers else 30
        roster = _parse_roster(target)
        if not any(side == PREY for _, side in roster) or not any(
            side == HUNTER for _, side in roster
        ):
            return (
                "开追逐要在 target 里同时给猎物和追击者，格式："
                "「小偷:猎物,守卫:追击者」（名字可用存档角色名或图鉴怪物名）。"
            )
        built = [_participant(save, name, side, rng=rng) for name, side in roster]
        participants = [item[0] for item in built]
        sources = "、".join(
            f"{p.name}={source}" for p, source in built if source == "默认值"
        )
        state = start_chase(terrain, distance, participants)
        save_chase(save, state)
        lines = [f"【追逐开始】{terrain}，起始距离 {distance} 尺。", format_chase(state)]
        if sources:
            lines.append(f"⚠️ 没查到数值按默认处理：{sources}；可用 update_character 补。")
        lines.append(_VISIBILITY_NOTE)
        return "\n".join(lines)
    if cmd == "结束":
        if not clear_chase(save):
            return "当前没有进行中的追逐"
        reason = " ".join(rest) or str(target or "").strip() or "一方停下"
        return f"追逐结束（{reason}）；账本已清除。记得在叙述里交代结果。"

    state, error = _need_state(save)
    if state is None:
        return error

    if cmd == "状态":
        return format_chase(state) + "\n" + _VISIBILITY_NOTE
    if cmd == "疾走":
        text = take_dash(state, _who(state, rest, 0, player), rng=rng)
    elif cmd == "移动":
        feet = next((int(w) for w in rest if w.isdigit()), 0)
        if feet <= 0:
            return "移动要写尺数，比如「追逐 移动 30」"
        idx = next((i + 1 for i, w in enumerate(rest) if w.isdigit()), 1)
        text = move_chase(state, _who(state, rest, idx, player), feet)
    elif cmd in ("意外", "复杂状况"):
        text = roll_complication(state, rng=rng)
    elif cmd in ("下一回合", "下回合", "推进"):
        factors = _factor_words(" ".join(rest), trigger, target)
        text = advance_turn(state, rng=rng, factors=factors)
    elif cmd == "逃脱":
        factors = _factor_words(" ".join(rest), trigger, target)
        text = escape_phase(state, rng=rng, factors=factors)
    elif cmd == "视线":
        off = any(w in ("关", "否", "取消", "off") for w in rest)
        words = [w for w in rest if w not in ("关", "否", "取消", "off")]
        who = _who(state, words, 0, player)
        actor = state.find(who)
        actor.left_sight = not off
        text = f"【{actor.name}】标记为{'仍在' if off else '已离开'}领头追击者的视线（影响轮末逃脱检定）。"
        state.log(text)
    elif cmd == "加入":
        roster = _parse_roster(" ".join(rest) or target)
        if not roster:
            return "加入要写「名字:猎物」或「名字:追击者」"
        lines = []
        for name, side in roster:
            participant, source = _participant(save, name, side, rng=rng)
            state.participants.append(participant)
            lines.append(f"{participant.name} 加入（先攻 {participant.initiative}，数值来自{source}）。")
        text = " ".join(lines)
        state.log(text)
    else:
        return f"没认出追逐子命令「{cmd}」。\n{chase_help()}"

    save_chase(save, state)
    return text
