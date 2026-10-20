"""剧情记忆库（跨场景、跨轮次）。

现有系统里 DM 能看到的「已发生」只有事件流水账（``events.jsonl``），而它有两个
硬伤：

1. **换场景即失忆**：``events_text(..., scene=save.current_scene)`` 按当前场景
   过滤，上个场景发生的事一个字都看不到；
2. **只有流水、没有重点**：流水账记的是「谁说了什么」，而不是「什么已经发生、
   什么还悬着」，DM 每次要从原始叙述里自己提炼。

本模块提供一层**结构化摘要**：每条 ≤ 40 字，分「事实 / 伏笔 / 关系」三类，带
发生场景、轮次与是否已回收。它有两个使用面：

- **写入**：系统从已有工具（``update_scene`` / ``update_npc`` / 战斗结算）顺带
  记一条，不额外花模型调用；DM 也可以用 ``remember`` 工具手动补（可选，
  不要求每轮都写）；
- **读取**：每轮只把摘要拼成一小段注入提示词，**替换掉**原来那几百字符的
  事件流水账——总量不增反降，跨场景覆盖反而更全。

**为什么放在 ``save.progress`` 里**：``Save.to_dict`` / ``from_dict`` 原样透传
``progress``，不需要改存档格式；键名以 ``_`` 开头，正好被 ``_progress_text``
的「内部键不给 DM 看」规则排除，不会误进场景构建器的提示词。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ATRI.log import log

from .save_store import Save

__all__ = [
    "MEMORY_KEY",
    "MEMORY_MAX",
    "MEMORY_TEXT_MAX",
    "PlotMemory",
    "add_memory",
    "current_round",
    "list_memories",
    "memory_text",
    "recall_memories",
    "remember_from_tool",
    "set_resolved",
]

#: 记忆在 ``save.progress`` 里的键名。下划线开头 = 内部键，不进场景构建器。
MEMORY_KEY = "_plot_memory"

#: 记忆条数硬上限：超了按「已回收优先、时间旧优先」淘汰。
#: 这条是 token 纪律——每轮都要注入，条数失控等于成本失控。
MEMORY_MAX = 12

#: 单条记忆的字数上限。
MEMORY_TEXT_MAX = 40

#: 记忆种类
_KINDS = ("fact", "foreshadow", "relation")

_KIND_LABELS = {
    "fact": "已发生",
    "foreshadow": "待回收",
    "relation": "关系",
}

#: 中文标点与空白，用于截断时补省略号
_CUT_RE = re.compile(r"[\s，。、；：,.;:]+")


@dataclass
class PlotMemory:
    """一条剧情记忆摘要。"""

    text: str
    kind: str = "fact"
    scene: str = ""
    round: int = 0
    resolved: bool = False
    source: str = ""  #: 来源标记：auto / dm
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return _KIND_LABELS.get(self.kind, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "scene": self.scene,
            "round": self.round,
            "resolved": self.resolved,
            "source": self.source,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlotMemory:
        return cls(
            text=str(data.get("text", "") or ""),
            kind=str(data.get("kind", "fact") or "fact"),
            scene=str(data.get("scene", "") or ""),
            round=int(data.get("round", 0) or 0),
            resolved=bool(data.get("resolved", False)),
            source=str(data.get("source", "") or ""),
            extra=dict(data.get("extra") or {}),
        )

    def line(self) -> str:
        """单行展示（注入提示词与 recall 返回都用它）。"""
        mark = "✓" if self.resolved else ""
        scene = f"（{self.scene}）" if self.scene else ""
        return f"- {self.text}{scene}{mark}"


def _clean(text: Any, limit: int = MEMORY_TEXT_MAX) -> str:
    """压成一行、限长。"""
    body = _CUT_RE.sub(" ", str(text or "")).strip()
    if len(body) > limit:
        body = body[: limit - 1].rstrip() + "…"
    return body


def list_memories(save: Save) -> list[PlotMemory]:
    """读出全部记忆（按写入顺序）。"""
    raw = (save.progress or {}).get(MEMORY_KEY)
    if not isinstance(raw, list):
        return []
    out: list[PlotMemory] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(PlotMemory.from_dict(item))
    return out


def _store(save: Save, memories: list[PlotMemory]) -> None:
    """写回记忆（顺带落盘）。"""
    save.progress[MEMORY_KEY] = [m.to_dict() for m in memories]
    try:
        from .save_store import save_save

        save_save(save)
    except Exception as e:  # 存档写失败不该影响主持
        log.warning(f"写剧情记忆失败：{e}")


def _prune(memories: list[PlotMemory]) -> list[PlotMemory]:
    """超上限时淘汰：先删已回收的，再删最旧的。"""
    if len(memories) <= MEMORY_MAX:
        return memories
    keep = list(memories)
    # 已回收的最先走
    while len(keep) > MEMORY_MAX:
        for index, item in enumerate(keep):
            if item.resolved:
                keep.pop(index)
                break
        else:
            break
    # 还超就砍最旧的
    while len(keep) > MEMORY_MAX:
        keep.pop(0)
    return keep


def add_memory(
    save: Save,
    text: Any,
    *,
    kind: str = "fact",
    scene: str = "",
    round: int = 0,
    resolved: bool = False,
    source: str = "auto",
    extra: dict[str, Any] | None = None,
    dedup: bool = True,
) -> PlotMemory | None:
    """记一条剧情记忆。

    :param kind: ``fact`` / ``foreshadow`` / ``relation``
    :param dedup: 文本重复时不重复记（自动摘要是程序生成的，很容易重复）
    :return: 记下的那条；内容为空时返回 ``None``
    """
    body = _clean(text)
    if not body:
        return None
    if kind not in _KINDS:
        kind = "fact"
    memories = list_memories(save)
    if dedup:
        for item in memories:
            if item.text == body:
                return item
    memory = PlotMemory(
        text=body,
        kind=kind,
        scene=str(scene or save.current_scene or ""),
        round=int(round or 0),
        resolved=bool(resolved),
        source=source,
        extra=dict(extra or {}),
    )
    memories.append(memory)
    _store(save, _prune(memories))
    return memory


def current_round(save: Save) -> int:
    """本局当前轮次（供记忆标记用；取不到时按 0 算）。

    轮次号存在 ``save.progress["_story_round"]``（与复盘共用），这里是**已完成的
    轮数**，也就是正在进行的是下一轮。取不到就返回 0，不影响记忆本身。
    """
    try:
        return int((save.progress or {}).get("_story_round", 0) or 0)
    except (TypeError, ValueError):
        return 0


def set_resolved(save: Save, text: Any, *, resolved: bool = True) -> PlotMemory | None:
    """把一条已存在的记忆标记为「已回收」（伏笔兑现时用）。

    :param text: 记忆原文（要与记下时一致，允许差一个尾部省略号）
    :return: 命中的那条；没找到返回 ``None``
    """
    body = _clean(text)
    if not body:
        return None
    memories = list_memories(save)
    for item in memories:
        if item.text == body or item.text.rstrip("…") == body.rstrip("…"):
            item.resolved = bool(resolved)
            _store(save, memories)
            return item
    return None


def remember_from_tool(
    save: Save, tool_name: str, args: dict[str, Any], *, round: int = 0
) -> None:
    """从已有工具的调用参数里顺带摘一条记忆（**零额外模型成本**）。

    只摘「本身就有结构」的参数，不去解析自然语言：

    - ``update_scene`` → 场景里发生了什么变化（fact）
    - ``update_npc`` → 某个 NPC 的状态 / 关系变化（relation）
    - ``update_character`` → 玩家角色的关键状态（fact），只在写了备注类字段时

    :param tool_name: 工具名
    :param args: 工具参数
    """
    args = dict(args or {})
    if tool_name == "update_scene":
        note = args.get("note")
        if note:
            add_memory(
                save,
                f"场景变化：{note}",
                kind="fact",
                round=round,
                source="auto",
            )
        return
    if tool_name == "update_npc":
        name = str(args.get("name") or "").strip()
        if not name:
            return
        bits: list[str] = []
        status = str(args.get("status") or "").strip()
        note = str(args.get("note") or "").strip()
        if status:
            bits.append(status)
        if note:
            bits.append(note)
        if not bits:
            return
        add_memory(
            save,
            f"{name}：{'，'.join(bits)}",
            kind="relation",
            round=round,
            source="auto",
        )
        return
    if tool_name == "update_character":
        # 只记「备注」这类 DM 手写的内容；数值变化靠角色卡，不必进记忆。
        # 该工具的参数形态是 field / value（不是 notes），所以要按字段挑。
        field = str(args.get("field") or "").strip().lower()
        if field != "notes":
            return
        value = str(args.get("value") or "").strip()
        if not value:
            return
        who = str(args.get("player") or "").strip()
        add_memory(
            save,
            f"{who}：{value}" if who else f"角色备注：{value}",
            kind="fact",
            round=round,
            source="auto",
        )


def memory_text(save: Save, *, max_chars: int = 300) -> str:
    """拼成注入提示词的摘要块。

    分三组：**已发生**（fact）、**待回收**（foreshadow 未回收）、**关系**
    （relation）。空组不出现。

    超长时的取舍：**先丢「已发生」里最旧的事实**（那部分大多能从场景 / NPC
    名单 / 上一轮叙述推出来），再从尾部裁；「待回收」与「关系」优先保住——
    它们是别处推导不出来的东西（没兑现的伏笔、人情旧账），丢了就真忘了。

    :param max_chars: 整块字数上限（每轮都要重发，这段要短）
    :return: 提示词文本；没有记忆时返回空串
    """
    memories = list_memories(save)
    if not memories:
        return ""
    facts = [m for m in memories if m.kind == "fact"]
    foreshadows = [m for m in memories if m.kind == "foreshadow" and not m.resolved]
    relations = [m for m in memories if m.kind == "relation"]

    def render(fact_items: list[PlotMemory]) -> str:
        lines: list[str] = []
        for title, items in (
            (_KIND_LABELS["fact"], fact_items),
            (_KIND_LABELS["foreshadow"], foreshadows),
            (_KIND_LABELS["relation"], relations),
        ):
            if not items:
                continue
            if lines:
                lines.append("")
            lines.append(f"【{title}】")
            for item in items:
                lines.append(item.line())
        return "\n".join(lines).strip()

    text = render(facts)
    # 装不下就先丢最旧的事实
    while len(text) > max_chars and len(facts) > 1:
        facts = facts[1:]
        text = render(facts)
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def recall_memories(
    save: Save, topic: str = "", *, limit: int = 5, include_resolved: bool = True
) -> list[PlotMemory]:
    """按关键词检索记忆（DM 按需调用，平时零开销）。

    匹配方式很朴素：关键词做子串匹配，命中数多的排前面；都没命中就按最近
    的返回。宁可给一点无关的，也别让 DM 查不到东西。

    :param topic: 关键词，空字符串表示「只看最近的」
    :param limit: 最多返回几条
    :return: 命中的记忆
    """
    memories = list_memories(save)
    if not memories:
        return []
    if not include_resolved:
        memories = [m for m in memories if not m.resolved]
    key = str(topic or "").strip()
    if not key:
        return list(reversed(memories))[:limit]
    hits: list[tuple[int, int, PlotMemory]] = []
    for index, item in enumerate(memories):
        score = 0
        for ch in set(key):
            if ch in item.text:
                score += 1
        if score:
            # 越新分越高
            hits.append((score, index, item))
    if not hits:
        return list(reversed(memories))[:limit]
    hits.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in hits[:limit]]
