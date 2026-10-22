"""世界里的时间尺度：**同一个省里走几小时，跨省按两侧区域的「跨越难度」算**。

这是旅行这件事的**唯一**口径。

==============================  ==========================================
从 → 到                          时间
==============================  ==========================================
同一个省                         几小时
跨两个省                         按两侧难度取**较大者**，见 :data:`CROSSING_LEVELS`
隔着一层省                       逐跳相加（每一跳各自取较大者）
==============================  ==========================================

## 为什么取「较大者」而不是平均

瓶颈在**难的那一边**。对面再平坦，你也得先翻过这座山。平均会把
「从平原进入绝壁」算得和「从绝壁回到平原」一样轻松，那是反的。

## 为什么现在要加权（上一版的判断作废）

上一版这里写着「不做加权：那需要一套地形-速度表，而一天的粒度足够」。
**这个判断是错的**，错在它假设了「境内的路总是好走的」。

一旦承认**王国可以是无人区**（没有政体、没有驿站、没有维护的路），
这个假设就塌了：没有人的地方往往也没有路，跨越难度只能由地形给——
裂谷、雪山、盐碱滩、塌陷带。同一个「相邻省」，可能是半天，也可能是八天。

所以现在的口径是：**难度挂在区域上（1–5），跨越取两侧较大者**。

**王国不参与旅行**：``regions`` 是政治单位（谁说了算），不是旅行单位。
两个王国之间的距离由它们的**省**决定——所以邻接表记的是省与省，
这一点见 :func:`~...dnd.world_store.set_world_map`。
"""

from __future__ import annotations

from collections import deque
from itertools import pairwise

__all__ = [
    "CROSSING_LEVELS",
    "DEFAULT_CROSSING",
    "MAX_CROSSING",
    "ONE_DAY",
    "SAME_PROVINCE_HOURS",
    "SAME_PROVINCE_TEXT",
    "SAME_SITE_TEXT",
    "UNKNOWN_TEXT",
    "crossing_days",
    "crossing_level",
    "province_of",
    "travel_days",
    "travel_hops",
    "travel_path",
    "travel_span",
]

#: 同一个省里移动的说法（刻意模糊：具体几小时由 DM 按距离叙述）
SAME_PROVINCE_TEXT = "几小时"

#: 同一个**场所**里挪位置的说法（主甲板 → 船长室）。
#: 跟「几小时」分开是因为语义不同：那不是赶路，是在自家地方里走了几步。
SAME_SITE_TEXT = "片刻"

#: 同一个省里移动的**记账值**（天）。只用于时间累加，不用于展示：
#: 展示用 :data:`SAME_PROVINCE_TEXT`。取 3 小时是「几小时」的中间值。
SAME_PROVINCE_HOURS = 3

#: 难度 2（好走）跨越一次的天数。保留这个常量是为了兼容旧引用。
ONE_DAY = 1

#: 找不到省时的说法：**不猜**，让 DM 自己按叙述安排
UNKNOWN_TEXT = ""

#: 跨越难度 -> (中文说法, 越过它要几天)
CROSSING_LEVELS: dict[int, tuple[str, float]] = {
    1: ("通衢", 0.5),
    2: ("好走", 1.0),
    3: ("难行", 2.0),
    4: ("险阻", 4.0),
    5: ("几乎不可越", 8.0),
}

#: 区域没标难度时按「好走」算。旧世界没有这个字段，于是行为与上一版一致。
DEFAULT_CROSSING = 2

MAX_CROSSING = 5


def province_of(name: str, places: dict[str, str]) -> str:
    """某个地名属于哪个省。

    :param places: 「地名 → 省」映射，由 :func:`~...dnd.world_store.province_index`
        从世界库里算出来（省自己映射到自己，地点映射到 ``meta.parent``）。
        **王国不在这张表里**：王国不是旅行单位。
    :return: 省名；认不出返回空串。
    """
    return str((places or {}).get(str(name or "").strip(), ""))


def crossing_level(province: str, crossing: dict[str, int] | None) -> int:
    """某个区域的跨越难度（1–5）；没标或标错都按 :data:`DEFAULT_CROSSING`。"""
    raw = (crossing or {}).get(str(province or "").strip())
    try:
        level = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        level = DEFAULT_CROSSING
    return max(1, min(level, MAX_CROSSING))


def crossing_days(level: int) -> float:
    """一个难度档对应几天。"""
    return CROSSING_LEVELS.get(level, CROSSING_LEVELS[DEFAULT_CROSSING])[1]


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


def travel_path(
    start: str,
    goal: str,
    adjacency: dict[str, list[str]],
    *,
    limit: int = 6,
) -> list[str]:
    """两省之间的最短路径（含首尾）；不可达返回空列表。

    为什么要路径而不只要跳数：每一跳的耗时**不一样**，
    「一跳通衢」和「一跳险阻」差八倍，只有拿到经过哪些省才算得出来。
    """
    start, goal = str(start or "").strip(), str(goal or "").strip()
    if not start or not goal:
        return []
    if start == goal:
        return [start]

    prev: dict[str, str] = {}
    seen = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        node, hops = queue.popleft()
        if hops >= limit:
            continue
        for nxt in adjacency.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = node
            if nxt == goal:
                path = [goal]
                cur = goal
                while cur != start:
                    cur = prev[cur]
                    path.append(cur)
                return list(reversed(path))
            queue.append((nxt, hops + 1))
    return []


def travel_days(
    origin: str,
    destination: str,
    *,
    places: dict[str, str],
    adjacency: dict[str, list[str]],
    crossing: dict[str, int] | None = None,
) -> float:
    """两地之间要走几天（**记账用**，0 表示算不出来）。

    同省返回 :data:`SAME_PROVINCE_HOURS` 折算的天数；跨省逐跳取
    ``max(两侧难度)`` 再相加。
    """
    start = province_of(origin, places)
    goal = province_of(destination, places)
    if not start or not goal:
        return 0.0
    if start == goal:
        return SAME_PROVINCE_HOURS / 24.0

    path = travel_path(start, goal, adjacency)
    if not path:
        return 0.0

    total = 0.0
    for here, there in pairwise(path):
        level = max(crossing_level(here, crossing), crossing_level(there, crossing))
        total += crossing_days(level)
    return total


def _format_days(days: float) -> str:
    """天数 -> 说法。**只在跨省时用**（同省有自己的说法）。"""
    if days <= 0:
        return UNKNOWN_TEXT
    if days < 0.75:
        return "半天"
    if abs(days - round(days)) < 0.01:
        return f"{round(days)} 天"
    return f"{days:g} 天"


def travel_span(
    origin: str,
    destination: str,
    *,
    places: dict[str, str],
    adjacency: dict[str, list[str]],
    crossing: dict[str, int] | None = None,
    sites: dict[str, str] | None = None,
) -> str:
    """两地之间「要走多久」的说法（给 DM 与玩家手册用）。

    :param places: 「地名 → 省」映射（见 :func:`province_of`）。
    :param adjacency: **省与省**的邻接表。
    :param crossing: 「省 → 跨越难度 1–5」。留空则全部按「好走」算，
        也就是旧世界的行为（一跳一天）。
    :param sites: 「场景 → 场所」映射（见
        :func:`~.world_store.scene_site_index`）。两个场景同属一个场所时
        返回「片刻」——**那不是赶路**，是在同一个地方里挪了几步。
    :return: ``几小时`` / ``半天`` / ``1 天`` / ``4 天`` ……；
        **认不出时返回空串**，宁可不说，也不要给一个错的数字。
    """
    start_site = str((sites or {}).get(str(origin or "").strip()) or "")
    goal_site = str((sites or {}).get(str(destination or "").strip()) or "")
    if start_site and start_site == goal_site:
        return SAME_SITE_TEXT
    start = province_of(origin, places)
    goal = province_of(destination, places)
    if not start or not goal:
        return UNKNOWN_TEXT
    if start == goal:
        return SAME_PROVINCE_TEXT
    return _format_days(
        travel_days(
            origin, destination, places=places, adjacency=adjacency, crossing=crossing
        )
    )
