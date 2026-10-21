"""世界里的时间尺度：**省与省之间走一天，同一个省里各处走几小时**。

这是旅行这件事的**唯一**口径。之前没有这一层，于是「从盐仓码头去旧盐井」和
「从白港去霜牙岭」在数据上是同一件事——都只是「换了个地方」。有了它之后：

==============  ==============  ============================================
从 → 到          时间            依据
==============  ==============  ============================================
同一个省         几小时           省内的路程
相邻两个省       1 天             ``adjacency`` 里的一条边
隔着一层省       2 天            一条边一天，按最短路数
==============  ==============  ============================================

**王国不参与旅行**：``regions`` 是政治单位（谁说了算），不是旅行单位。
两个王国之间的距离由它们的**省**决定——所以邻接表记的是省与省，
这一点见 :func:`~...dnd.world_store.set_world_map`。

为什么不做加权（山地两天、平原半天）：那需要一套地形-速度表，而收益是
「DM 叙述时能说两天而不是一天」——但 DM 本来就在叙述里自己安排行程，
程序给太细的数字反而会和叙述打架。一天的粒度足够支撑「赶不赶得上」这类判断。
"""

from __future__ import annotations

from collections import deque

__all__ = [
    "ONE_DAY",
    "SAME_PROVINCE_TEXT",
    "UNKNOWN_TEXT",
    "province_of",
    "travel_hops",
    "travel_span",
]

#: 同一个省里移动的说法（刻意模糊：具体几小时由 DM 按距离叙述）
SAME_PROVINCE_TEXT = "几小时"

#: 相邻两省之间的天数
ONE_DAY = 1

#: 找不到省时的说法：**不猜**，让 DM 自己按叙述安排
UNKNOWN_TEXT = ""


def province_of(name: str, places: dict[str, str]) -> str:
    """某个地名属于哪个省。

    :param places: 「地名 → 省」映射，由 :func:`~...dnd.world_store.province_index`
        从世界库里算出来（省自己映射到自己，地点映射到 ``meta.parent``）。
        **王国不在这张表里**：王国不是旅行单位。
    :return: 省名；认不出返回空串。
    """
    return str((places or {}).get(str(name or "").strip(), ""))


def travel_hops(
    start: str, goal: str, adjacency: dict[str, list[str]], *, limit: int = 6
) -> int:
    """两省之间的最短跳数（相邻 = 1）；不可达或超过 ``limit`` 返回 0。

    ``limit`` 是「别为了一个远景地名把图全搜一遍」的护栏，不是距离上限。
    """
    start, goal = str(start or "").strip(), str(goal or "").strip()
    if not start or not goal:
        return 0
    if start == goal:
        return 0
    seen = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        node, hops = queue.popleft()
        if hops >= limit:
            continue
        for nxt in adjacency.get(node, []):
            if nxt == goal:
                return hops + 1
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, hops + 1))
    return 0


def travel_span(
    origin: str,
    destination: str,
    *,
    places: dict[str, str],
    adjacency: dict[str, list[str]],
) -> str:
    """两地之间「要走多久」的说法（给 DM 与玩家手册用）。

    :param places: 「地名 → 省」映射（见 :func:`province_of`）。
    :param adjacency: **省与省**的邻接表（相邻 = 走一天）。
    :return: ``几小时`` / ``1 天`` / ``2 天`` ……；**认不出时返回空串**，
        宁可不说，也不要给一个错的数字。
    """
    start = province_of(origin, places)
    goal = province_of(destination, places)
    if not start or not goal:
        return UNKNOWN_TEXT
    if start == goal:
        return SAME_PROVINCE_TEXT
    hops = travel_hops(start, goal, adjacency)
    if hops <= 0:
        return UNKNOWN_TEXT
    return f"{hops * ONE_DAY} 天"
