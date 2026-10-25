"""世界时钟：时间推进、时段、疲惫与饥饿的判据，纯函数。

## 时间存在哪

存在 :class:`~.events.EventPool` 上：``day``（1 起）/ ``hours``（0–23）/ ``minutes``（0–59）。
**内部按分钟累加**，展示时才折成「第 N 天 14:20 · 下午」。

为什么不再加一个独立的 ``Clock`` 类：时间已经和事件池长在一起了
（``since_day``、``rumor_for(day)``、``EventModifier.day`` 都在用它）。
再包一层只会让「现在几点」有两个来源。

## 时间从哪来

**只从玩家动作里扣**，不是 DM 口头宣布：

============  ==========================================
动作          耗时
============  ==========================================
同省移动      3 小时（几小时的中值）
跨省移动      按区域跨越难度的较大者
短休          1 小时
长休          8 小时
一场战斗      10 分钟（含收尸、包扎、喘气）
一次行动轮    10 分钟（调查 / 交涉 / 磨嘴皮）
============  ==========================================

最后一行的意义：这类动作**没有客观时长**，所以给一个固定值。
它让时间永远在走，而 DM 一个字都不用报。

## 疲惫与饥饿

两条判据，这里只回答「到点了没有」，**结算在编排层**（要掷骰、要改角色卡）：

- **挨饿**（5e 规则，计时口径是房规）：连续 **3 + 体质调整值** 天不进食后，
  每饿一天 +1 级力竭（「粮食与水·进食」）。计时器 ``hunger_days`` 在角色卡上，
  见 :meth:`~.card.DndCharacter.hunger_limit`。**「3 + 体质调整值」是 2014 的
  口径**，2024 改成了「吃不足半量就过一次 DC 10 体质豁免」——本局沿用旧口径，
  因为它能记账、不需要每顿饭都判一次。
  **不单独追踪饮水**：水源（井、溪、买水）由 DM 按叙述裁定，真遇到无水环境
  直接用 ``update_character`` 的 exhaustion 加级——参考里「只喝一半过 DC15」
  那条的一半份量同样没有可记账的对象。
- **24 小时没长休**（**房规**，不是 PHB 规则）：DC 10 体质豁免，失败 +1 级力竭。
  ⚠️ PHB 2014 / 2024 都没有这条（2024 只在「力竭」条目里说长休减 1 级），
  它是本局为了让「连续赶路不睡觉」有代价而加的——别在别处当成规则书引用。
  之所以留着：它让 :func:`fatigue_due` 有一个可记账的触发器，
  而代价（1 级力竭 = 全检定 −2、速度 −5 尺）在 2024 口径下也不算重。

判据做成纯函数的好处是：它们能被单测钉住，而不是埋在 prompt 里靠 DM 记得。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "COMBAT_MINUTES",
    "FATIGUE_DC",
    "HOURS_PER_LONG_REST",
    "HOURS_PER_SHORT_REST",
    "HUNGER_HOURS",
    "MINUTES_PER_DAY",
    "MINUTES_PER_HOUR",
    "TURN_MINUTES",
    "abs_minutes",
    "advance",
    "clock_text",
    "fatigue_due",
    "hours_since",
    "period_of",
]

MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR

#: 一次长休的小时数（5e：至少 8 小时，其中 6 小时睡眠）
HOURS_PER_LONG_REST = 8
#: 一次短休的小时数（5e：至少 1 小时）
HOURS_PER_SHORT_REST = 1

#: 一场战斗折算多少分钟。**不按轮数 × 6 秒**：十轮才一分钟，
#: 记了等于没记。这里给的是「这一场过去了多久」，含收尸、包扎、喘气。
COMBAT_MINUTES = 10

#: 一次行动轮（调查 / 交涉 / 磨嘴皮）折算多少分钟。
#: 这类动作没有客观时长，给一个固定值是为了**让时间永远在走**。
TURN_MINUTES = 10

#: 多久没长休要过一次豁免（**房规**：PHB 两版都没有这条，见模块 docstring）
HUNGER_HOURS = 24
#: 疲惫 / 饥饿豁免的 DC（挨饿在 5e 里是 10；24 小时未长休那条是房规，同样取 10）
FATIGUE_DC = 10


def abs_minutes(clock: Any) -> int:
    """把 ``day/hours/minutes`` 折成「开局以来第几分钟」（第 1 天 0 点 = 0）。

    存档里的时间是三元组，而「距上次长休多久」要比大小——
    折成一个绝对数比在两个三元组之间做减法可靠得多。
    """
    day = max(1, int(getattr(clock, "day", 1) or 1))
    hours = max(0, int(getattr(clock, "hours", 0) or 0))
    minutes = max(0, int(getattr(clock, "minutes", 0) or 0))
    return (day - 1) * MINUTES_PER_DAY + hours * MINUTES_PER_HOUR + minutes


def advance(clock: Any, minutes: float) -> int:
    """把时间往前推，返回**跨过了几天**。

    作用于任何有 ``day`` / ``hours`` / ``minutes`` 的对象（目前是 ``EventPool``）。
    负数与 0 一律不动：时间不倒流。
    """
    step = round(max(0.0, float(minutes)))
    if step <= 0:
        return 0
    total = abs_minutes(clock) + step
    days, rest = divmod(total, MINUTES_PER_DAY)
    clock.day = max(1, days + 1)
    clock.hours = rest // MINUTES_PER_HOUR
    clock.minutes = rest % MINUTES_PER_HOUR
    return days


def advance_hours(clock: Any, hours: float) -> int:
    """按小时推进（``advance`` 的薄包装）。"""
    return advance(clock, float(hours) * MINUTES_PER_HOUR)


def period_of(hours: int) -> str:
    """一天里的时段。只用于措辞，不参与结算。

    **切得比「早/午/夜」细一点**：入夜与深夜对玩家是两件事
    （「城门要关了」和「现在出去等于送死」）。
    """
    hour = int(hours) % 24
    if 5 <= hour < 11:
        return "上午"
    if 11 <= hour < 14:
        return "正午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 22:
        return "入夜"
    return "深夜"


def clock_text(clock: Any, *, with_period: bool = True) -> str:
    """``第 3 天 14:20 · 下午``。年份由 :func:`~.events.world_day_text` 负责。"""
    hours = max(0, int(getattr(clock, "hours", 0) or 0)) % 24
    minutes = max(0, int(getattr(clock, "minutes", 0) or 0)) % 60
    day = max(1, int(getattr(clock, "day", 1) or 1))
    head = f"第 {day} 天 {hours:02d}:{minutes:02d}"
    return f"{head} · {period_of(hours)}" if with_period else head


def hours_since(clock: Any, since_abs: int) -> float:
    """距某个绝对时刻过了几小时。``since_abs`` 为 0 时按「开局到现在」算。"""
    return max(0.0, (abs_minutes(clock) - int(since_abs)) / MINUTES_PER_HOUR)


def fatigue_due(clock: Any, last_rest_abs: int, *, hours: int = HUNGER_HOURS) -> bool:
    """是否该做一次「太久没休息」的豁免。

    :param last_rest_abs: 上次长休的绝对分钟（``0`` 表示开局以来还没长休过）。

    **不返回该不该失败**——掷骰是编排层的事。这一层只回答「到点了没有」。
    """
    return hours_since(clock, last_rest_abs) >= max(1, int(hours))
