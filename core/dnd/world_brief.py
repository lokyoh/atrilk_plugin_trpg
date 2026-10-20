"""玩家向的世界背景手册（``world_brief.md``）。

DM 提示词里已经有世界圣经、故事文档、场景副本，但那些是**给 DM 看**的：夹着内部键、
提示词口吻、还有不许剧透的部分。玩家需要的是一份**能自己看、能存着翻**的背景手册：
这地方叫什么、现在谁说了算、有哪些势力与传说、我是谁。

本模块在**开局后**把散落在世界库与存档里的设定汇成一份 Markdown，落在存档目录里
（``saves/<save_id>/world_brief.md``），由 :mod:`..agents.dnd.game` 交给 handler
发到群文件（见 ``ATRI.message.file_msg``）。

内容来源（全部**程序提取**，一次模型调用都不额外花）：

- **世界档案**：世界名称、一句话简介、各地区（形状 / 地标 / 国都 / 人文 / 政治）；
- **本局故事**：故事名称、简介、世界起点与推力（时间背景 / 整体格局 / 近期大事 /
  未解悬念 / 玩家切入点）；
- **世界圣经**：已登记的实体（人物 / 势力 / 地名 / 物品）与不易变的事实；
- **本局队伍**：角色名与等级——玩家拿到文件就能对上「我是谁」。

设计取向：

- **宁缺毋滥**：缺哪块就少一节，绝不为了凑齐去调模型；
- **不剧透**：故事只取「世界起点」这类开篇设定，不取后续章节与场景里的隐藏信息；
- **幂等**：重写同一份文件即可，不按版本累积。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ATRI.log import log

from .save_store import Save, get_character, save_dir
from .world_bible import KIND_LABELS, load_bible
from .world_store import (
    MAIN_DOC_KEY,
    get_world,
    load_doc,
)

__all__ = [
    "BRIEF_FILENAME",
    "brief_path",
    "build_world_brief",
    "save_world_brief",
    "world_brief_text",
]

#: 存档内的世界背景手册文件名
BRIEF_FILENAME = "world_brief.md"


def brief_path(save_id: str) -> Path:
    """某存档的世界背景手册路径（``saves/<save_id>/world_brief.md``）。"""
    return save_dir(save_id) / BRIEF_FILENAME


# --------------------------------------------------------------------------- #
# Markdown 提取小工具
# --------------------------------------------------------------------------- #

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s*(?P<title>.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、])\s+(?P<text>.+?)\s*$")


def _sections(text: str) -> list[tuple[int, str, str]]:
    """把 Markdown 拆成 ``(标题层级, 标题, 正文)``；顶级标题（``#``）也要。

    正文一直取到下一个同级或更高级标题为止，这样「## 地区一：xxx」这种
    带编号的标题也能被前缀匹配到。
    """
    lines = str(text or "").splitlines()
    heads: list[tuple[int, int, str]] = []  # (行号, 层级, 标题)
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match:
            heads.append((index, len(match.group("hashes")), match.group("title")))
    out: list[tuple[int, str, str]] = []
    for order, (index, level, title) in enumerate(heads):
        end = len(lines)
        for next_index, next_level, _ in heads[order + 1 :]:
            if next_level <= level:
                end = next_index
                break
        out.append((level, title, "\n".join(lines[index + 1 : end]).strip()))
    return out


def _find(sections: list[tuple[int, str, str]], *keywords: str) -> str:
    """按关键词找一个标题的正文（任一关键词命中前缀即可）；没有返回空串。"""
    for _level, title, body in sections:
        clean = title.strip().strip("*_")
        if any(keyword in clean for keyword in keywords):
            return body
    return ""


def _label_value(text: str, *labels: str) -> str:
    """从 ``- 世界名称：灰烬海`` 这类行里取值。"""
    for line in str(text or "").splitlines():
        body = _BULLET_RE.match(line)
        if not body:
            continue
        item = body.group("text")
        for label in labels:
            head, sep, tail = item.partition("：")
            if sep and label in head:
                return tail.strip().strip("*_")
    return ""


def _bullets(text: str) -> list[str]:
    """取一个小节里的列表项（``-`` / ``*`` / ``1.`` 都认），去掉强调符。"""
    return [
        _BULLET_RE.match(line).group("text").strip().strip("*_")
        for line in str(text or "").splitlines()
        if _BULLET_RE.match(line)
    ]


def _labeled_lines(text: str, *, skip: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """把 ``- 标题：正文`` 的小节拆成 ``[(标题, 正文)]``；``skip`` 里的标题丢掉。

    世界档案的每个地区都是这个形状（「### 标志地标」…），这里统一抽出来，
    只留下玩家用得上的几项。
    """
    sections = _sections(text)
    return [
        (title.strip().strip("*_"), body.strip())
        for level, title, body in sections
        if level >= 3 and body and title.strip().strip("*_") not in skip
    ]


#: 地区小节里值得给玩家看的（其余如「与周边区域的分隔方式」是给 DM 生成用的）
_REGION_FIELDS = ("大致形状", "主要地形", "标志地标", "国都", "人文特点", "政治局势")


def _first_paragraph(text: str, limit: int = 300) -> str:
    """取正文第一段（跳过标题、列表与引用），压成一行并截断。"""
    for line in str(text or "").splitlines():
        body = line.strip()
        if not body or body.startswith(("#", ">", "-", "*", "|")):
            continue
        return body[: limit - 1] + "…" if len(body) > limit else body
    return ""


# --------------------------------------------------------------------------- #
# 各分节
# --------------------------------------------------------------------------- #


def _evolution_section(save: Save) -> list[str]:
    """世界演化：势力位置与地点痕迹（玩家侧形态）。

    这一节进手册的理由：世界演化轨的意义就是「世界在自己往前走」，
    玩家得**看得见**这件事——回到走过的城镇发现它烧了半条街，
    那是这一轴唯一的产出；要是只存在存档里没人知道，就白做了。

    玩家侧**不给看**势力档位与期限的到期后果（那是 DM 的账），
    所以用 ``for_player=True``。
    """
    from .evolution import evolution_text, load_evolution

    text = evolution_text(load_evolution(save), for_player=True)
    if not text.strip():
        return []
    return ["## 近况", "", text]


def _world_section(save: Save) -> list[str]:
    """世界档案：名称、简介、地区。"""
    world = get_world(save.world_key)
    content = load_doc(save.world_key, "world", MAIN_DOC_KEY) or ""
    if world is None and not content:
        return []

    lines = ["## 世界"]
    title = world.title if world is not None else _label_value(content, "世界名称")
    intro = world.intro if world is not None else _label_value(content, "一句话简介")
    if title:
        lines.append(f"**{title}**")
    if intro:
        lines.append(intro)
    if len(lines) == 1:
        return []

    # 地区：从世界地图文档的「## 地区X：名称」小节里取
    regions = [
        (heading, body)
        for level, heading, body in _sections(content)
        if level == 2 and heading.strip().startswith("地区")
    ]
    if regions:
        lines.append("")
        lines.append("### 主要地区")
        for heading, body in regions:
            name = heading.strip().split("：")[-1].strip()
            lines.append("")
            lines.append(f"**{name or heading.strip()}**")
            for label, value in _labeled_lines(body, skip=("特殊地区",)):
                # 只保留玩家关心的字段
                if not any(field in label for field in _REGION_FIELDS):
                    continue
                lines.append(f"- **{label}**：{_first_paragraph(value, 160)}")
    return lines


def _chapter_section(save: Save) -> list[str]:
    """冒险目标：当前章节的目标、完成条件、已知线索与已完成章节。

    章节卡取代了原来的故事推力，所以手册这一节从「故事背景」变成了
    「现在该干什么」——玩家最需要知道的是**下一步问什么**。

    难度数值、威胁层级、fail_forward 这些**不进手册**：
    手册是发给玩家的，不该剧透DM 侧的难度设计。
    """
    from .chapter import chapter_summary

    text = chapter_summary(save, for_player=True)
    if not text.strip():
        return []
    return ["## 冒险目标", "", text]


def _bible_section(save: Save) -> list[str]:
    """世界圣经：实体登记与不易变的事实（**只取可公开的部分**）。

    故事文档的「关键节点」这类剧情秘密登记为 ``public=False``（见
    :class:`~.world_bible.Entity`），这里直接跳过——手册是发给玩家的，
    不能把藏宝点、伏击处这类东西剧透出去。
    """
    bible = load_bible(save.world_key)
    public = [entity for entity in bible.entities if entity.public]
    if not public and not bible.facts:
        return []

    lines = ["## 人物与势力"]
    if bible.world_name:
        lines.append("（下列名字均为定稿，DM 不会另起新名）")
    if bible.intro:
        lines.append(bible.intro)
    if bible.facts:
        lines.append("")
        lines.extend(f"- {fact}" for fact in bible.facts)

    by_kind: dict[str, list[Any]] = {}
    for entity in bible.sorted_entities():
        if not entity.public:
            continue
        by_kind.setdefault(entity.kind, []).append(entity)
    for kind, entities in by_kind.items():
        lines.append("")
        lines.append(f"### {KIND_LABELS.get(kind, kind)}")
        for entity in entities:
            row = f"- **{entity.name}**"
            if entity.region:
                row += f"（{entity.region}）"
            if entity.note:
                row += f"：{entity.note}"
            if entity.aliases:
                row += f"　〔又称 {'、'.join(entity.aliases)}〕"
            lines.append(row)
    return lines


def _party_section(save: Save) -> list[str]:
    """本局队伍：角色名与等级。"""
    rows: list[str] = []
    for user_id in save.player_ids:
        card = get_character(save, user_id)
        if card is None:
            continue
        rows.append(f"- **{card.name}**（{card.level} 级）")
    if not rows:
        return []
    return ["## 本局队伍", *rows]


#: 各分节的构建器（按展示顺序）
#: 手册各节的拼装顺序。章节节取代了原来的故事节——
#: 章节卡既然接手了「玩家该干什么」，手册就该讲这个，而不是讲背景故事。
_BUILDERS = (
    _world_section,
    _chapter_section,
    _evolution_section,
    _bible_section,
    _party_section,
)


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #


def world_brief_text(save: Save) -> str:
    """把本局的世界背景整理成一份 Markdown 文本。

    各块都没有内容时返回空串（调用方据此跳过写文件）。单块出错只丢那一块，
    不让整份手册空掉。
    """
    blocks: list[list[str]] = []
    for builder in _BUILDERS:
        try:
            block = builder(save)
        except Exception as e:  # 单块失败不该让整份文档没有
            log.warning(f"整理世界背景「{builder.__name__}」失败：{e}")
            continue
        if block:
            blocks.append(block)
    if not blocks:
        return ""

    lines = [
        "# 世界背景手册",
        "",
        "> 由本局世界设定自动整理，供玩家随时查阅；"
        "后续探索出新的人物与地点时 DM 会同步更新。",
    ]
    for block in blocks:
        lines.append("")
        lines.extend(block)
    return "\n".join(lines).rstrip() + "\n"


def save_world_brief(save: Save, text: str = "") -> Path | None:
    """生成并写入存档目录里的世界背景手册。

    :param text: 已经生成好的正文；留空则现场生成一次。
    :return: 写入的路径；没有可写内容或写失败时返回 ``None``。
    """
    body = text or world_brief_text(save)
    if not body:
        return None
    path = brief_path(save.save_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as e:
        log.warning(f"写世界背景手册失败：{path}（{e}）")
        return None
    return path


def build_world_brief(save: Save) -> tuple[Path | None, str]:
    """整理 + 落盘。

    :return: ``(路径, 正文)``；没有可整理内容时路径为 ``None``、正文是给玩家的一句话说明。
    """
    text = world_brief_text(save)
    if not text:
        return None, "这个世界还没有可整理的背景设定。"
    path = save_world_brief(save, text)
    if path is None:
        return None, "世界背景手册写不进存档目录，请稍后再试。"
    return path, text
