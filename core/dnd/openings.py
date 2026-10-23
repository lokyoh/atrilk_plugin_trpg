"""开场剧情：**从事件里读出来**。

**为什么不是另写一份清单**：一份手写的开场清单与世界里的事件是同一件事的
两份抄本——事件里已经有标题、入口场景、冲突、入口提示，
抄一遍就会漂移（改了事件忘了改清单，或者反过来）。

所以这里的「开场候选」就是**这个世界里现成的事件**：

- **预写事件**（预设世界自带的那些）；
- **模组第一章事件**（从模组转进来的那些——它们**就是入场的事件**，
  本就是预写事件的一部分）；
- **玩家自己生成的**：只有**开局那一刻织出来的那一件**——
  游玩途中织的事件是「后来发生的事」，不是「从哪儿切进去」。

**多章的故事只出入口那一章**：模组转进来的往往是**一条线上的好几件**
（失落矿坑五章、风骸岛四章）。把它们全当候选，等于让玩家从第四章切进去——
第三章的来路、第五章的前情都在别人身上。作者用 ``chain`` 把同一线的事件标住，
**同一条链里只出 ``event_no`` 最小的那一件**（见 :func:`_entry_keys`）；
没标 ``chain`` 的事件自成一条链，所以只出场一次的预写事件一件都不会少。

每件事件天然就是一个开场切入点：它有标题、入口场景、冲突与入口提示。
玩家挑一件，这一局就从它开始；DM 照着它的冲突与入口写开场白。

**清单不是白名单**：玩家可以另起一个故事（挑的时候不选任何一件，
或开局时跟 DM 说要自己来）。挑了一件也不锁死——
队伍的实际来路跟这条对不上时，DM 可以按世界的规则另起一个开场，
只要守住一条：**玩家刚来，开场得是他们躲不开的**
（撞见 / 被扣下 / 过不去 / 非要不可），不能是「听说某地不太平」。
"""

from __future__ import annotations

from dataclasses import dataclass

from .events import text_to_event
from .world_store import load_doc, load_index

__all__ = [
    "Opening",
    "entry_place",
    "opening_choices_text",
    "opening_options",
    "opening_text",
]


@dataclass(slots=True)
class Opening:
    """一条可选的开场剧情——实质是**一件现成的事件**。"""

    #: 序号（1 起，给玩家发指令用）
    index: int = 0
    #: 事件键（世界库里的 ``event`` 文档键）
    event_key: str = ""
    #: 标题（事件标题）
    title: str = ""
    #: 所在省（事件节点所在的地方）
    province: str = ""
    #: 入口场景
    where: str = ""
    #: 摘要（一句话说清这件事是什么）
    summary: str = ""
    #: 入口提示（给 DM 看：进门第一眼是什么）
    hint: str = ""
    #: 规模档（这条事件多大）
    scale: str = ""


def _province_of_event(world_key: str, ats: set[str]) -> str:
    """事件节点所在的那个省（取第一个认得出的）。"""
    if not ats:
        return ""
    try:
        from .world_store import province_index

        places = province_index(world_key)
    except Exception:
        return ""
    for at in sorted(ats):
        province = str(places.get(at) or "")
        if province:
            return province
    return ""


def _read_event(world_key: str, key: str) -> tuple[Opening, bool] | None:
    """把一件事件读成一条开场候选。读不出（图坏了）返回 ``None``。"""
    text = load_doc(world_key, "event", str(key)) or ""
    if not text.strip():
        return None
    slot = text_to_event(text)
    if slot is None or slot.graph.empty:
        return None
    # 入口节点：没有前置事实的那些
    entries = [n for n in slot.graph.nodes.values() if not n.requires]
    if not entries:
        return None
    entry = entries[0]
    ats = {str(n.at).strip() for n in slot.graph.nodes.values() if str(n.at).strip()}
    return (
        Opening(
            event_key=str(key),
            title=slot.title or str(key),
            province=_province_of_event(world_key, ats),
            where=str(entry.at or ""),
            summary=str(slot.graph.conflict or ""),
            hint=str(entry.hint or ""),
            scale=str(slot.scale or ""),
        ),
        True,
    )


def _entry_keys(events: dict[str, dict]) -> set[str]:
    """哪些事件能当**入场件**：每一条事件链只留它的头。

    模组转进来的是一串「章节」：矿坑那五件（伏击 → 红标帮 → 雷树 → 克拉摩堡 →
    回声洞）按序位排在同一条线上，风骸岛那四件同理。第二件起都有前情，
    拿它当开局等于让玩家从半截进来。作者用 ``chain`` 标同一条线，
    这里取每条线 ``event_no`` 最小的那件当入口。

    **没标 ``chain`` 的事件自成一条链**——只出场一次的那些（六个王国各一件）
    因此一件都不会被漏掉；将来加事件忘了标，最坏也只是它自己单独成链，
    还是会出现在候选里。宁可多出一件，也不要悄悄吞掉一件。
    """
    chains: dict[str, list[tuple[int, str]]] = {}
    entries: set[str] = set()
    for key, meta in (events or {}).items():
        meta = meta if isinstance(meta, dict) else {}
        name = str(meta.get("chain") or "").strip()
        key = str(key)
        if not name:
            entries.add(key)
            continue
        try:
            no = int(meta.get("event_no") or 1)
        except (TypeError, ValueError):
            no = 1
        chains.setdefault(name, []).append((no, key))
    for items in chains.values():
        entries.add(min(items)[1])
    return entries


def opening_options(world_key: str) -> list[Opening]:
    """这个世界里**可选的开场剧情**（给玩家挑）。

    来源是**这个世界里现成的事件**——预写事件与模组第一章事件都在其中；
    但**多章故事的后续章节不算**（见 :func:`_entry_keys`），
    游玩途中织出来的事件也不在（那是「后来发生的事」）。

    排序按「省首次出现的次序 + 序位」：同一省的候选要挨在一起，
    否则清单里会出现两个「【剑湾】」。
    """
    index = load_index(world_key)
    entries = (index.get("docs") or {}).get("event") or {}
    entries = {str(k): (v if isinstance(v, dict) else {}) for k, v in entries.items()}
    allowed = _entry_keys(entries)

    order_no: dict[str, int] = {}
    province_order: dict[str, int] = {}
    for key, meta in entries.items():
        try:
            order_no[key] = int(meta.get("event_no") or 0)
        except (TypeError, ValueError):
            order_no[key] = 0

    options: list[Opening] = []
    for key in entries:
        if key not in allowed:
            continue
        read = _read_event(world_key, key)
        if read is None:
            continue
        option, _ok = read
        province_order.setdefault(option.province, len(province_order))
        options.append(option)
    options.sort(
        key=lambda o: (province_order.get(o.province, 0), order_no[o.event_key])
    )
    for number, option in enumerate(options, start=1):
        option.index = number
    return options


def opening_choices_text(world_key: str) -> str:
    """给**玩家**看的开场剧情选项（开局时列出来让玩家挑）。

    只给标题、地点与摘要——入口提示是 DM 的事，不该提前剧透给玩家。
    """
    options = opening_options(world_key)
    if not options:
        return ""
    lines: list[str] = []
    last_province = ""
    for option in options:
        if option.province != last_province:
            if lines:
                lines.append("")
            lines.append(f"【{option.province or '别处'}】")
            last_province = option.province
        lines.append(f"{option.index}. {option.title}")
        if option.where:
            lines.append(f"   地点：{option.where}")
        if option.summary:
            lines.append(f"   {option.summary}")
    return "\n".join(lines)


def entry_place(world_key: str, event_key: str = "") -> str:
    """这一局的入场事件在**哪儿**：省名优先，其次入口场景名；读不出来返回空串。

    只为一件事存在——开局那一轮还没有 ``current_scene``（场景是 DM 在这一轮里
    才用 move_scene 进去的），而世界圣经要按「玩家在哪儿」裁。入场事件自带的
    入口地点，就是开局当下能拿到的最准的位置。
    """
    if not str(event_key or "").strip():
        return ""
    read = _read_event(world_key, str(event_key))
    if read is None:
        return ""
    option, _ok = read
    return option.province or option.where


def opening_text(world_key: str, event_key: str = "") -> str:
    """给 DM 看的开场剧情。

    :param event_key: 玩家挑中的那件事件。给了就**只给这一件**——
        玩家已经选过了，DM 照着开就行，不用再看一堆别的。

    **清单不是白名单**——即使玩家挑了一件，这句话也要跟着给 DM，
    否则它会以为只能照抄；队伍有别的来路时，DM 可以另起一个开场。
    """
    if event_key:
        read = _read_event(world_key, str(event_key))
        if read is not None:
            option, _ok = read
            return (
                "【玩家挑的起始故事】这一局从这件事开始。下面是它的冲突与入口，"
                "**照着开，但别照抄**——队伍的实际来路、现场的人与事，"
                "都要接得上。\n"
                "**这不是白名单**：如果队伍的情况跟这条对不上，"
                "你可以按世界的规则另起一个开场，只要守住一条："
                "**玩家刚来，开场得是他们躲不开的**"
                "（撞见 / 被扣下 / 过不去 / 非要不可），"
                "不能是「听说某地不太平」这种能转身走开的。\n\n"
                f"- 冲突：{option.summary}\n"
                f"- 入口：{option.where}\n"
                f"- 进门第一眼：{option.hint}"
            )
    options = opening_options(world_key)
    if not options:
        return ""
    parts: list[str] = []
    for option in options:
        parts.append(
            f"### {option.title}（{option.province or '别处'}）\n"
            f"- 冲突：{option.summary}\n"
            f"- 入口：{option.where}\n"
            f"- 进门第一眼：{option.hint}"
        )
    return (
        "【这个世界的开场剧情】下面是这个世界里现成的几件事，每件都能当开场的切入点。\n"
        "**这不是白名单**：照着开当然行，但队伍从哪儿来、什么来路，"
        "你可以按世界设定另起一个开场——只要守住一条："
        "**玩家刚来，开场得是他们躲不开的**（撞见 / 被扣下 / 过不去 / 非要不可），"
        "不能是「听说某地不太平」这种能转身走开的。\n\n" + "\n\n".join(parts)
    )
