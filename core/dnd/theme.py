"""图片配色主题：角色卡 / 战场图 / 玩家面板共用的一套设计令牌。

渲染层（``card.to_html``、``image_render``）里不写死颜色，只用 CSS 变量；
换肤只改这里。三套预设各自定义完整的令牌，切主题把 ``DEFAULT_THEME`` 换个键
（或设环境变量 ``TRPG_IMAGE_THEME``）即可，不用碰任何布局。

令牌约定：
- 中性色四档：``page`` / ``card1``·``card2``（卡面渐变）/ ``border``
- 文字三档：``text``（主）/ ``text2``（次要）/ ``text3``（最弱）
- 强调：``accent``（数值、小标题、进度条高光）
- 头部：``header1``·``header2``（渐变）
- 控件：``tag_bg``/``tag_border``（标签）、``track``/``hp1``·``hp2``（血条）
- 棋盘：``board_bg`` + ``terrain``（七个地形键）+ ``player``/``enemy``（token）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = ["DEFAULT_THEME", "THEMES", "Theme", "css_vars", "get_theme"]


@dataclass(frozen=True)
class Theme:
    """一套配色令牌。字段名即 CSS 变量名（``--page`` 等）。"""

    key: str
    label: str
    page: str
    card1: str
    card2: str
    border: str
    text: str
    text2: str
    text3: str
    accent: str
    header1: str
    header2: str
    tag_bg: str
    tag_border: str
    track: str
    hp1: str
    hp2: str
    board_bg: str
    player: str
    enemy: str
    terrain: dict[str, str] = field(default_factory=dict)


THEMES: dict[str, Theme] = {
    # 墨金：暖褐纸墨 + 琥珀，最接近奇幻书页
    "ink": Theme(
        key="ink",
        label="墨金",
        page="#14100e",
        card1="#241d17",
        card2="#191310",
        border="#8a6a3f",
        text="#f5e9d6",
        text2="#cbb99c",
        text3="#a28d72",
        accent="#e8b04b",
        header1="#6b4a22",
        header2="#3d2a14",
        tag_bg="rgba(232,176,75,.16)",
        tag_border="rgba(232,176,75,.45)",
        track="#3b2e22",
        hp1="#e05252",
        hp2="#e8b04b",
        board_bg="#0f0c09",
        player="#5b8ac9",
        enemy="#cc4a41",
        terrain={
            "floor": "#2b2219",
            "difficult": "#3f3320",
            "cover_half": "#263a2d",
            "cover_three": "#1d4636",
            "blocked": "#12100d",
            "hazard": "#4b2420",
            "water": "#1e3448",
        },
    ),
    # 深青：冷色幽光，适合地城 / 夜间场景
    "teal": Theme(
        key="teal",
        label="深青",
        page="#0b1416",
        card1="#16302e",
        card2="#101f20",
        border="#3f7f78",
        text="#e3f5f1",
        text2="#a9cdc6",
        text3="#7fa8a1",
        accent="#5fdcc4",
        header1="#1d5a54",
        header2="#123b38",
        tag_bg="rgba(95,220,196,.16)",
        tag_border="rgba(95,220,196,.45)",
        track="#1f3d3b",
        hp1="#e0575b",
        hp2="#5fdcc4",
        board_bg="#081112",
        player="#4fa8d8",
        enemy="#dd5f5f",
        terrain={
            "floor": "#17312f",
            "difficult": "#3a3220",
            "cover_half": "#22463a",
            "cover_three": "#1a5245",
            "blocked": "#0b1718",
            "hazard": "#4a2427",
            "water": "#1b3c52",
        },
    ),
    # 血墨：暗红 + 金，战斗氛围最重
    "crimson": Theme(
        key="crimson",
        label="血墨",
        page="#120c0f",
        card1="#2b1a21",
        card2="#1a1014",
        border="#7d3a45",
        text="#f7e7e9",
        text2="#cda8af",
        text3="#a3818a",
        accent="#f2b24c",
        header1="#6b2231",
        header2="#3c1220",
        tag_bg="rgba(242,178,76,.16)",
        tag_border="rgba(242,178,76,.45)",
        track="#3c232b",
        hp1="#e04858",
        hp2="#f2b24c",
        board_bg="#0e0a0c",
        player="#5b8fd4",
        enemy="#e0525f",
        terrain={
            "floor": "#2b1c22",
            "difficult": "#3e3020",
            "cover_half": "#24392d",
            "cover_three": "#1e4636",
            "blocked": "#110d10",
            "hazard": "#57212b",
            "water": "#1e3448",
        },
    ),
}

#: 默认配色键；也可以设环境变量 ``TRPG_IMAGE_THEME`` 临时换
DEFAULT_THEME = "ink"


def get_theme(key: str | None = None) -> Theme:
    """取一套主题：显式 ``key`` > 环境变量 > :data:`DEFAULT_THEME`。"""
    name = key or os.environ.get("TRPG_IMAGE_THEME") or DEFAULT_THEME
    return THEMES.get(name) or THEMES[DEFAULT_THEME]


def css_vars(theme: Theme | None = None) -> str:
    """把主题摊成 ``:root{--page:...;--t-floor:...}`` 一段，塞进 ``<style>`` 最前面。"""
    t = theme or get_theme()
    pairs: list[tuple[str, str]] = [
        ("page", t.page),
        ("card1", t.card1),
        ("card2", t.card2),
        ("border", t.border),
        ("text", t.text),
        ("text2", t.text2),
        ("text3", t.text3),
        ("accent", t.accent),
        ("header1", t.header1),
        ("header2", t.header2),
        ("tag-bg", t.tag_bg),
        ("tag-border", t.tag_border),
        ("track", t.track),
        ("hp1", t.hp1),
        ("hp2", t.hp2),
        ("board-bg", t.board_bg),
        ("player", t.player),
        ("enemy", t.enemy),
    ]
    for key, color in t.terrain.items():
        pairs.append((f"t-{key}", color))
    body = ";".join(f"--{name}:{value}" for name, value in pairs)
    return f":root{{{body}}}"
