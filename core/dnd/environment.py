"""探索 / 环境规则（对照《玩家手册（2024）》第一章「探索」一节）。

这一块原来整段缺失——武器 / 战斗 / 法术都结算了，但「从多高摔下来、
一天能走多远」这类规则一个都没有。这里只落**可结算**的纯函数，不碰战斗流程；
DM 用 ``query_data(table=rule)`` 查，或在战斗 / 叙述里按需调用。

规则口径（2024 PHB；坠落 / 窒息 / 水下与 2014 相同，旅行步调按 2024 重写）：

- **坠落**：每落下 10 尺受到 1d6 钝击伤害（向下取整），上限 200 尺 = 20d6。
- **旅行步调**（2024，与 2014 效果不同）：快速 400 尺/分（4 里/时、30 里/天）——
  感知（察觉或求生）与敏捷（隐匿）劣势；普通 300 尺/分（3 里/时、24 里/天）——
  敏捷（隐匿）劣势；慢速 200 尺/分（2 里/时、18 里/天）——感知（察觉或求生）优势。
  骑乘坐骑一小时可行两倍距离，之后坐骑需短休或长休。
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


#: 旅行步调表：每里 = 约 1.5 公里。
#: **2024 PHB 口径**（与 2014 不同）：三种步调各有一次游戏效果——
#: 快速 = 感知（察觉或求生）与隐匿劣势；普通 = 隐匿劣势；慢速 = 感知（察觉或求生）优势。
#: ``ft_per_min`` 是 2024 表里的「每分钟」列（分秒必争时用）。
TRAVEL_PACE: dict[str, dict[str, Any]] = {
    "slow": {
        "cn": "慢速",
        "ft_per_min": 200,
        "mi_per_hour": 2,
        "mi_per_day": 18,
        "stealth": "普通",
        "perception": "优势",
    },
    "normal": {
        "cn": "普通",
        "ft_per_min": 300,
        "mi_per_hour": 3,
        "mi_per_day": 24,
        "stealth": "劣势",
        "perception": "普通",
    },
    "fast": {
        "cn": "快速",
        "ft_per_min": 400,
        "mi_per_hour": 4,
        "mi_per_day": 30,
        "stealth": "劣势",
        "perception": "劣势",
    },
}


def travel_pace(mode: str) -> dict[str, Any]:
    """取某种步调的参数；不认得的模式回退到普通。"""
    return TRAVEL_PACE.get(str(mode or "normal").lower(), TRAVEL_PACE["normal"])


#: 步调效果的人类可读名（检定项目 + 姿态）
_PACE_EFFECT_CN = {
    "stealth": "隐匿（潜行）",
    "perception": "感知（察觉或求生）",
}


def travel_pace_text(mode: str) -> str:
    """一种步调的可读说明（给 DM / 玩家）。"""
    p = travel_pace(mode)
    note: list[str] = []
    for field in ("stealth", "perception"):
        stance = p.get(field, "普通")
        if stance in ("优势", "劣势"):
            note.append(f"{_PACE_EFFECT_CN[field]}{stance}")
    tail = f"（{'；'.join(note)}）" if note else "（无步调效果）"
    return (
        f"{p['cn']}：{p['ft_per_min']} 尺/分钟、{p['mi_per_hour']} 里/时、"
        f"{p['mi_per_day']} 里/天{tail}"
    )


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


#: 水下近战不受劣势影响的武器（水下战斗，2014 与 2024 相同）
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
    """水下武器攻击是否带劣势（水下战斗，2014 与 2024 相同）。

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
            "坠落伤害（PHB）：每落下 10 尺受到 1d6 钝击伤害（向下取整），"
            "上限 200 尺 = 20d6。落地瞬间的其他效果（溺水、尖刺、地形）由 DM 裁定。"
        )
    if key in ("旅行", "旅行步调", "步调", "赶路", "行军"):
        return (
            "旅行步调（2024 PHB）：快速 400 尺/分（4 里/时、30 里/天）——"
            "感知（察觉或求生）与敏捷（隐匿）检定劣势；"
            "普通 300 尺/分（3 里/时、24 里/天）——敏捷（隐匿）检定劣势；"
            "慢速 200 尺/分（2 里/时、18 里/天）——感知（察觉或求生）检定优势。"
            "骑乘坐骑一小时可行两倍距离，之后坐骑需短休或长休。"
            "（2014 旧表是慢速潜行优势 / 快速被动察觉 -5，已不适用）"
        )
    if key in ("强行军", "疲劳", "力竭行军"):
        return (
            "强行军（沿用 DC10 固定口径）：一天行进超过 8 小时，每多 1 小时需过 DC10 体质豁免，"
            "失败获得 1 层力竭。快速步调强行军更伤：感知（察觉或求生）与隐匿都带劣势，"
            "每多 1 小时同样要豁免。"
        )
    if key in ("跳跃", "跳远", "跳高", "蹦"):
        return (
            "跳跃（PHB）：跳远 = 力量值尺（需 ≥10 尺助跑，立定减半）；"
            "跳高 = 3 + 力量调整值尺（助跑，立定减半），伸手可再 +1.5 倍身高。"
            "每跨越 1 尺花费 1 尺移动力；落点困难地形需 DC10 敏捷检定站稳。"
        )
    if key in ("窒息", "溺水", "闭气", "缺氧"):
        return (
            "窒息（PHB）：可闭气 1 + 体质调整值分钟（至少 30 秒）；"
            "失去空气后还能坚持体质调整值轮（至少 1 轮），随后一回合开始降为 0 并濒死，"
            "直至能呼吸为止无法被治疗或稳定。"
        )
    if key in ("水下", "水下战斗", "潜水"):
        return (
            "水下战斗（PHB）：近战武器攻击无游泳速度且非匕首/标枪/短剑/矛/三叉戟带劣势；"
            "远程武器攻击非弩/捕网/标枪类（矛/三叉戟/飞标）带劣势，且超出常规射程直接落空。"
            "完全没入水中的生物与物件对火焰伤害具有抗性。"
        )
    if key in ("攀爬", "游泳", "匍匐", "攀爬游泳", "攀爬游泳匍匐"):
        return (
            "攀爬 / 游泳（PHB）：移动时每 1 尺多花 1 尺移动力（困难地形上额外 2 尺），"
            "除非生物有对应的攀爬 / 游泳速度。匍匐移动速度减半。"
        )
    # 战斗战术规则（擒抱 / 推撞 / 突袭 / 骑乘 / 躲藏 / 回避 / 预备 / 借机攻击 /
    # 视野光照）在 tactics 里，这里兜底前先问一句——懒导入避免模块级耦合。
    from . import tactics

    tactic_text = tactics.rule_lookup(key)
    if tactic_text:
        return tactic_text
    return (
        "可用规则：坠落、旅行步调、强行军、跳跃、窒息、水下战斗、攀爬游泳匍匐；"
        "战斗战术：擒抱、推撞、突袭、骑乘、躲藏、回避、预备、借机攻击、视野光照。"
    )
