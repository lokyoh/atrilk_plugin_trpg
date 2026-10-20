"""跑团战场棋盘：格子、地形、距离、视线与移动。

战斗原来只有「谁先攻、谁多少血」，没有位置概念，于是「够不够得着」「跑不跑得到」
全凭 DM 一句话。这里补上一块方格战术地图：

- 每格 :data:`CELL_FEET` 尺（5 尺），**斜向也算 5 尺**（与 D&D 5e 的方格规则一致）；
- 地形分空地 / 困难地形 / 半掩体 / 四分之三掩体 / 障碍 / 危险 / 浅水，
  各自带移动消耗、掩体 AC 加值与是否阻挡视线；
- 开战时由系统按敌人数量与场景类型生成（不额外调用模型），DM 可以用
  ``update_battlefield`` 微调，用 ``move`` 让参战者移动（系统按 Dijkstra 算路径
  与消耗，困难地形一步算两格）；
- 攻击 / 施法 / 敌人攻击在结算前用 :func:`range_check` 校验射程与视线，
  掩体按目标所在格给 AC 加值。

本模块只依赖参战者对象上的 ``x`` / ``y`` / ``speed`` 字段（鸭子类型），
不导入 :mod:`.combat`，避免循环引用。
"""

from __future__ import annotations

import hashlib
import heapq
import random
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CELL_FEET",
    "MAX_SIDE",
    "MIN_SIDE",
    "PRESETS",
    "TERRAINS",
    "Battlefield",
    "RangeCheck",
    "battlefield_text",
    "build_battlefield",
    "chebyshev",
    "cover_bonus",
    "distance_feet",
    "has_map",
    "has_position",
    "los_blocked",
    "map_digest",
    "normalize_terrain",
    "plan_move",
    "range_check",
    "render_map",
    "set_terrain",
    "terrain_at",
]

#: 每格的边长（尺）
CELL_FEET = 5
#: 棋盘最小 / 最大边长（格）
MIN_SIDE = 9
MAX_SIDE = 19


# --------------------------------------------------------------------------- #
# 地形
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Terrain:
    """一种地形：移动消耗、掩体加值与通行性。"""

    key: str
    cn: str
    glyph: str
    #: 进入该格消耗几格移动（困难地形 2）
    cost: int = 1
    #: 站在该格获得的 AC 加值（掩体）
    cover: int = 0
    passable: bool = True
    blocks_sight: bool = False
    note: str = ""


TERRAINS: dict[str, Terrain] = {
    "floor": Terrain("floor", "空地", ".", note="可自由行走"),
    "difficult": Terrain(
        "difficult", "困难地形", ",", cost=2, note="每格消耗 2 格移动"
    ),
    "cover_half": Terrain("cover_half", "半掩体", "o", cover=2, note="AC +2"),
    "cover_three": Terrain("cover_three", "四分之三掩体", "O", cover=5, note="AC +5"),
    "blocked": Terrain(
        "blocked",
        "障碍",
        "#",
        passable=False,
        blocks_sight=True,
        note="不可通行、阻挡视线",
    ),
    "hazard": Terrain(
        "hazard", "危险地形", "!", note="进入时由 DM 结算（火、尖刺、深渊边缘…）"
    ),
    "water": Terrain("water", "浅水", "~", cost=2, note="每格消耗 2 格移动"),
}

#: 写法 -> 地形键（给 ``update_battlefield`` 用）
_TERRAIN_ALIASES: dict[str, str] = {
    "空地": "floor",
    "平地": "floor",
    "地面": "floor",
    "floor": "floor",
    "困难": "difficult",
    "困难地形": "difficult",
    "difficult": "difficult",
    "泥泞": "difficult",
    "灌木": "difficult",
    "半掩体": "cover_half",
    "掩体": "cover_half",
    "cover": "cover_half",
    "cover_half": "cover_half",
    "箱子": "cover_half",
    "木桶": "cover_half",
    "四分之三掩体": "cover_three",
    "重掩体": "cover_three",
    "cover_three": "cover_three",
    "石墙": "cover_three",
    "障碍": "blocked",
    "墙": "blocked",
    "石柱": "blocked",
    "阻塞": "blocked",
    "blocked": "blocked",
    "危险": "hazard",
    "危险地形": "hazard",
    "hazard": "hazard",
    "火": "hazard",
    "浅水": "water",
    "水": "water",
    "water": "water",
}


def normalize_terrain(text: str) -> str:
    """把地形写法（中文 / 英文 / 物件名）归一成地形键；认不出返回空串。"""
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    return _TERRAIN_ALIASES.get(raw, "")


def terrain_at(state: Any, x: int, y: int) -> Terrain:
    """取某格地形（没记录的格子都是空地）。"""
    key = (getattr(state, "tiles", None) or {}).get(f"{int(x)},{int(y)}", "floor")
    return TERRAINS.get(key, TERRAINS["floor"])


def set_terrain(state: Any, x: int, y: int, key: str) -> bool:
    """设置某格地形；越界返回 ``False``。"""
    if not in_bounds(state, x, y):
        return False
    if key not in TERRAINS:
        return False
    if not hasattr(state, "tiles") or state.tiles is None:
        state.tiles = {}
    if key == "floor":
        state.tiles.pop(f"{int(x)},{int(y)}", None)
    else:
        state.tiles[f"{int(x)},{int(y)}"] = key
    return True


# --------------------------------------------------------------------------- #
# 场景模板
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Preset:
    """一种战场风格：地形配比与一段可直接用作叙述素材的描述。"""

    key: str
    label: str
    desc: str
    mix: tuple[tuple[str, int], ...] = ()
    #: 固定的额外地形（如水中场景给几片浅水）
    extras: tuple[str, ...] = ()


PRESETS: dict[str, Preset] = {
    "street": Preset(
        "street",
        "石街窄巷",
        "两侧是斑驳的墙面与堆叠的货箱，湿滑的石板路被车辙磨得发亮；"
        "零散的木箱与推车能挡下飞来的箭矢，墙角的水沟让人落脚打滑。",
        mix=(("floor", 12), ("cover_half", 3), ("difficult", 2), ("blocked", 2)),
    ),
    "interior": Preset(
        "interior",
        "室内厅堂",
        "梁柱与家具把空间切得支离破碎，翻倒的长桌、垂落的帷幔既是掩体也是障碍；"
        "头顶的吊灯摇晃，地面散着碎瓷与酒渍。",
        mix=(("floor", 12), ("cover_half", 3), ("cover_three", 1), ("blocked", 3)),
    ),
    "forest": Preset(
        "forest",
        "林地",
        "盘结的树根与齐腰的灌木拖慢脚步，树干与倒木挡住视线；"
        "枝叶间漏下的光斑不停晃动，看不清的地方比看得清的地方更多。",
        mix=(("floor", 10), ("difficult", 4), ("blocked", 4), ("cover_half", 2)),
    ),
    "cave": Preset(
        "cave",
        "洞窟",
        "低矮的岩顶压得人喘不过气，钟乳石与崩塌的岩块割裂了空间；"
        "水声在深处回响，火把只能照亮几步之内。",
        mix=(
            ("floor", 11),
            ("blocked", 4),
            ("difficult", 3),
            ("cover_three", 1),
            ("hazard", 1),
        ),
    ),
    "waterside": Preset(
        "waterside",
        "水岸",
        "脚下是湿滑的砾石与半浸的滩涂，浅水没到脚踝、拖慢每一个动作；"
        "系船桩与散落的货箱提供掩护，水面反射的光晃得人眼花。",
        mix=(
            ("floor", 10),
            ("water", 3),
            ("cover_half", 2),
            ("blocked", 2),
            ("difficult", 2),
        ),
    ),
    "wild": Preset(
        "wild",
        "旷野",
        "碎石与矮草铺开一片起伏的空地，几块巨岩与枯倒的树干零星散落；"
        "没有遮挡的地方很远，谁先冲到石头后面谁就占便宜。",
        mix=(("floor", 14), ("cover_half", 2), ("blocked", 3), ("difficult", 2)),
    ),
}

#: 场景文本里出现这些词就用对应模板（按顺序匹配，先命中先用）
_PRESET_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cave", ("洞", "窟", "墓", "地下", "遗迹", "矿", "地穴", "甬道")),
    ("forest", ("林", "树", "森", "丛林", "灌木", "草原", "荒野")),
    ("waterside", ("河", "湖", "海", "港", "船", "岸", "滩", "水边", "码头")),
    # 街巷放在室内之前：「酒馆后巷」该算窄巷，不算厅堂
    ("street", ("街", "巷", "城", "镇", "集", "市", "广场", "胡同", "桥")),
    ("interior", ("室", "房", "厅", "殿", "塔", "屋", "酒馆", "客栈", "店内", "仓库")),
)


def pick_preset(scene_text: str) -> Preset:
    """按场景文本猜一个战场模板；认不出就用旷野。"""
    text = str(scene_text or "")
    for key, words in _PRESET_WORDS:
        if any(word in text for word in words):
            return PRESETS[key]
    return PRESETS["wild"]


# --------------------------------------------------------------------------- #
# 棋盘生成
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Battlefield:
    """生成结果（写回 :class:`~.combat.CombatState` 用）。"""

    width: int
    height: int
    tiles: dict[str, str] = field(default_factory=dict)
    preset: str = "wild"
    label: str = "旷野"
    desc: str = ""


def has_map(state: Any) -> bool:
    """这场战斗有没有棋盘（老存档没有）。"""
    return (
        int(getattr(state, "width", 0) or 0) > 0
        and int(getattr(state, "height", 0) or 0) > 0
    )


def in_bounds(state: Any, x: int, y: int) -> bool:
    return 0 <= int(x) < int(getattr(state, "width", 0) or 0) and 0 <= int(y) < int(
        getattr(state, "height", 0) or 0
    )


def has_position(combatant: Any) -> bool:
    """参战者是否已经被放到棋盘上（注意坐标 0 是合法值）。"""
    x = getattr(combatant, "x", -1)
    y = getattr(combatant, "y", -1)
    x = -1 if x is None else int(x)
    y = -1 if y is None else int(y)
    return x >= 0 and y >= 0


def chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    """切比雪夫距离（格数）；D&D 方格规则里斜向也走一格。"""
    return max(abs(int(ax) - int(bx)), abs(int(ay) - int(by)))


def distance_feet(a: Any, b: Any) -> int:
    """两个参战者之间的距离（尺）；没站位返回 ``-1``。"""
    if not has_position(a) or not has_position(b):
        return -1
    return chebyshev(a.x, a.y, b.x, b.y) * CELL_FEET


def _free_col(preferred: int, taken: list[int], side: int) -> int:
    """在被占用的列里给 ``preferred`` 找一个最近的空列（找不到就原样返回）。"""
    if preferred not in taken:
        return preferred
    for delta in range(1, side):
        for candidate in (preferred + delta, preferred - delta):
            if 0 <= candidate < side and candidate not in taken:
                return candidate
    return preferred


def _spread(count: int, side: int) -> list[int]:
    """把 ``count`` 个人均匀铺在一行上（返回 0 基列号，尽量不重复）。"""
    if count <= 0:
        return []
    span = max(1, side - 2)
    step = span / (count + 1)
    cols: list[int] = []
    for index in range(count):
        col = min(side - 1, max(0, round(step * (index + 1))))
        cols.append(_free_col(col, cols, side))
    return cols


def _free_for_feature(
    state: Any, x: int, y: int, blocked_cells: set[tuple[int, int]]
) -> bool:
    """这一格能不能放地形（别压在人身上，也别贴着人的起始位置）。"""
    if not in_bounds(state, x, y):
        return False
    return all(chebyshev(x, y, bx, by) > 1 for bx, by in blocked_cells)


def build_battlefield(
    state: Any,
    players: list[Any],
    enemies: list[Any],
    *,
    scene_text: str = "",
    rng: random.Random | None = None,
) -> str:
    """按参战者数量与场景类型生成棋盘，并把人放到两侧；返回环境描述。

    - 边长随人数增长（9~19 格），玩家在下方、敌人在上方；
    - 地形按模板配比随机铺（避开双方起始位置）；
    - 结果直接写进 ``state.width`` / ``state.height`` / ``state.tiles`` 与各参战者的
      ``x`` / ``y``。
    """
    rng = rng or random.Random()
    preset = pick_preset(scene_text)
    total = max(1, len(players) + len(enemies))
    side = min(MAX_SIDE, max(MIN_SIDE, 7 + total))

    state.width = side
    state.height = side
    state.tiles = {}
    state.terrain = preset.key
    state.terrain_label = preset.label

    # —— 站位：双方隔开 15~25 尺居中列阵（一轮移动就能接战，但也不是贴脸开局）——
    gap = 3 + min(2, max(0, len(enemies) - 1))
    gap = min(gap, max(2, side - 3))
    top = max(1, (side - gap) // 2)
    player_cols = _spread(len(players), side)
    for index, combatant in enumerate(players):
        combatant.x = player_cols[index]
        combatant.y = min(side - 2, top + gap)
    enemy_cols = _spread(len(enemies), side)
    for index, combatant in enumerate(enemies):
        combatant.x = enemy_cols[index]
        # 人多时上下两排，免得挤在一起
        combatant.y = top if index < max(1, side - 2) else max(0, top - 1)

    occupied = {(c.x, c.y) for c in [*players, *enemies]}

    # —— 地形 ——
    counts: dict[str, int] = {}
    for key, weight in preset.mix:
        if key == "floor":
            continue
        # 人少也给每种地形留一两块，别让 4 人遭遇战打在一片空地上
        counts[key] = max(1, round(weight * total / 6))
    for key in preset.extras:
        counts[key] = counts.get(key, 0) + 1

    cells = [
        (x, y)
        for y in range(side)
        for x in range(side)
        if (x, y) not in occupied and _free_for_feature(state, x, y, occupied)
    ]
    rng.shuffle(cells)
    cursor = 0
    for key, count in counts.items():
        if key == "floor":
            continue
        for _ in range(count):
            if cursor >= len(cells):
                break
            x, y = cells[cursor]
            cursor += 1
            set_terrain(state, x, y, key)

    return f"{preset.label}：{preset.desc}"


# --------------------------------------------------------------------------- #
# 视线 / 掩体
# --------------------------------------------------------------------------- #


def _line_cells(ax: int, ay: int, bx: int, by: int) -> list[tuple[int, int]]:
    """两个格子之间的连线经过的中间格（不含两端），用 Bresenham 近似。"""
    cells: list[tuple[int, int]] = []
    dx = abs(bx - ax)
    dy = abs(by - ay)
    sx = 1 if ax < bx else -1
    sy = 1 if ay < by else -1
    err = dx - dy
    x, y = ax, ay
    while (x, y) != (bx, by):
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
        if (x, y) != (bx, by):
            cells.append((x, y))
    return cells


def los_blocked(state: Any, a: Any, b: Any) -> tuple[bool, tuple[int, int] | None]:
    """两点之间视线是否被阻挡；返回 ``(是否阻挡, 阻挡格)``。"""
    if not has_map(state) or not has_position(a) or not has_position(b):
        return False, None
    for x, y in _line_cells(int(a.x), int(a.y), int(b.x), int(b.y)):
        if terrain_at(state, x, y).blocks_sight:
            return True, (x, y)
    return False, None


def cover_bonus(state: Any, target: Any) -> int:
    """目标所在格提供的掩体 AC 加值。

    **场地规则修正**（``state.arena_cover_delta``）一并算上：
    「灯火限制」「涨潮」这类规则会让掩体变差——负数就是扣 AC。
    修正值存在战斗状态上，所以本模块不用反向依赖 :mod:`.threat`。
    """
    if not has_map(state) or not has_position(target):
        return 0
    bonus = terrain_at(state, target.x, target.y).cover
    delta = int(getattr(state, "arena_cover_delta", 0) or 0)
    return max(0, bonus + delta)


# --------------------------------------------------------------------------- #
# 射程
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RangeCheck:
    """一次射程 / 视线校验的结论。"""

    ok: bool = True
    #: 距离（尺）；没有棋盘时为 ``-1``
    distance: int = -1
    reason: str = ""
    #: 远程攻击超出常规射程：本次攻击带劣势
    disadvantage: bool = False


def range_check(
    state: Any,
    attacker: Any,
    target: Any,
    *,
    reach: int = 5,
    normal: int = 0,
    max_range: int = 0,
) -> RangeCheck:
    """校验攻击 / 法术能否够到目标（射程 + 视线 + 远程劣势）。

    :param reach: 近战触及（尺）
    :param normal: 远程常规射程（尺）；为 0 表示近战
    :param max_range: 远程最大射程（尺），留空按常规射程算
    """
    if not has_map(state) or not has_position(attacker) or not has_position(target):
        return RangeCheck()
    distance = distance_feet(attacker, target)
    if normal > 0:
        blocked, cell = los_blocked(state, attacker, target)
        if blocked and cell is not None:
            return RangeCheck(
                False, distance, f"视线被（{cell[0] + 1},{cell[1] + 1} 的障碍）挡住了"
            )
        limit = max_range or normal
        if distance > limit:
            return RangeCheck(
                False,
                distance,
                f"目标在 {distance} 尺外，超出最大射程 {limit} 尺——先用 move 靠近",
            )
        return RangeCheck(True, distance, "", disadvantage=distance > normal)
    if distance > reach:
        return RangeCheck(
            False,
            distance,
            f"目标在 {distance} 尺外，超出触及范围 {reach} 尺——先用 move 靠近",
        )
    return RangeCheck(True, distance)


# --------------------------------------------------------------------------- #
# 移动
# --------------------------------------------------------------------------- #

_NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def reachable_cells(
    state: Any,
    start: tuple[int, int],
    budget: int,
    *,
    blocked_cells: set[tuple[int, int]] | None = None,
) -> dict[tuple[int, int], int]:
    """从 ``start`` 出发、在 ``budget`` 尺内能到的格子 -> 花费（尺）。

    八方向 Dijkstra：斜向一格同样算 5 尺；困难地形 / 浅水进一格算 2 格。
    """
    blocked = blocked_cells or set()
    if budget <= 0:
        return {start: 0}
    dist: dict[tuple[int, int], int] = {start: 0}
    heap: list[tuple[int, tuple[int, int]]] = [(0, start)]
    while heap:
        cost, (x, y) = heapq.heappop(heap)
        if cost > dist.get((x, y), 10**9):
            continue
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if not in_bounds(state, nx, ny):
                continue
            if (nx, ny) in blocked:
                continue
            terrain = terrain_at(state, nx, ny)
            if not terrain.passable:
                continue
            total = cost + terrain.cost * CELL_FEET
            if total > budget:
                continue
            if total < dist.get((nx, ny), 10**9):
                dist[(nx, ny)] = total
                heapq.heappush(heap, (total, (nx, ny)))
    return dist


def plan_move(
    state: Any,
    mover: Any,
    dest: tuple[int, int],
    budget: int,
    *,
    blocked_cells: set[tuple[int, int]] | None = None,
) -> tuple[tuple[int, int], int, int]:
    """往 ``dest`` 走，返回 ``(落点, 花费尺数, 落点到目标的格数)``。

    走不到就尽量靠近（在预算内选离目标最近的格子）。
    """
    start = (int(mover.x), int(mover.y))
    cells = reachable_cells(state, start, budget, blocked_cells=blocked_cells)
    if dest in cells:
        return dest, cells[dest], 0
    best = min(
        cells,
        key=lambda cell: (chebyshev(cell[0], cell[1], dest[0], dest[1]), cells[cell]),
    )
    return best, cells[best], chebyshev(best[0], best[1], dest[0], dest[1])


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #


def _glyphs(state: Any) -> dict[str, str]:
    """参战者 -> 地图上的字符（玩家用数字，敌人用字母）。"""
    glyphs: dict[str, str] = {}
    players = [c for c in state.order if c.kind == "player"]
    enemies = [c for c in state.order if c.is_enemy]
    for index, combatant in enumerate(players[:9], 1):
        glyphs[combatant.cid] = str(index)
    for index, combatant in enumerate(enemies[:26]):
        glyphs[combatant.cid] = chr(ord("a") + index)
    return glyphs


def render_map(state: Any) -> str:
    """把棋盘画成等宽字符图（1 基坐标）。"""
    if not has_map(state):
        return ""
    width = int(state.width)
    height = int(state.height)
    glyphs = _glyphs(state)
    cells: dict[tuple[int, int], str] = {}
    for combatant in state.order:
        if not has_position(combatant):
            continue
        if combatant.is_enemy and getattr(combatant, "out", False):
            continue
        if combatant.kind == "player" and getattr(combatant, "out", False):
            continue
        cells[(int(combatant.x), int(combatant.y))] = glyphs.get(combatant.cid, "?")

    lines = ["    " + "".join(f"{x + 1:>3}" for x in range(width))]
    for y in range(height):
        row = []
        for x in range(width):
            row.append(f"{cells.get((x, y)) or terrain_at(state, x, y).glyph:>3}")
        lines.append(f"{y + 1:>3} " + "".join(row))
    return "\n".join(lines)


def legend_text(state: Any) -> str:
    """图例：参战者编号 + 场上出现过的地形。"""
    if not has_map(state):
        return ""
    glyphs = _glyphs(state)
    parts: list[str] = []
    for combatant in state.order:
        glyph = glyphs.get(combatant.cid)
        if not glyph:
            continue
        role = "玩家" if combatant.kind == "player" else "敌人"
        parts.append(f"{glyph}={combatant.name}（{role}）")
    used = {terrain_at(state, x, y).glyph for x, y in _all_pairs(state)}
    terrain_parts = [
        f"{t.glyph} {t.cn}" + (f"（{t.note}）" if t.note else "")
        for t in TERRAINS.values()
        if t.glyph in used
    ]
    lines = []
    if parts:
        lines.append("图例：" + "　".join(parts))
    if terrain_parts:
        lines.append("地形：" + "　".join(terrain_parts))
    return "\n".join(lines)


def _all_pairs(state: Any):
    for y in range(int(getattr(state, "height", 0) or 0)):
        for x in range(int(getattr(state, "width", 0) or 0)):
            yield x, y


def map_digest(state: Any) -> str:
    """战场指纹：尺寸 + 地形 + 参战者站位。

    用来判断「这一轮要不要重画地图」——布局没变就只给坐标和距离，
    省掉每轮重复的十几行地图（老存档没有棋盘时返回空串）。
    """
    if not has_map(state):
        return ""
    tiles = ";".join(
        f"{key}={value}"
        for key, value in sorted((getattr(state, "tiles", {}) or {}).items())
    )
    spots = ";".join(
        f"{c.cid}@{c.x},{c.y}" + ("x" if getattr(c, "out", False) else "")
        for c in getattr(state, "order", [])
    )
    raw = (
        f"{state.width}x{state.height}|{getattr(state, 'terrain', '')}|{tiles}|{spots}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def battlefield_text(state: Any, *, with_map: bool = True) -> str:
    """战场现状：尺寸、地形风格、地图（可选）、坐标、图例与各人对目标的距离。"""
    if not has_map(state):
        return ""
    label = str(getattr(state, "terrain_label", "") or "").strip()
    head = (
        f"【战场】{state.width}×{state.height} 格（每格 {CELL_FEET} 尺，斜向同算 "
        f"{CELL_FEET} 尺）" + (f" · {label}" if label else "")
    )
    lines = [head]
    if with_map:
        lines.append(render_map(state))
        legend = legend_text(state)
        if legend:
            lines.append(legend)
    else:
        lines.append("（地图布局与上一轮相同，不再重画；坐标见下面的「位置」行）")

    spots = [
        f"{c.name} {int(c.x) + 1},{int(c.y) + 1}"
        for c in state.order
        if has_position(c) and not (c.is_enemy and getattr(c, "out", False))
    ]
    if spots:
        lines.append("位置：" + "；".join(spots))

    players = [c for c in state.order if c.kind == "player"]
    enemies = [c for c in state.order if c.is_enemy and not c.out and c.hp > 0]
    if players and enemies:
        lines.append("距离（尺）：")
        for player in players:
            if not has_position(player):
                continue
            pairs = sorted(
                ((distance_feet(player, enemy), enemy) for enemy in enemies),
                key=lambda item: item[0],
            )
            shown = pairs if len(pairs) * len(players) <= 8 else pairs[:2]
            text = "；".join(
                f"{enemy.name} {dist}" for dist, enemy in shown if dist >= 0
            )
            if text:
                lines.append(f"  {player.name} → {text}")
    movers = []
    for player in players:
        if not has_position(player):
            continue
        used = int(getattr(player, "move_used", 0) or 0)
        speed = int(getattr(player, "speed", 30) or 30)
        movers.append(f"{player.name} 剩余 {max(0, speed - used)}/{speed} 尺")
    if movers:
        lines.append("移动：" + "；".join(movers))
    return "\n".join(lines)
