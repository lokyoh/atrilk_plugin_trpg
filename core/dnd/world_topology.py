"""跑团世界地图的**拓扑生成器**（纯本地，不调用模型）。

原来的做法是让模型「一边编故事一边编地理」，再正则解析它自己写的相邻关系 JSON——
于是经常出现链状走廊、孤立地区、A 与 B 相邻但 B 不写 A，以及解析失败后地区名退化成
JSON 键名。这里把「地理」从模型手里拿走：

1. 在网格上撒下 ``count`` 个种子（最远点采样，保证初始就很散）；
2. 多源 BFS 把每个格子划给最近的种子——**每一块都是连续的**，不会出现飞地；
3. 相邻关系 = 共享一条边的两块地（网格的天然性质：平面、对称、无孤立点）；
4. 再算出每块地的方位（东南 / 中……）、角色（枢纽 / 普通 / 边陲）与网格坐标。

模型只负责给这些「格子」填名字与内容（见 :func:`prompt_text` 产出的布局说明），
既不会把地图画歪，也不会漏掉邻接关系。
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = [
    "MAX_REGIONS",
    "MIN_REGIONS",
    "Region",
    "Topology",
    "build_topology",
    "lint_world",
    "prompt_text",
    "render",
]

#: 一张世界图里的地区数量范围（太少没有探索感，太多记不住）
MIN_REGIONS = 3
MAX_REGIONS = 12

#: 每个地区平均占几格
_CELLS_PER_REGION = 6


@dataclass(slots=True)
class Region:
    """一个地区：由若干相邻格子组成的一块连续土地。"""

    #: 0 基序号，同时决定文档里的「地区一 / 地区二」顺序
    index: int
    #: 格子集合（网格坐标）
    cells: tuple[tuple[int, int], ...]
    #: 质心（格坐标，用于算方位与以后画图）
    cx: float
    cy: float
    #: 相邻地区数
    degree: int = 0
    #: 枢纽 / 普通 / 边陲
    role: str = "普通"
    #: 方位：北 / 西南 / 中……
    bearing: str = "中"

    @property
    def label(self) -> str:
        """文档里用的占位名（模型会把它换成真实地名）。"""
        return f"地区{_CN_NUMBERS[self.index] if self.index < len(_CN_NUMBERS) else self.index + 1}"

    def cell_count(self) -> int:
        return len(self.cells)


_CN_NUMBERS = (
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
    "十一",
    "十二",
)


@dataclass(slots=True)
class Topology:
    """一整张世界图的拓扑。"""

    width: int
    height: int
    regions: list[Region]
    #: 序号 -> 相邻序号（双向、对称）
    adjacency: dict[int, set[int]] = field(default_factory=dict)
    seed: int = 0

    def neighbors(self, index: int) -> list[int]:
        return sorted(self.adjacency.get(index, set()))

    @property
    def count(self) -> int:
        return len(self.regions)


def _grid_size(count: int) -> tuple[int, int]:
    """按地区数量估一个 4:3 的网格，保证每块地至少 4 格。"""
    cells = max(count * _CELLS_PER_REGION, count * 4)
    width = max(4, round((cells * 4 / 3) ** 0.5))
    height = max(3, round(width * 3 / 4))
    while width * height < count * 4:
        width += 1
        height = max(3, round(width * 0.75))
    return width, height


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _seed_cells(
    width: int, height: int, count: int, rng: random.Random
) -> list[tuple[int, int]]:
    """最远点采样：每个新种子都取「离已有种子最远」的格子，初始分布足够散。"""
    cells = [(x, y) for y in range(height) for x in range(width)]
    seeds = [rng.choice(cells)]
    while len(seeds) < count:
        best: tuple[int, int] | None = None
        best_score = -1
        for cell in cells:
            score = min(_dist2(cell, seed) for seed in seeds)
            if score > best_score or (score == best_score and rng.random() < 0.5):
                best, best_score = cell, score
        if best is None:  # 网格比地区数还小（理论上不会发生）
            break
        seeds.append(best)
    return seeds


def _assign_cells(
    width: int, height: int, seeds: list[tuple[int, int]]
) -> list[list[int]]:
    """多源 BFS 划分：每块地都从自己的种子长出来，因此必然连续。"""
    owner = [[-1] * width for _ in range(height)]
    queue: deque[tuple[int, int, int]] = deque()
    for index, (x, y) in enumerate(seeds):
        owner[y][x] = index
        queue.append((x, y, index))
    while queue:
        x, y, index = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and owner[ny][nx] < 0:
                owner[ny][nx] = index
                queue.append((nx, ny, index))
    return owner


def _bearing(cx: float, cy: float, width: int, height: int) -> str:
    """质心相对地图中心的方位（北 / 东南 / 中……）。"""
    dx = cx - (width - 1) / 2
    dy = cy - (height - 1) / 2
    vertical = "北" if dy < -height * 0.18 else ("南" if dy > height * 0.18 else "")
    horizontal = "西" if dx < -width * 0.18 else ("东" if dx > width * 0.18 else "")
    # 中文习惯是「东北 / 西南」，横向在前
    return horizontal + vertical or "中"


def _role(degree: int) -> str:
    if degree <= 1:
        return "边陲"
    if degree >= 3:
        return "枢纽"
    return "普通"


def build_topology(
    count: int,
    *,
    seed: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> Topology:
    """生成一张世界图：``count`` 块连续、对称相邻、没有孤立点的地区。"""
    count = max(MIN_REGIONS, min(MAX_REGIONS, int(count)))
    seed = int(seed) if seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    if width and not height:
        height = max(3, round(width * 3 / 4))
    elif height and not width:
        width = max(4, round(height * 4 / 3))
    if not width or not height:
        width, height = _grid_size(count)
    width = max(width, count)
    height = max(height, 3)

    seeds = _seed_cells(width, height, count, rng)
    owner = _assign_cells(width, height, seeds)

    cells: list[list[tuple[int, int]]] = [[] for _ in range(count)]
    for y in range(height):
        for x in range(width):
            index = owner[y][x]
            if 0 <= index < count:
                cells[index].append((x, y))

    adjacency: dict[int, set[int]] = {index: set() for index in range(count)}
    for y in range(height):
        for x in range(width):
            here = owner[y][x]
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx >= width or ny >= height:
                    continue
                there = owner[ny][nx]
                if here != there and here >= 0 and there >= 0:
                    adjacency[here].add(there)
                    adjacency[there].add(here)

    regions: list[Region] = []
    for index in range(count):
        block = cells[index] or [seeds[index]]
        cx = sum(cell[0] for cell in block) / len(block)
        cy = sum(cell[1] for cell in block) / len(block)
        regions.append(
            Region(
                index=index,
                cells=tuple(block),
                cx=cx,
                cy=cy,
                degree=len(adjacency[index]),
                role=_role(len(adjacency[index])),
                bearing=_bearing(cx, cy, width, height),
            )
        )
    return Topology(
        width=width,
        height=height,
        regions=regions,
        adjacency=adjacency,
        seed=seed,
    )


def render(topology: Topology) -> str:
    """把拓扑画成等宽字符图（调试 / 测试用；每个地区一个字母）。"""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    grid = [[" " for _ in range(topology.width)] for _ in range(topology.height)]
    for region in topology.regions:
        glyph = letters[region.index % len(letters)]
        for x, y in region.cells:
            grid[y][x] = glyph
    return "\n".join("".join(row) for row in grid)


def prompt_text(topology: Topology) -> str:
    """给模型的布局说明：只能按这个顺序与邻接写，不许自己改地理。"""
    lines = [
        f"大地图由系统生成，共 {topology.count} 个地区（{topology.width}×{topology.height} 的网格），"
        "**邻接关系与顺序不可更改**：",
    ]
    for region in topology.regions:
        neighbors = "、".join(
            topology.regions[i].label for i in topology.neighbors(region.index)
        )
        lines.append(
            f"- {region.label}：{region.bearing}部，{region.role}，"
            f"{region.cell_count()} 格，与 {neighbors} 相邻"
        )
    return "\n".join(lines)


def lint_world(topology: Topology, names: Iterable[str], text: str = "") -> list[str]:
    """生成后自检：名称数量、重名、孤立点、邻接是否写进正文。

    返回问题列表（空 = 通过）。**不做模型重试**，只把问题交给调用方 log / 展示，
    避免"为了修一个错再花一整个世界的钱"。
    """
    issues: list[str] = []
    name_list = [str(name).strip() for name in names if str(name).strip()]

    if len(name_list) != topology.count:
        issues.append(
            f"地区名数量不符：拓扑 {topology.count} 个，模型给了 {len(name_list)} 个"
        )
    lowered = [name.lower() for name in name_list]
    duplicates = {name for name in lowered if lowered.count(name) > 1}
    if duplicates:
        issues.append("地区重名：" + "、".join(sorted(duplicates)))

    for region in topology.regions:
        if region.degree == 0:
            issues.append(f"{region.label} 与任何地区都不相邻（拓扑异常）")
        elif region.degree <= 1 and topology.count > 3:
            issues.append(f"{region.label} 只有 {region.degree} 个邻接，可能是地图边角")

    # 正文里至少要让每个地区的邻接名字出现（不做严格逐条校验，避免误报）
    if text.strip():
        for index, name in enumerate(name_list[: topology.count]):
            if not name:
                continue
            for neighbor in topology.neighbors(index):
                if neighbor >= len(name_list):
                    continue
                other = name_list[neighbor]
                if other and other not in text:
                    issues.append(f"正文里没出现邻接地区「{other}」")
                    break
    return issues
