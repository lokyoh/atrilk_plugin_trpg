"""探索 / 环境规则（对照《快速参考 (5e_2014).md》探索与移动一节）。

这一块原来整段缺失——武器 / 战斗 / 法术都结算了，但「从多高摔下来、
一天能走多远、强行军掉几层力竭」这类规则一个都没有。这里只落**可结算**的
纯函数，不碰战斗流程；DM 用 ``query_data(table=rule)`` 查，或在战斗 / 叙述里
按需调用（坠落伤害将来可接进落点变化）。

规则口径（5e2014 PHB）：

- **坠落**：每落下 10 尺受到 1d6 钝击伤害（向下取整），上限 200 尺 = 20d6。
- **旅行步调**：慢 2 里/时（18 里/天，潜行有优势）/ 常 3（24）/ 快 4（30，
  被动察觉 -5、潜行劣势）。
- **强行军**：一天超过 8 小时，每多 1 小时需 DC10 体质豁免，失败 +1 力竭。
- **跳跃**：跳远 = 力量值尺（助跑）/ 减半（立定）；跳高 = 3 + 力量调整值尺（助跑）/ 减半。
- **窒息**：闭气 = 1 + 体质调整值分钟（至少 30 秒）；失去空气后还能坚持体质调整值轮（至少 1 轮）。
- **水下战斗**：站在浅水里武器攻击受限——近战无游泳速度且非匕首/标枪/短剑/矛/三叉戟带劣势；
  远程非弩/捕网/标枪类投掷武器带劣势，且超出常规射程直接落空。
"""

from __future__ import annotations

from typing import Any

from ..trpg.dice import roll

__all__ = [
    "FALLING_MAX_FEET",
    "TRAVEL_PACE",
    "falling_damage",
    "falling_dice",
    "forced_march_exhaustion",
    "hold_breath_minutes",
    "jump_distance",
    "jump_height",
    "rule_lookup",
    "suffocation_rounds",
    "travel_pace",
    "travel_pace_text",
    "underwater_disadvantage",
]

#: 坠落伤害上限（200 尺，超过按 20d6 算）
FALLING_MAX_FEET = 200


def falling_dice(feet: int) -> int:
    """坠落对应的 d6 数量：每 10 尺 1 颗，向下取整，上限 20。"""
    feet = max(0, int(feet or 0))
    return min(feet // 10, FALLING_MAX_FEET // 10)


def falling_damage(feet: int, rng: Any = None) -> tuple[int, int]:
    """坠落伤害：``(骰子数, 总伤害)``。

    没摔（≤0 尺）返回 ``(0, 0)``；超过 200 尺按 200 尺算（20d6）。
    """
    dice = falling_dice(feet)
    if dice <= 0:
        return 0, 0
    result = roll(f"{dice}d6", rng=rng)
    return dice, result.total


#: 旅行步调表：每里 = 约 1.5 公里
TRAVEL_PACE: dict[str, dict[str, Any]] = {
    "slow": {
        "cn": "慢速",
        "mi_per_hour": 2,
        "mi_per_day": 18,
        "stealth": "优势",
        "perception": 0,
    },
    "normal": {
        "cn": "常速",
        "mi_per_hour": 3,
        "mi_per_day": 24,
        "stealth": "普通",
        "perception": 0,
    },
    "fast": {
        "cn": "快速",
        "mi_per_hour": 4,
        "mi_per_day": 30,
        "stealth": "劣势",
        "perception": -5,
    },
}


def travel_pace(mode: str) -> dict[str, Any]:
    """取某种步调的参数；不认得的模式回退到常速。"""
    return TRAVEL_PACE.get(str(mode or "normal").lower(), TRAVEL_PACE["normal"])


def travel_pace_text(mode: str) -> str:
    """一种步调的可读说明（给 DM / 玩家）。"""
    p = travel_pace(mode)
    note = []
    if p["stealth"] == "优势":
        note.append("潜行有优势")
    elif p["stealth"] == "劣势":
        note.append("潜行劣势、被动察觉 -5")
    tail = f"（{'、'.join(note)}）" if note else ""
    return f"{p['cn']}：{p['mi_per_hour']} 里/时、{p['mi_per_day']} 里/天{tail}"


def forced_march_exhaustion(hours: int, con_mod: int, rng: Any = None) -> int:
    """强行军：一天行进超过 8 小时，每多 1 小时需 DC10 体质豁免，失败 +1 力竭。

    返回累计获得的力竭层数（返回给 DM 的「本次强行军新增力竭」）。

    :param hours: 一天行进的**总小时数**（≤8 返回 0；9 小时 = 超出 1 小时）。
    :param con_mod: 体质调整值（DC10 的对抗加值）。
    """
    extra = max(0, int(hours or 0) - 8)
    if extra <= 0:
        return 0
    gained = 0
    for _ in range(extra):
        result = roll("1d20", rng=rng)
        if result.total + int(con_mod) < 10:
            gained += 1
    return gained


def jump_distance(strength_score: int, run_up: bool = True) -> int:
    """跳远距离（尺）。

    助跑（≥10 尺）时等于力量值；立定跳远减半。每跨越 1 尺花费 1 尺移动力。
    """
    score = max(0, int(strength_score or 0))
    return score if run_up else score // 2


def jump_height(strength_mod: int, run_up: bool = True) -> int:
    """跳高高度（尺）。

    助跑时等于 3 + 力量调整值（至少 0）；立定跳高减半。伸高手臂可再 +1.5 倍身高。
    """
    mod = int(strength_mod or 0)
    base = max(0, 3 + mod)
    return base if run_up else base // 2


def hold_breath_minutes(con_mod: int) -> float:
    """可闭气分钟数：1 + 体质调整值，至少 0.5（30 秒）。"""
    return max(0.5, 1 + int(con_mod or 0))


def suffocation_rounds(con_mod: int) -> int:
    """失去空气后还能坚持的战斗轮数：体质调整值，至少 1 轮；之后降为 0 并濒死。"""
    return max(1, int(con_mod or 0))


#: 水下近战不受劣势影响的武器（5e2014 水下战斗）
_UNDERWATER_MELEE_OK = frozenset(
    {"dagger", "javelin", "shortsword", "spear", "trident"}
)
#: 水下远程不受劣势影响、且超出常规射程不落空的武器
_UNDERWATER_RANGED_OK = frozenset(
    {
        "light_crossbow",
        "hand_crossbow",
        "heavy_crossbow",
        "net",
        "javelin",
        "spear",
        "trident",
        "dart",
    }
)


def underwater_disadvantage(
    attack_kind: str, weapon_key: str, has_swim_speed: bool = False
) -> bool:
    """水下武器攻击是否带劣势（5e2014 水下战斗）。

    - 近战武器攻击：无游泳速度且武器不是匕首 / 标枪 / 短剑 / 矛 / 三叉戟 → 劣势。
    - 远程武器攻击：武器不是弩 / 捕网 / 标枪类投掷武器（矛 / 三叉戟 / 飞标）→ 劣势。
    - 徒手（weapon_key 为空）按近战处理，无游泳速度时带劣势。
    - 非 ``"melee"`` / ``"ranged"``（法术、徒手远程等）返回 False，由别处裁定。
    """
    kind = str(attack_kind or "").lower()
    key = str(weapon_key or "")
    if kind == "melee":
        if has_swim_speed:
            return False
        return key not in _UNDERWATER_MELEE_OK
    if kind == "ranged":
        return key not in _UNDERWATER_RANGED_OK
    return False


def rule_lookup(name: str) -> str:
    """按名字查一条探索 / 环境规则；认不出就列出可用条目。

    名字认得：坠落 / 旅行步调 / 强行军 / 跳跃 / 窒息 / 水下战斗 / 攀爬游泳匍匐。
    """
    key = str(name or "").strip()
    if key in ("坠落", "坠落伤害", "摔落", "跌落"):
        return (
            "坠落伤害（5e2014）：每落下 10 尺受到 1d6 钝击伤害（向下取整），"
            "上限 200 尺 = 20d6。落地瞬间的其他效果（溺水、尖刺、地形）由 DM 裁定。"
        )
    if key in ("旅行", "旅行步调", "步调", "赶路", "行军"):
        return (
            "旅行步调（5e2014）：慢速 2 里/时（18 里/天，潜行有优势）/ "
            "常速 3（24）/ 快速 4（30，潜行劣势、被动察觉 -5）。一天按 8 小时行进。"
        )
    if key in ("强行军", "疲劳", "力竭行军"):
        return (
            "强行军（5e2014）：一天行进超过 8 小时，每多 1 小时需过 DC10 体质豁免，"
            "失败获得 1 层力竭。快速步调强行军更伤：被动察觉 -5 且每多 1 小时同样要豁免。"
        )
    if key in ("跳跃", "跳远", "跳高", "蹦"):
        return (
            "跳跃（5e2014）：跳远 = 力量值尺（需 ≥10 尺助跑，立定减半）；"
            "跳高 = 3 + 力量调整值尺（助跑，立定减半），伸手可再 +1.5 倍身高。"
            "每跨越 1 尺花费 1 尺移动力；落点困难地形需 DC10 敏捷检定站稳。"
        )
    if key in ("窒息", "溺水", "闭气", "缺氧"):
        return (
            "窒息（5e2014）：可闭气 1 + 体质调整值分钟（至少 30 秒）；"
            "失去空气后还能坚持体质调整值轮（至少 1 轮），随后一回合开始降为 0 并濒死，"
            "直至能呼吸为止无法被治疗或稳定。"
        )
    if key in ("水下", "水下战斗", "潜水"):
        return (
            "水下战斗（5e2014）：近战武器攻击无游泳速度且非匕首/标枪/短剑/矛/三叉戟带劣势；"
            "远程武器攻击非弩/捕网/标枪类（矛/三叉戟/飞标）带劣势，且超出常规射程直接落空。"
            "完全没入水中的生物与物件对火焰伤害具有抗性。"
        )
    if key in ("攀爬", "游泳", "匍匐", "攀爬游泳", "攀爬游泳匍匐"):
        return (
            "攀爬 / 游泳（5e2014）：移动时每 1 尺多花 1 尺移动力（困难地形上额外 2 尺），"
            "除非生物有对应的攀爬 / 游泳速度。匍匐移动速度减半。"
        )
    return (
        "可用探索 / 环境规则：坠落（坠落伤害）、旅行步调（慢/常/快）、"
        "强行军（超 8 小时掉力竭）、跳跃（跳远/跳高）、窒息（闭气/溺水）、"
        "水下战斗（水下近战/远程劣势）、攀爬游泳匍匐（速度减半）。"
    )
