"""通用文本排版工具。

聊天平台消息长度有限、且不支持复杂排版，这里统一提供几种简短文案格式，
供各规则体系复用，避免每个模块各写一套。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

__all__ = [
    "bullet",
    "kv_block",
    "rule_line",
    "strip_player_hints",
    "strip_safety_blocks",
    "truncate",
]

DEFAULT_LIMIT = 800

#: 叙述结尾给玩家的「接下来你们可以…」提示块（只给玩家看，不该进历史 / 复盘）。
#: 不锚定行首：模型偶尔不守「单独一行」的规矩、把提示写在句末；也容忍「接着」这类
#: 换措辞与单数「你」。冒号或行尾必须跟上——否则「接下来你可以活着离开」这种正文里
#: 的相似句子会被误伤（「下面」是常见空间词，容易撞正文，不做变体）。
_PLAYER_HINTS_RE = re.compile(
    r"[ \t>*_#\-]*(?:接下来|接着)你(?:们)?可以(?:[：:]|$)", re.MULTILINE
)

#: 模型侧偶尔会把安全审查的元信息（``<ds_safety>…</ds_safety>``）混进应答正文：
#: 原样透出会给玩家看到一堆无关标签，事件历史与复盘也会被污染。
_SAFETY_BLOCK_RE = re.compile(r"<ds_([a-z_]+)>.*?</ds_\1>", re.DOTALL)
_SAFETY_TAG_RE = re.compile(r"</?ds_[a-z_]+>")


def strip_safety_blocks(text: str) -> str:
    """剥掉模型应答里混入的审查元信息块（如 ``<ds_safety>…</ds_safety>``）。

    这些块不是给玩家看的内容：留着会原样泄漏成 DM 叙述，并污染事件历史与复盘。
    成对的整块剥掉，没配对的残留标签也清掉；剥完顺手把连续空行压回正常段距。
    """
    cleaned = _SAFETY_BLOCK_RE.sub("", str(text or ""))
    cleaned = _SAFETY_TAG_RE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def strip_player_hints(text: str) -> str:
    """去掉叙述结尾给玩家的行动提示，留下纯粹的剧情正文。"""
    raw = str(text or "")
    match = _PLAYER_HINTS_RE.search(raw)
    if match is None:
        return raw
    return raw[: match.start()].rstrip()


def rule_line(width: int = 16, char: str = "─") -> str:
    """一条分隔线。"""
    return char * max(1, width)


def bullet(items: Iterable[str], mark: str = "·") -> str:
    """把若干条目排成竖排列表。"""
    return "\n".join(f"{mark} {item}" for item in items if str(item).strip())


def kv_block(data: Mapping[str, object], sep: str = "：") -> str:
    """把键值对排成竖排文本。"""
    return "\n".join(f"{key}{sep}{value}" for key, value in data.items())


def truncate(
    text: str, limit: int = DEFAULT_LIMIT, ellipsis: str = "……（已截断）"
) -> str:
    """按字符数截断过长文本。"""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + ellipsis
