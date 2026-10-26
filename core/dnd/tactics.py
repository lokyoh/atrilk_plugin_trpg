"""战斗战术规则（对照《玩家手册（2024）》第一章：战斗流程 / 擒抱 / 推撞 / 骑乘 / 视野光照）。

这些都是「规则书里有、引擎却没结算」的战术动作。做成纯函数：

- **对抗类**（擒抱 / 推撞）：双方各 d20 + 加值，给出一次对抗的成败与明细；
- **数值类**（上下坐骑耗速、强制移动豁免）：给出可直接用的数字 / 布尔；
- **判定类**（突袭的先攻姿态、光照遮蔽）：给出可判定的布尔或文字。

**工具侧**：纯判定（擒抱 / 推撞对抗、上下坐骑耗速、强制移动豁免）没有独立工具，
DM 用 ``query_data(table=rule)`` 查规则、用 ``check`` 掷骰；而**回避 / 躲藏 /
预备 / 借机攻击 / 上下坐骑**这五条要掷骰、挂状态、记行动资源，交给
``tactic`` 工具一次性结算（分派在 :func:`~.combat.do_tactic`）——它们不是「查一下
文字就能照做」的规则，让 DM 手算等于把已经自动化的东西退回去。

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
- **躲藏**：敏捷（隐匿）**DC 15**（2024 给了固定 DC，不再对抗被动察觉）；
  前提是处于重度遮蔽或四分之三 / 全掩护之后，且不在任何敌人的视线内。
  成功即获得**隐形**状态，出手（无论中不中）后位置暴露。
- **回避**：到下个回合开始前，打你有劣势、你的敏捷豁免有优势；
  失能或速度归零时失效。
- **预备**：花一个动作定好触发条件，触发时用**反应**执行那个动作；预备法术要专注。
- **借机攻击**：看得见的生物离开你的触及范围时，用**反应**对它做一次近战攻击。
"""

from __future__ import annotations

from typing import Any

from ..trpg.dice import Advantage, roll

__all__ = [
    "HIDE_DC",
    "LIGHT_LEVELS",
    "MOUNT_SAVE_DC",
    "TACTIC_ALIASES",
    "contest",
    "dodge_expires",
    "grapple_contest",
    "hide_check",
    "hide_requirement_note",
    "light_obscurement",
    "mount_cost",
    "mount_stays_mounted",
    "normalize_tactic",
    "ready_note",
    "rule_lookup",
    "shove_contest",
    "surprise_initiative",
    "vision_effect",
]

#: 骑乘被强制移动 / 坐骑倒地时的敏捷豁免 DC
MOUNT_SAVE_DC = 10

#: 躲藏（Hide 动作）的敏捷（隐匿）检定 DC（PHB 2024 给的是固定 15）
HIDE_DC = 15

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


#: 战术动作的写法 -> 规范名。规范名就是 :func:`~.combat.do_tactic` 认得的那几个。
TACTIC_ALIASES: dict[str, str] = {
    "回避": "回避",
    "闪避": "回避",
    "躲避": "回避",
    "dodge": "回避",
    "躲藏": "躲藏",
    "隐藏": "躲藏",
    "潜行": "躲藏",
    "hide": "躲藏",
    "预备": "预备",
    "准备": "预备",
    "ready": "预备",
    "借机攻击": "借机攻击",
    "借机": "借机攻击",
    "机会攻击": "借机攻击",
    "opportunity": "借机攻击",
    "上马": "上马",
    "骑乘": "上马",
    "mount": "上马",
    "下马": "下马",
    "unmount": "下马",
    # —— 职业资源类（花了次数 / 功力换效果）——
    "狂暴": "狂暴",
    "进入狂暴": "狂暴",
    "狂怒": "狂暴",
    "rage": "狂暴",
    "回气": "回气",
    "回复体力": "回气",
    "second wind": "回气",
    "secondwind": "回气",
    "疾风步": "疾风步",
    "疾风连击": "疾风步",
    "flurry": "疾风步",
    "flurry of blows": "疾风步",
    "患者防御": "患者防御",
    "patient defense": "患者防御",
    # —— 子职特性（只有少数几条值得单独结算）——
    "鲁莽攻击": "鲁莽攻击",
    "鲁莽": "鲁莽攻击",
    "reckless": "鲁莽攻击",
    "reckless attack": "鲁莽攻击",
}


def normalize_tactic(action: str) -> str:
    """把 DM 的写法归一到规范战术动作名；认不出返回空串（交给上层报错）。"""
    return TACTIC_ALIASES.get(str(action or "").strip().lower(), "")


def hide_check(total: int, dc: int = HIDE_DC) -> bool:
    """躲藏是否成功：敏捷（隐匿）``total >= DC``（2024 固定 DC 15）。

    2024 把 2014 的「对抗每个敌人的被动察觉」换成一个固定 DC，
    但**前提没变**：得有地方藏，而且不在人家视线里——那是 DM 判的，
    见 :func:`hide_requirement_note`。
    """
    return int(total or 0) >= int(dc)


def hide_requirement_note() -> str:
    """躲藏的环境前提（给 DM 的提示原文，成功 / 失败都要带一句）。"""
    return (
        "前提：处于重度遮蔽（黑暗 / 浓雾 / 密林）或四分之三、全掩护之后，"
        "且不在任何敌人的视线内——不满足就没得藏（这一步由你判定）"
    )


def ready_note(trigger: str) -> str:
    """预备动作的说明：触发时用反应执行，未触发则在自己下回合开始时作废。"""
    text = str(trigger or "").strip()
    when = f"「{text}」" if text else "（没写触发条件）"
    return (
        f"预备：花一个动作盯住{when}，条件达成时用**反应**执行那个动作"
        "（没触发就到自己下个回合开始时作废；预备的是法术则要专注）"
    )


def dodge_expires(speed: int, incapacitated: bool) -> bool:
    """回避是否已经失效：速度归零或陷入失能（PHB 2024 明写这两种情况失去增益）。"""
    return bool(incapacitated) or int(speed or 0) <= 0


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
    if key in ("躲藏", "隐藏", "潜行", "躲藏动作"):
        return (
            "躲藏（PHB 2024）：用动作做一次**敏捷（隐匿）检定，DC 15**。"
            "前提是你处于重度遮蔽（黑暗 / 浓雾 / 密林）或四分之三、全掩护之后，"
            "且不在任何敌人的视线内——不满足就不能尝试。成功即获得**隐形**状态"
            "（对它攻击有劣势、它对看不见它的生物攻击有优势）；"
            "**出手后位置一律暴露**，不论中没中。"
        )
    if key in ("回避", "闪避", "躲避", "回避动作"):
        return (
            "回避（PHB 2024，旧称闪避）：用动作，直到你**下个回合开始**为止，"
            "对你进行的攻击检定有劣势、你进行的**敏捷豁免**有优势。"
            "你一旦失能或速度归零，立刻失去这两个增益。系统会挂「回避」状态并按轮摘掉。"
        )
    if key in ("预备", "准备", "准备动作", "预备动作"):
        return (
            "预备（PHB 2024，旧称准备）：用动作定好「什么情况下做什么动作」，"
            "条件达成时你用**反应**执行它。触发没发生的话，到你下个回合开始时作废。"
            "预备的是法术则要花法术位并**保持专注**。"
        )
    if key in ("借机攻击", "机会攻击", "借机"):
        return (
            "借机攻击（PHB 2024）：一个你能看见的生物**离开你的触及范围**时，"
            "你可以用**反应**对它做一次近战攻击（只能用近战，法术不行）。"
            "用「撤离」动作或传送离开不触发。骑乘时由坐骑触发的话，"
            "攻击者可以选择以你或坐骑为目标。"
        )
    if key in ("鲁莽攻击", "鲁莽"):
        return (
            "鲁莽攻击（野蛮人 2 级，PHB 2024，**不花动作**）：你进行本回合第一次攻击"
            "检定时决定是否鲁莽——决定鲁莽后，直到你下个回合开始，你基于**力量**的"
            "攻击检定具有优势；但同一段时间里，**对你进行的攻击检定也具有优势**。"
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
