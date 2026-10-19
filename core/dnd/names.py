"""玩家显示名：优先用 lk 插件里绑定的名称。

跑团内部一律用 QQ 号当玩家 ID（存档、角色卡、事件日志、等待名单都按它索引），
只在**显示**与**提示词**里换成 lk 绑定的名字——没绑定 lk 名称的玩家退回角色名
或 QQ 号，避免在 DM 的叙述里出现一串数字。
"""

from __future__ import annotations

__all__ = ["lk_name", "player_name"]


def lk_name(user_id: str | int) -> str:
    """玩家在 lk 插件里绑定的名称；没绑定 / lk 不可用时返回空串。"""
    try:
        from ATRI.system.lkbot.util import lk_util

        return str(lk_util.get_name(str(user_id)) or "").strip()
    except Exception:  # lk 没装、没初始化、用户没绑定，都当作没有名字
        return ""


def player_name(user_id: str | int, fallback: str = "") -> str:
    """玩家显示名：lk 绑定的名称 > fallback（角色名） > QQ 号。"""
    name = lk_name(user_id).strip()
    if name:
        return name
    return str(fallback or "").strip() or str(user_id)
