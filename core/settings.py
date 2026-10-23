"""读取跑团插件设置的薄封装。

设置定义在插件根目录 ``config.py``（``TrpgConfig``），由 ``__init__.py`` 注册到
``ATRI.configs.plugin_config.plugin_config`` 里。这里只做三件事：

- 统一服务名；
- 取不到设置时退回默认值（脚本 / 单测里根本没有初始化 Service）；
- 把 JSON 里可能出现的 ``"true"`` / ``"3"`` 这类写法转成正确的类型。

这样调用方不用关心配置是否已加载、也不用到处写 ``try/except``。
"""

from __future__ import annotations

from typing import Any

from ATRI.configs.plugin_config import PluginConfig

__all__ = [
    "SERVICE_NAME",
    "auto_enemy_turns",
    "battle_map_always",
    "history_events",
    "npc_roster_limit",
    "partial_min_players",
    "partial_narration",
    "use_partial",
    "world_bible_max_chars",
]

#: 与 ``Service("跑团", ...)`` 里的服务名一致（配置按服务名索引）
SERVICE_NAME = "跑团"

#: 默认值（与 ``config.py`` 的 Field 默认值保持一致）
_DEFAULTS: dict[str, Any] = {
    "partial_narration": True,
    "partial_min_players": 3,
    "history_events": 5,
    "npc_roster_limit": 6,
    "battle_map_always": False,
    "world_bible_max_chars": 800,
    "auto_enemy_turns": True,
}


def _raw(key: str) -> Any:
    """读原始配置值；没有配置对象时退回默认值。"""
    default = _DEFAULTS[key]
    config = PluginConfig.get_config(SERVICE_NAME)
    if config is None:
        return default
    try:
        value = config.get(key, default)
    except Exception:  # 配置对象损坏 / 被换掉：不影响主持
        return default
    return default if value is None else value


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "否"}
    return bool(value)


def _as_int(value: Any, *, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def partial_narration() -> bool:
    """是否逐人结算（关掉就退回「全员提交后一次性主持」）。"""
    return _as_bool(_raw("partial_narration"))


def partial_min_players() -> int:
    """逐人结算的最小玩家人数。"""
    return _as_int(_raw("partial_min_players"), low=1, high=99)


def history_events() -> int:
    """每轮塞给 DM 的历史事件条数。"""
    return _as_int(_raw("history_events"), low=0, high=20)


def npc_roster_limit() -> int:
    """提示词里最多列几名具名 NPC。"""
    return _as_int(_raw("npc_roster_limit"), low=0, high=20)


def battle_map_always() -> bool:
    """是否每轮都附战场地图（关闭时只在变化那一轮附）。"""
    return _as_bool(_raw("battle_map_always"))


def world_bible_max_chars() -> int:
    """世界圣经提示词块的字数上限（0 = 不限制）。"""
    return _as_int(_raw("world_bible_max_chars"), low=0, high=5000)


def auto_enemy_turns() -> bool:
    """敌人回合是否由系统自动结算（关掉则敌人不自动出手，只玩家侧结算）。"""
    return _as_bool(_raw("auto_enemy_turns"))


def use_partial(save) -> bool:
    """这一局要不要逐人结算：开关打开且人数达到门槛。"""
    if not partial_narration():
        return False
    return len(getattr(save, "player_ids", []) or []) >= partial_min_players()
