"""通用文本排版工具。

聊天平台消息长度有限、且不支持复杂排版，这里统一提供几种简短文案格式，
供各规则体系复用，避免每个模块各写一套。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ["bullet", "kv_block", "rule_line", "truncate"]

DEFAULT_LIMIT = 800


def rule_line(width: int = 16, char: str = "─") -> str:
    """一条分隔线。"""
    return char * max(1, width)


def bullet(items: Iterable[str], mark: str = "·") -> str:
    """把若干条目排成竖排列表。"""
    return "\n".join(f"{mark} {item}" for item in items if str(item).strip())


def kv_block(data: Mapping[str, object], sep: str = "：") -> str:
    """把键值对排成竖排文本。"""
    return "\n".join(f"{key}{sep}{value}" for key, value in data.items())


def truncate(text: str, limit: int = DEFAULT_LIMIT, ellipsis: str = "……（已截断）") -> str:
    """按字符数截断过长文本。"""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + ellipsis
