"""旅程阶段的存档 IO 与 ``tactic`` 子命令编排（agents 层）。

``core/dnd/journey.py`` 是纯规则层，不碰磁盘；这里把 ``JourneyState`` 落到
**该存档目录下** 的 ``journey.json``（与 ``combat.json`` / ``chase.json`` 同级），
再把 DM 写进 ``tactic(action=旅程 …)`` 的子命令翻译成状态机调用。

边界：DC 由系统给，检定由 DM 用 ``check`` 对着角色卡做；天气 / 遭遇距离 /
觅食产出 / 迷路延时由系统掷骰。「有没有遭遇」「障碍造成什么后果」是 DM 裁定。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...dnd.journey import (
    JourneyLeg,
    JourneyState,
    complete_leg,
    forage_dc,
    forage_yield,
    format_journey,
    format_journey_rules,
    get_terrain,
    journey_help,
    navigation_delay,
    normalize_pace,
    roll_encounter_distance,
    roll_weather,
    set_pace,
    start_journey,
    terrain_names,
)
from ...dnd.save_store import find_character, save_dir

__all__ = [
    "clear_journey",
    "do_journey",
    "load_journey",
    "save_journey",
]

JOURNEY_FILENAME = "journey.json"

#: 玩家可见性提醒（只在开规划卡 / 看账本时附一次）
_VISIBILITY_NOTE = (
    "⚠️ 这份规划卡只有 DM 看得到：地形、走几天、天气与遭遇距离要显式写进叙述，玩家才知道。"
)

_SPLIT = re.compile(r"[,，;；\s]+")
_COLON = re.compile(r"[:：]")
_COMMANDS = (
    "规划",
    "状态",
    "步调",
    "天气",
    "遭遇",
    "觅食",
    "迷路",
    "挑战",
    "推进",
    "结束",
    "规则",
)


def journey_path(save) -> Path:
    return save_dir(save.save_id) / JOURNEY_FILENAME


def load_journey(save) -> JourneyState | None:
    """读旅程规划卡；没在旅程中返回 ``None``。"""
    path = journey_path(save)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return JourneyState.from_dict(data)


def save_journey(save, state: JourneyState) -> Path:
    """原子写回规划卡。"""
    path = journey_path(save)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def clear_journey(save) -> bool:
    path = journey_path(save)
    if not path.is_file():
        return False
    path.unlink()
    return True


def _number(word: str) -> float | None:
    try:
        return float(word)
    except (TypeError, ValueError):
        return None


def _parse_legs(spec: str) -> tuple[list[JourneyLeg], str]:
    """解析 ``起点:终点:英里:地形[:名称]``，多段用逗号 / 空格隔开。

    也收简写 ``地形:英里`` / ``英里:地形`` / 单个地形名（英里记 0，
    由 DM 后续补）。认不出的地形**不猜**，直接报错并列出 11 种。
    """
    legs: list[JourneyLeg] = []
    errors: list[str] = []
    for chunk in _SPLIT.split(str(spec or "").strip()):
        if not chunk:
            continue
        parts = [p.strip() for p in _COLON.split(chunk) if p.strip()]
        start = goal = name = ""
        miles = 0.0
        terrain = ""
        if len(parts) >= 4:
            start, goal, miles_raw, terrain = parts[:4]
            miles = _number(miles_raw) or 0.0
            name = parts[4] if len(parts) > 4 else ""
        elif len(parts) == 2:
            a, b = parts
            if get_terrain(a):
                terrain, miles = a, _number(b) or 0.0
            elif get_terrain(b):
                terrain, miles = b, _number(a) or 0.0
        elif len(parts) == 1:
            terrain = parts[0]
        row = get_terrain(terrain)
        if row is None:
            errors.append(chunk)
            continue
        legs.append(
            JourneyLeg(
                name=name or goal or str(row["cn"]),
                start=start,
                goal=goal,
                miles=max(0.0, miles),
                terrain=str(row["cn"]),
            )
        )
    if errors:
        return [], (
            f"这几段没认出地形：{'、'.join(errors)}。可用：{'、'.join(terrain_names())}"
        )
    if not legs:
        return [], "规划要写阶段，比如「旅程 规划 河湾:林缘:30:草地,林缘:遗址:20:森林」"
    return legs, ""


def _need_state(save) -> tuple[JourneyState | None, str]:
    state = load_journey(save)
    if state is None:
        return None, "当前没有旅程规划卡。先写「旅程 规划 起点:终点:英里:地形」"
    return state, ""


def _wisdom_mod(save, who: str) -> tuple[int, str]:
    """觅食产出要感知调整值：优先取角色卡，取不到如实标默认 0。"""
    card = find_character(save, str(who or "").strip())
    if card is None:
        return 0, "默认值 0（没找到角色卡）"
    return int(card.abilities.modifier("wis")), "角色卡"


def do_journey(
    save,
    action_text: str,
    *,
    player: str = "",
    target: str = "",
    trigger: str = "",
    rng: Any = None,
) -> str:
    """把 ``tactic(action=旅程 …)`` 翻成状态机调用。"""
    text = str(action_text or "").strip()
    for prefix in ("旅程", "旅行"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    if not text:
        return journey_help()

    cmd = next((c for c in _COMMANDS if text.startswith(c)), "")
    rest = text[len(cmd) :].strip() if cmd else ""
    if not cmd:
        return f"没认出旅程子命令「{text}」。\n{journey_help()}"

    if cmd == "规划":
        spec = " ".join(x for x in (rest, str(target or "").strip()) if x)
        legs, error = _parse_legs(spec)
        if error:
            return error
        state = start_journey(legs)
        save_journey(save, state)
        return (
            f"建好 {len(legs)} 个阶段。\n{format_journey(state)}\n{_VISIBILITY_NOTE}"
            "\n下一步：「旅程 步调 快速」定步调、「旅程 天气」掷天气。"
        )

    if cmd == "规则":
        return format_journey_rules(rest) or journey_help()

    state, error = _need_state(save)
    if state is None:
        return error

    if cmd == "结束":
        clear_journey(save)
        return "旅程规划卡已清掉（阶段记录留在存档文件里，可随时重新规划）。"
    if cmd == "状态":
        return format_journey(state) + "\n" + _VISIBILITY_NOTE

    leg = state.current()
    if leg is None:
        return "所有阶段都走完了。写「旅程 规划 …」开新旅程，「旅程 结束」清掉旧卡。"

    if cmd == "步调":
        words = _SPLIT.split(rest)
        wanted = next((w for w in words if normalize_pace(w)), "普通")
        joined = rest.replace(" ", "")
        text_out = set_pace(
            state,
            wanted,
            good_road="路况" in joined or "好路" in joined,
            half_speed="速度减半" in joined or "减半" in joined or "半速" in joined,
            ignore_difficult="无视困难" in joined or "飞行" in joined,
        )
        save_journey(save, state)
        return text_out + f"\n（本阶段 {leg.terrain}，觅食DC{forage_dc(leg.terrain)}）"

    if cmd == "天气":
        weather = roll_weather(rng=rng)
        leg.weather = weather
        state.log.append("掷天气：" + weather.replace("\n", " "))
        save_journey(save, state)
        return weather + "\n（天气已记进当前阶段；极端天气后果见第三章环境效应）"

    if cmd == "遭遇":
        feet, out = roll_encounter_distance(leg.terrain, rng=rng)
        return out + "\n（系统只掷距离；是否真有遭遇、来的是什么由你裁定）"

    if cmd == "觅食":
        who = rest or str(player or "").strip()
        wis, source = _wisdom_mod(save, who)
        _, _, out = forage_yield(wis, rng=rng)
        return (
            f"{out}（感知调整值来自{source}）\n"
            f"⚠️ 先由 DM 用 check 对 {who or '队伍'} 做感知（求生）检定，"
            f"DC {forage_dc(leg.terrain)}（{leg.terrain}）；**失败就什么也找不到**，别用上面的产出。"
        )

    if cmd == "迷路":
        _, percent, out = navigation_delay(rng=rng)
        return (
            f"{out}\n⚠️ 先由 DM 用 check 做感知（求生）对抗导航DC；"
            f"失败才适用。写「旅程 推进 {percent}」把延时算进本阶段。"
        )

    if cmd == "挑战":
        label = rest or str(trigger or "").strip()
        if not label:
            return (
                "挑战可写：遭遇其他生物 / 觅食 / 导航 / 阻碍 / 搜索行动 / 追踪。"
                "\n" + (format_journey_rules("挑战") or "")
            )
        leg.challenges.append(label)
        state.log.append(f"阶段挑战：{label}")
        save_journey(save, state)
        return f"记下本阶段挑战「{label}」，共 {len(leg.challenges)} 项。"

    if cmd == "推进":
        percent = next(
            (n for w in _SPLIT.split(rest) if (n := _number(w)) is not None), 0.0
        )
        out = complete_leg(state, delay_percent=max(0.0, percent))
        save_journey(save, state)
        return out + f"\n{format_journey(state)}\n{_VISIBILITY_NOTE}"

    return f"没认出旅程子命令「{text}」。\n{journey_help()}"
