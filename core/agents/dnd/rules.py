"""DND 5e 规则条目（供 DM 的 ``get_rule`` 功能按需查询）。

规则正文来自插件内置资源 ``res/rule.md``（随插件一起分发，不依赖仓库外部文件），
按 ``##`` 事件切分后缓存；:func:`match_rules` 用关键词 / 相似度挑出最相关的
若干条返回，未命中时回退到相似度最高的一条。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from ATRI.log import log

__all__ = [
    "PLUGIN_ROOT",
    "RULE_FILE",
    "load_rule_sections",
    "match_rules",
    "rule_topics",
]

#: 插件根目录（``core/agents/dnd/rules.py`` 上溯 3 层）
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
#: 规则文件（插件内置资源）
RULE_FILE = PLUGIN_ROOT / "res" / "rule.md"

_SECTION_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
#: 事件标题的序号前缀，如「一、」「3.」
_ORDER_RE = re.compile(r"^[一二三四五六七八九十百零\d]+[、.．]\s*")


@lru_cache(maxsize=1)
def load_rule_sections() -> dict[str, str]:
    """读取并切分规则文件，返回 ``事件标题 -> 正文``（标题已去掉序号）。"""
    try:
        text = RULE_FILE.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"读取规则文件失败：{RULE_FILE}（{e}）")
        return {}

    sections: dict[str, str] = {}
    title: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        match = _SECTION_RE.match(line)
        if match:
            if title is not None:
                sections[title] = "\n".join(buf).strip()
            title = _ORDER_RE.sub("", match.group("title")).strip()
            buf = []
        elif title is not None:
            buf.append(line)
    if title is not None:
        sections[title] = "\n".join(buf).strip()
    return sections


def rule_topics() -> str:
    """全部规则主题，供功能在未命中时提示。"""
    return "、".join(load_rule_sections())


def match_rules(topic: str, limit: int = 3) -> str:
    """按主题返回最相关的规则条目；未命中时回退到相似度最高的一条。"""
    sections = load_rule_sections()
    if not sections:
        return "暂时读不到规则内容。"

    topic = (topic or "").strip()
    if not topic:
        return "请提供规则主题关键词。\n可用主题：" + rule_topics()

    scored: list[tuple[float, str]] = []
    for title, body in sections.items():
        ratio = SequenceMatcher(None, topic, title).ratio()
        if topic in title or title in topic:
            ratio += 1.0
        elif topic in body:
            # 标题没提但正文讲到的（如「专注」在「法术规则」里）也算沾边；
            # 提得越多的事件排得越前
            ratio += 0.4 + min(0.5, body.count(topic) * 0.05)
        # 有条目直接以该词开头且带解释（「- **中毒**：……」）时，这节就是它的主场
        for line in body.splitlines():
            stripped = line.lstrip("-*#　 ").strip()
            if not stripped.startswith(topic):
                continue
            rest = stripped[len(topic) :].lstrip("*").lstrip()
            if rest[:1] in {"：", ":", "（", "(", ""}:
                ratio += 0.6
                break
        scored.append((ratio, title))
    scored.sort(key=lambda item: item[0], reverse=True)

    top = [item for item in scored[:limit] if item[0] >= 0.3] or scored[:1]
    return "\n\n".join(f"# {title}\n{sections[title]}" for _, title in top)
