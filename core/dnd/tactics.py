"""战斗战术规则（对照《玩家手册（2024）》第一章：战斗流程 / 擒抱 / 推撞 / 骑乘 / 视野光照）。

这些都是「规则书里有、引擎却没结算」的战术动作。做成纯函数：

- **对抗类**（擒抱 / 推撞）：双方各 d20 + 加值，给出一次对抗的成败与明细；
- **数值类**（上下坐骑耗速、强制移动豁免）：给出可直接用的数字 / 布尔；
- **判定类**（突袭的先攻姿态、光照遮蔽）：给出可判定的布尔或文字。

**刻意不新增 DM 工具**——工具提示词预算已经顶满（见 ``tests/test_trpg_prompt_size``），
这些规则由 DM 用 ``query_data(table=rule)`` 查，对抗结果写进叙述，条件用现有手段落地。

规则口径（2024 PHB；擒抱 / 推撞 / 视野光照两版相同，突袭按 2024）：

- **擒抱**：力量（运动）对抗目标的 力量（运动）或 敏捷（特技）；胜则目标受擒。
  目标体型不得比你大超过一级、须在触及内、至少一只空手；目标失能则直接成功。
- **推撞**：同一对抗；胜则把目标击倒（倒地）或推开 5 尺，攻击者二选一。
- **突袭**：**2024 口径**——被突袭者在其**先攻检定**上具有劣势
  （2014 的「第一个回合不能行动」已不采用）。
- **骑乘**：上下坐骑各花「一半速度」的移动力；被强制移动 / 坐骑倒地时
  DC10 敏捷豁免，失败在坐骑 5 尺内失足倒地。
- **视野光照**：明亮 / 微光 / 黑暗；微光 = 轻度遮蔽（视觉察觉劣势），
  黑暗 = 重度遮蔽（等同目盲）；黑暗视觉把黑暗当微光、微光当明亮。
"""

from __future__ import annotations

from typing import Any

from ..trpg.dice import Advantage, roll

__all__ = [
    "LIGHT_LEVELS",
    "MOUNT_SAVE_DC",
    "contest",
    "grapple_contest",
    "light_obscurement",
    "mount_cost",
    "mount_stays_mounted",
    "rule_lookup",
    "shove_contest",
    "surprise_initiative",
    "vision_effect",
]

#: 骑乘被强制移动 / 坐骑倒地时的敏捷豁免 DC
MOUNT_SAVE_DC = 10

#: 光照三级的可读名
LIGHT_LEVELS: dict[str, str] = {
    "bright": "明亮",
    "dim": "微光",
    "dark": "黑暗",
}

_LIGHT_ALIASES: dict[str, str] = {
    "bright": "bright",
    "明亮": "bright",
    "亮": "bright",
    "dim": "dim",
    "微光": "dim",
    "昏暗": "dim",
    "阴暗": "dim",
    "dark": "dark",
    "黑暗": "dark",
    "无光": "dark",
}


def contest(
    attacker_bonus: int,
    defender_bonus: int,
    rng: Any = None,
    *,
    label: str = "对抗",
) -> tuple[bool, str]:
    """一次对抗检定：双方各 d20 + 加值，**攻击方严格大于防守方**才成功。

    5e 的对抗里平局视为维持原状（防守方胜），所以判据是 ``>`` 而不是 ``>=``。

    返回 ``(攻击方是否胜出, 明细文字)``。
    """
    attack_roll = roll("1d20", rng=rng)
    defend_roll = roll("1d20", rng=rng)
    a_bonus = int(attacker_bonus or 0)
    d_bonus = int(defender_bonus or 0)
    a_total = attack_roll.total + a_bonus
    d_total = defend_roll.total + d_bonus
    win = a_total > d_total
    detail = (
        f"{label}：攻方 {attack_roll.total}{a_bonus:+d}={a_total}，"
        f"守方 {defend_roll.total}{d_bonus:+d}={d_total} → "
        f"{'成功' if win else '失败（平局算守方）'}"
    )
    return win, detail


def grapple_contest(
    attacker_bonus: int, defender_bonus: int, rng: Any = None
) -> tuple[bool, str]:
    """擒抱检定：力量（运动）对抗目标的 力量（运动）或 敏捷（特技）。

    胜出 → 目标**受擒**（速度归零，用动作可挣脱）。目标失能时 DM 直接判成功，
    不必调用本函数。
    """
    win, detail = contest(
        attacker_bonus,
        defender_bonus,
        rng,
        label="擒抱（力量·运动 对 力量·运动/敏捷·特技）",
    )
    tail = "（目标受擒：速度归零，可用动作挣脱）" if win else "（没抓住）"
    return win, detail + tail


def shove_contest(
    attacker_bonus: int, defender_bonus: int, rng: Any = None
) -> tuple[bool, str]:
    """推撞检定：与擒抱同一对抗；胜出后可**击倒（倒地）或推开 5 尺**，攻击者二选一。"""
    win, detail = contest(
        attacker_bonus,
        defender_bonus,
        rng,
        label="推撞（力量·运动 对 力量·运动/敏捷·特技）",
    )
    tail = "（成功：击倒倒地 或 推开 5 尺，二选一）" if win else "（没推动）"
    return win, detail + tail


def surprise_initiative(surprised: bool) -> Advantage:
    """被突袭者的**先攻姿态**（2024 PHB）：突袭 → 先攻检定劣势。

    2024 把 2014 的「被突袭者第一个回合不能行动 / 不能反应」改成了
    「先攻检定具有劣势」——伏击的价值体现在先攻排序上，不是禁言。
    注意：突袭**不**自动给攻击方优势，那要另算（躲藏方未命中位置前对方打不到）。

    :param surprised: 该生物是否遭突袭（由 DM 判定：躲藏方隐匿 对 被动察觉）。
    :return: 遭突袭 :data:`Advantage.DISADVANTAGE`，否则 :data:`Advantage.NORMAL`。
    """
    return Advantage.DISADVANTAGE if surprised else Advantage.NORMAL


def mount_cost(speed: int) -> int:
    """上下坐骑花费的移动力 = 一半速度（向下取整）。速度不足一半则上不了 / 下不来。"""
    return max(0, int(speed or 0)) // 2


def mount_stays_mounted(total: int, dc: int = MOUNT_SAVE_DC) -> bool:
    """骑乘时被强制移动 / 坐骑倒地：敏捷豁免 ``total >= DC`` 则稳住，否则在坐骑 5 尺内倒地。"""
    return int(total or 0) >= int(dc)


def light_obscurement(level: str) -> str:
    """光照对应的遮蔽级别：``无`` / ``轻度`` / ``重度``。不认得的按「无」处理。"""
    key = _LIGHT_ALIASES.get(str(level or "").strip().lower())
    if key == "bright":
        return "无"
    if key == "dim":
        return "轻度"
    if key == "dark":
        return "重度"
    return "无"


def vision_effect(level: str, darkvision: bool = False) -> str:
    """某光照下基于视觉的判定效果；有黑暗视觉则黑暗按微光、微光按明亮算。"""
    obscured = light_obscurement(level)
    if darkvision:
        if obscured == "重度":
            obscured = "轻度"
        elif obscured == "轻度":
            obscured = "无"
    if obscured == "无":
        return "正常视物"
    if obscured == "轻度":
        return "轻度遮蔽：基于视觉的感知（察觉）检定劣势"
    return "重度遮蔽：等同目盲（看不见其中的目标）"


def rule_lookup(name: str) -> str:
    """按名字查一条战斗战术规则；认不出返回空串（交给上层兜底）。"""
    key = str(name or "").strip()
    if key in ("擒抱", "抓取", "抓住", "扭打"):
        return (
            "擒抱（PHB）：用「攻击」动作发动特殊近战攻击——力量（运动）对抗"
            "目标的力量（运动）或敏捷（特技）（目标选）。目标体型不得大你超过一级、"
            "须在触及内、你至少一只空手；目标失能则直接成功。胜则目标受擒（速度归零），"
            "它可用动作挣脱（力量运动/敏捷特技 对抗 你的力量运动）。拖行受擒者时你速度减半。"
        )
    if key in ("推撞", "推开", "撞倒", "推倒"):
        return (
            "推撞（PHB）：用「攻击」动作发动特殊近战攻击——力量（运动）对抗"
            "目标的力量（运动）或敏捷（特技）（目标选）。目标体型不得大你超过一级、"
            "须在触及内；目标失能则直接成功。胜则把目标击倒（倒地）或推开 5 尺，二选一。"
        )
    if key in ("突袭", "偷袭", "伏击"):
        return (
            "突袭（2024 PHB）：由 DM 判定——躲藏方敏捷（隐匿）对抗对方每人的被动察觉，"
            "没注意到威胁的角色即遭突袭。**被突袭者在其先攻检定上具有劣势**"
            "（开战时对 start_combat 传 surprised=遭袭者名字即可，系统按劣势掷先攻）。"
            "注意：突袭并不自动给攻击方优势；2014 的「第一个回合不能行动」已不采用。"
        )
    if key in ("骑乘", "坐骑", "上马", "下马", "骑乘战斗"):
        return (
            "骑乘（PHB）：上下坐骑各花「一半速度」的移动力（速度不足一半则做不到）。"
            "骑乘时被强制移动、或被击倒时，需过 DC10 敏捷豁免，失败在坐骑 5 尺内失足倒地；"
            "坐骑倒地时你可用反应安全下马，否则骑乘强行结束并在坐骑 5 尺内倒地。"
            "受训坐骑受你控制（先攻与你同值，只能疾走/撤离/回避）；智能坐骑自主行动。"
            "坐骑触发借机攻击时，攻击者可选择以你或坐骑为目标。"
        )
    if key in (
        "视野",
        "光照",
        "视野光照",
        "视野与光照",
        "黑暗视觉",
        "微光",
        "遮蔽",
        "黑暗",
    ):
        return (
            "视野与光照（PHB）：光照分明亮 / 微光 / 黑暗。微光是轻度遮蔽——"
            "基于视觉的感知（察觉）检定劣势；黑暗是重度遮蔽——等同目盲。"
            "黑暗视觉：微光视为明亮、黑暗视为微光。盲视/真实视觉另有规则。"
        )
    return ""
