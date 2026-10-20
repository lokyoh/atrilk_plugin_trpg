"""难度分级：把「这件事有多难」折算成 5e 的 DC。

原来的 :meth:`~.judge.DndRuleJudge._suggest_check` 对所有行动一律给 DC 15——
在四岁小孩面前潜行和在铁门前强推是同一个难度，这既不像现实跑团，也让玩家的
检定结果失去意义（低难度本该稳过、高难度该险过）。

5e（PHB）的四档基准 DC：

===========  ======  ==================================
难度          DC      典型场景
===========  ======  ==================================
非常简单      5      婴儿抓水壶；大衣的扣子
容易          10     爬上矮墙；点燃已备好的火把
中等          15     攀爬普通屋顶；徒手推开木门
困难          20     跳过一丈宽的壕沟；把木门连门框拆下
非常困难      25     徒手折断铁栅；从城墙跳下不受伤
极其困难      30     一击劈开石墙；徒手接住落下的巨斧
===========  ======  ==================================

模块给出两层：

- :data:`DC_BANDS` —— 难度档位到 DC 的映射（对外，供 ``query_data`` 展示）；
- :func:`dc_for` —— 实际算 DC 的入口：先看玩家话里有没有**难度描述词**
  （「偷偷」「小心翼翼」「拼尽全力」「徒手」「轻松」……），有就按它定档；
  没有就回落到该技能的**基准难度** :data:`SKILL_BASE_DC`。

这样分档的知识全在代码里，**不占提示词**。
"""

from __future__ import annotations

import re

__all__ = [
    "DC_BANDS",
    "DIFFICULTY_LEVELS",
    "SKILL_BASE_DC",
    "dc_for",
    "describe_dc",
    "guess_difficulty",
]

#: 5e 的难度档位 → DC。
DC_BANDS: tuple[tuple[str, int], ...] = (
    ("非常简单", 5),
    ("容易", 10),
    ("中等", 15),
    ("困难", 20),
    ("非常困难", 25),
    ("极其困难", 30),
)

#: 档位名 → DC（给内部查用）
DIFFICULTY_LEVELS: dict[str, int] = dict(DC_BANDS)

#: 每项技能的**基准难度**（DC 15，即「中等」）。
#:
#: 几乎所有 5e 技能的基准都是中等，例外主要是「见多识广」类：
#: 需要专门训练才能吃下的动作（攀爬、跳跃、唬骗）实际偏难；
#: 而察言观色、追踪这类日常做得到的偏容易。这一项决定了「玩家什么
#: 都不说难度词」时的落点。
SKILL_BASE_DC: dict[str, int] = {
    # 偏难：需要专门训练，且失败代价高
    "athletics": 15,  # 运动：攀爬、游泳、推举
    "acrobatics": 15,  # 体操：平衡、翻滚
    "sleight_of_hand": 15,  # 手上功夫：开锁、偷摸
    "stealth": 15,  # 潜行
    "deception": 15,  # 欺瞒
    # 偏容易：日常做得到的事
    "perception": 13,  # 感知：察觉、倾听、搜索
    "investigation": 15,
    "insight": 13,  # 洞察：看穿动机
    "medicine": 13,  # 医疗：稳定伤势
    "survival": 13,  # 生存：追踪、辨识方向
    "animal_handling": 13,  # 驯养
    "persuasion": 15,
    "intimidation": 15,
    "history": 13,  # 歷史
    "arcana": 15,  # 秘法
    "nature": 13,  # 自然
    "religion": 13,  # 宗教
    "performance": 15,  # 表演
}

#: 默认基准（表里没有的技能一律按中等）
DEFAULT_BASE_DC = 15

#: 难度描述词 → DC 偏移（叠加在基准难度上）。
#:
#: 命中多个时取**最强**的那一个（取绝对值最大的偏移），而不是叠加——
#: 「偷偷地轻轻推开一扇生锈的铁门」不该因为两个词就变成极其困难。
_DIFFICULTY_OFFSETS: tuple[tuple[str, int], ...] = (
    # —— 明确变难 ——
    (r"(极其|极|非常|特别|超)(难|困难)", +15),
    (r"(拼尽|用尽|全力)", +8),
    (r"(勉强|奋力|艰难)", +5),
    (r"(徒手|空手|赤手)", +5),
    (r"(试图|尝试|冒险|硬闯)", +5),
    (r"(翻)(越|过)(.+)(城墙|壕沟|深渊|峭壁|屋顶|高墙)", +8),
    # —— 明确变易 ——
    (r"(轻|悄悄|无声|小心|小心地|缓缓|轻松|从容)", -5),
    (r"(轻松地|很容易|简单)", -8),
    (r"(悄悄靠近|摸过去|偷偷)", -3),
)

#: 直接指定难度档位的写法（玩家 / DM 自己写了档位名或 DC）。
#: 认 ``dc18`` / ``dc=18`` / ``DC 18`` / ``难度18`` 这几种写法。
_EXPLICIT_DC_RE = re.compile(
    r"(?:d\s*c\s*[：:=]?\s*|难度\s*[：:=]?\s*)(\d{1,2})",
    re.IGNORECASE,
)


def guess_difficulty(text: str, base_dc: int = DEFAULT_BASE_DC) -> int:
    """按叙述里的难度描述词调整 DC。

    :param text: 玩家 / DM 的行动描述
    :param base_dc: 起始难度（技能基准）
    :return: 调整后的 DC，钳制在 1~30 之间
    """
    body = str(text or "")
    if not body:
        return base_dc
    # 玩家直接写了 DC 就照他的来
    explicit = _EXPLICIT_DC_RE.search(body)
    if explicit:
        return max(1, min(30, int(explicit.group(1))))

    best = 0
    for pattern, offset in _DIFFICULTY_OFFSETS:
        if re.search(pattern, body):
            best = offset if abs(offset) > abs(best) else best
    if not best:
        return base_dc
    return max(1, min(30, base_dc + best))


def dc_for(text: str, skill_key: str = "") -> int:
    """给一次行动定 DC。

    :param text: 行动描述（用于识别难度词）
    :param skill_key: 技能内部键；空串或表里没有时按 :data:`DEFAULT_BASE_DC`
    :return: 5e 的 DC 值
    """
    base = SKILL_BASE_DC.get(str(skill_key or ""), DEFAULT_BASE_DC)
    return guess_difficulty(text, base)


def describe_dc(dc: int) -> str:
    """DC → 档位名（DM 叙述时好说「这是一次困难的检定」）。"""
    value = max(5, min(30, int(dc or DEFAULT_BASE_DC)))
    name = ""
    for label, band in DC_BANDS:
        if value >= band:
            name = label
    return name
