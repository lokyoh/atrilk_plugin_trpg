"""跑团 Agent：DM 单轮主持流程。

对应 ``.agents/new_plusin_lktrpg.md`` 的「DM Agent 工作流」，并按「先凑齐所有玩家的行动、
再统一主持」收敛成一轮：

1. 描述（DM 叙述）
2. 等待玩家 @ 回复；每一轮等谁提交由 DM 用 ``await_players`` 决定（默认全员）
3. 每接到一条行动就用 :data:`~...dnd.judge.dnd_judge` 把关：不通过打回，通过则
   记进本轮（DM 运行前可再次 @ 覆盖自己的行动）
4. **名单上的玩家都提交后**，才由 :data:`~.dm.dm_agent` 结合当前场景与事件统一主持，
   完成后清空本轮并记下 DM 决定的下一轮名单，转回 1

**战斗**（:mod:`~...dnd.combat`）时改由先攻顺序驱动：轮到的玩家提交后立刻主持，
该回合结束后系统推进到下一名玩家，中间的敌人回合交给 DM 用 ``enemy_attack`` 结算。

本模块负责第 3、4 步的编排：:func:`submit_action` 接住单个玩家的行动，
:func:`run_dm_round` 把整轮行动一起交给 DM，并把玩家行动与 DM 叙述写进存档事件日志。
另外还有：场景切换 :func:`open_scene`、场景构建 :func:`build_scene_for_save`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ... import settings
from ...dnd.battlemap import map_digest
from ...dnd.billing import bind_save, record_result
from ...dnd.chapter import chapter_summary, current_card
from ...dnd.combat import (
    advance_turn,
    all_enemies_out,
    clear_combat,
    combat_text,
    combatant_conditions,
    current_actor,
    effective_ac,
    is_incapacitated,
    is_out,
    living_enemies,
    load_combat,
    peek_following,
    save_combat,
    use_resource,
)
from ...dnd.epoch import ENDINGS
from ...dnd.judge import dnd_judge
from ...dnd.memory import memory_text
from ...dnd.names import player_name
from ...dnd.npcs import list_npcs, npcs_text
from ...dnd.save_store import (
    append_story,
    append_story_ending,
    clear_binding,
    clear_round,
    copy_scene,
    events_text,
    find_character,
    get_character,
    has_scene,
    load_round,
    load_save,
    log_event,
    next_story_round,
    read_scene,
    save_round,
    save_save,
    set_character,
)
from ...dnd.settlement import settle_combat
from ...dnd.world_bible import bible_text
from ...dnd.world_brief import build_world_brief
from ...dnd.world_store import read_doc
from ...trpg.rule import Verdict
from ...trpg.text import strip_player_hints
from ..base import AgentError
from ..thinking import AgentContext
from .build_scene import scene_builder_agent
from .dm import dm_agent
from .on_demand import ensure_scene

__all__ = [
    "TurnResult",
    "build_scene_for_save",
    "is_started",
    "open_scene",
    "resolve_players",
    "run_dm_round",
    "run_opening",
    "scene_brief",
    "scene_text",
    "submit_action",
]


@dataclass(slots=True)
class TurnResult:
    """一轮主持的结果。"""

    #: 规则判断结论
    verdict: Verdict
    #: 直接回给玩家的文案（打回 / 追问 / 等待他人 / 报错时用）
    message: str = ""
    #: DM 的主持叙述
    narration: str = ""
    #: 判定是否已结算（交给 DM 之前就已掷骰）
    resolved: bool = False
    #: 本轮还没提交行动的玩家（非空表示还在等他们，DM 尚未运行）
    waiting_for: list[str] = field(default_factory=list)
    #: 本轮之后本局已经结档（DM 用 wrap_up 收尾）
    ended: bool = False
    #: 要展示给玩家的判定明细（骰点 + 成败）；暗骰不会出现在这里
    rulings: list[str] = field(default_factory=list)
    #: 开场轮整理出的世界背景手册：落盘路径与正文（没有则为 ``None`` / 空串）
    world_brief_path: Any = None
    world_brief_text: str = ""

    @property
    def text(self) -> str:
        """实际要发给玩家的正文（主持叙述 + 判定明细）。"""
        body = self.narration or self.message
        if self.narration and self.rulings:
            body += "\n\n🎲 判定\n" + "\n".join(f"· {line}" for line in self.rulings)
        return body

    @property
    def rulings_text(self) -> str:
        """只有判定明细的那一段（给需要自己拼文案的调用方用）。"""
        if not self.rulings:
            return ""
        return "🎲 判定\n" + "\n".join(f"· {line}" for line in self.rulings)

    @property
    def passed(self) -> bool:
        """是否通过规则判断（``False`` 表示被打回 / 追问）。"""
        return self.verdict is Verdict.ACCEPT

    @property
    def waiting(self) -> bool:
        """本轮是否还在等其他玩家提交行动。"""
        return bool(self.waiting_for)


def scene_text(save) -> str:
    """取存档当前场景的正文：优先读存档里的副本，没副本再退回世界库。"""
    key = (save.current_scene or "").strip()
    if not key:
        return ""
    body = read_scene(save, key)
    if body is not None:
        return body
    doc = read_doc(save.world_key, "scene", key)
    return doc.content if doc is not None else ""


def _field(body: str, *names: str) -> str:
    """从场景文档里取某个字段的值（``- 一句话定位：xxxx``）。"""
    for name in names:
        match = re.search(
            rf"^[-*·]\s*{re.escape(name)}\s*[：:]\s*(.+)$", body, re.MULTILINE
        )
        if match:
            return match.group(1).strip()
    return ""


def _recent_changes(body: str, limit: int = 3) -> list[str]:
    """取场景副本「变化记录」小节里最近几条（这些才是会累积、需要记住的）。"""
    index = body.find("## 变化记录")
    if index < 0:
        return []
    lines: list[str] = []
    for line in body[index:].splitlines()[1:]:
        text = line.strip()
        # 变化记录是最后一节；万一后面又接了别的标题，到此为止
        if text.startswith("#"):
            break
        if text.startswith("- "):
            lines.append(text)
    return lines[-limit:]


def scene_brief(save) -> str:
    """当前场景的**简介**（进提示词用），细节由 DM 自己用 ``get_scene`` 查。

    完整场景文档（布局 / 可交互元素 / 在场者 / 隐藏信息）每轮都塞进提示词太贵也
    太吵，这里只留：场景名、一句话定位、氛围、最近的变化记录。
    """
    key = (save.current_scene or "").strip()
    if not key:
        return ""
    body = scene_text(save)
    if not body:
        return f"场景：{key}"

    name = _field(body, "场景名称") or key
    kind = _field(body, "地点类型")
    headline = f"场景：{name}" + (f"（{kind}）" if kind else "")
    lines = [headline]
    for label, names in (
        ("定位", ("一句话定位", "场景功能")),
        ("氛围", ("整体氛围", "直觉")),
    ):
        value = _field(body, *names)
        if value:
            lines.append(f"{label}：{value}")
    changes = _recent_changes(body)
    if changes:
        lines.append("最近的变化：")
        lines.extend(f"· {item.lstrip('- ').strip()}" for item in changes)
    if len(lines) == 1:
        # 文档结构不完整时退回开头几行，别让提示词里出现空白场景
        preview = " ".join(
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        lines.append(preview[:200] + ("…" if len(preview) > 200 else ""))
    return "\n".join(lines)


def _party_text(save) -> str:
    """本局角色的摘要，供构建器把场景里的 NPC / 线索挂到具体角色上。"""
    lines: list[str] = []
    for user_id in save.player_ids:
        card = get_character(save, user_id)
        if card is not None:
            lines.append(card.summary())
    return "\n\n".join(lines)


def _card_brief(card) -> str:
    """角色卡的一行摘要：多人局里非焦点玩家用它，省掉整块属性/技能/法术。"""
    race = card.race_obj
    profession = card.profession_obj
    weapons = "、".join(weapon.cn for weapon in card.primary_weapons()[:2])
    line = (
        f"【{card.name}】{race.cn if race else '未知种族'} "
        f"{profession.cn if profession else '无业'} Lv.{card.level}"
        f"　HP {card.hp}/{card.max_hp}　AC {card.armor_class}"
    )
    if weapons:
        line += f"　武器 {weapons}"
    if card.is_caster:
        line += f"　法术位 {card.spell_slots_text()}"
    line += f"　XP {card.exp}"
    status = list(card.conditions)
    if card.exhaustion:
        status.append(f"力竭 {card.exhaustion} 级")
    if status:
        line += "　状态 " + "、".join(status)
    return line


def _party_block(save, order, focus: str = "") -> str:
    """本轮参与者的角色卡：焦点玩家给完整摘要，其他人只给一行。

    逐人结算时焦点就是当前行动的玩家；多人的整轮总结里大家都是「一行」，
    够 DM 判断血量 / 武器 / 状态，具体数值要查再走 query_data / update_character。
    """
    blocks: list[str] = []
    for user_id in order:
        card = get_character(save, user_id)
        if card is None:
            continue
        if focus and str(user_id) == str(focus):
            blocks.append(card.summary())
        else:
            blocks.append(_card_brief(card))
    return "\n\n".join(blocks)


#: 注入 DM 提示词的「最近原始事件」条数上限。
#: 剧情记忆摘要已经覆盖了「已发生什么」，这里只留一行即时细节兜底。
_RECENT_EVENTS_MAX = 1

#: 单条原始事件的最大字符数。
_RECENT_EVENT_LEN = 80

#: 剧情记忆摘要注入提示词的字数上限（整轮）。
_MEMORY_TEXT_MAX = 160

#: 逐人结算时摘要的字数上限（这一轮只结算一个人，不用给全景）。
_MEMORY_TEXT_MAX_PARTIAL = 100


def _history_block(save, limit: int, *, summary_max: int = _MEMORY_TEXT_MAX) -> str:
    """拼「已发生」块：剧情记忆摘要 + 最近一条原始事件。

    **不再按场景过滤**：原来 ``events_text(..., scene=save.current_scene)`` 会让
    DM 换个场景就忘掉之前发生的事；摘要本身跨场景，原始事件也一并跨场景取。

    还没有任何记忆时（新档开局）退回纯流水账视图——那时摘要替代不了什么，
    反而把上下文砍得太狠。

    :param limit: 原始事件条数上限（由调用方按逐人 / 整轮裁剪后给出）
    :param summary_max: 摘要字数上限
    """
    summary = memory_text(save, max_chars=summary_max)
    if not summary:
        # 还没有记忆：按流水账给，但同样不按场景过滤
        return events_text(save, max(1, limit), max_len=120)
    parts = [summary]
    count = max(1, min(limit, _RECENT_EVENTS_MAX))
    recent = events_text(save, count, max_len=_RECENT_EVENT_LEN)
    if recent:
        parts.append("【刚刚】\n" + recent)
    return "\n\n".join(parts)


def _progress_text(save) -> str:
    """本局进度（任务 / 线索 / 时间等）；下划线开头的内部键不给 DM 看。"""
    lines = [
        f"{key}：{value}"
        for key, value in (save.progress or {}).items()
        if not str(key).startswith("_")
    ]
    if save.current_scene:
        lines.append(f"此前所在场景：{save.current_scene}")
    return "\n".join(lines)


def _chapter_brief(save) -> str:
    """当前章节的构架文本（给 DM 与场景构建器看）。

    用 :func:`~...dnd.chapter.chapter_summary` 的 DM 侧形态，所以这一段里
    **包含难度数值与 fail_forward**——那是 DM 该知道、玩家不该看到的。
    后面接世界演化轨的 DM 侧形态（势力位置、痕迹、未结期限），
    再接纪元轨（称号、结局、上一纪元的代价）。
    """
    try:
        text = chapter_summary(save, for_player=False)
    except Exception as e:  # 章节状态读不出来不该拦住切换场景
        log.warning(f"读章节进度失败：{str_traceback(e)}")
        return ""
    extra = ""
    try:
        from ...dnd.evolution import evolution_text, load_evolution

        extra = evolution_text(load_evolution(save), for_player=False)
    except Exception as e:  # 世界演化是增强，读不出来不该拦住
        log.warning(f"读世界演化失败：{str_traceback(e)}")
    try:
        from ...dnd.epoch import epoch_text, load_epoch

        ep = load_epoch(save)
        extra = "\n\n".join(
            x
            for x in (extra, epoch_text(ep, _epoch_facts_of(save, ep), for_player=False))
            if x
        )
    except Exception as e:
        log.warning(f"读纪元状态失败：{str_traceback(e)}")
    return f"{text}\n\n{extra}".strip() if extra else text


def _epoch_facts_of(save, ep) -> object | None:
    """当前纪元的进度事实（读不出来就返回 ``None``，那一段整块不出现）。"""
    from ...dnd.chapter import _load
    from ...dnd.epoch import epoch_facts
    from ...dnd.evolution import load_evolution

    if ep.ending:
        return None
    return epoch_facts(_load(save), load_evolution(save), index=ep.current, length=ep.length)


#: 复盘「裁定」一行要收进来的功能（其余是查询 / 叙述 / 世界书类，不必进流水）
_STORY_RULING_TOOLS = (
    "roll_dice",
    "check",
    "attack",
    "cast_spell",
    "enemy_attack",
    "update_enemy",
    "update_character",
    "update_npc",
    "start_combat",
    "end_combat",
    "rest",
)


def _story_rulings(context) -> list[str]:
    """从 DM 这轮的功能调用里挑出裁定摘要（检定 / 伤害 / 法术位……）。"""
    log = context.data.get("tool_log")
    if not isinstance(log, list):
        return []
    lines: list[str] = []
    for item in log:
        if not isinstance(item, dict):
            continue
        if str(item.get("function", "")) not in _STORY_RULING_TOOLS:
            continue
        text = " ".join(str(item.get("result", "") or "").split())
        if not text:
            continue
        lines.append(text[:240] + ("…" if len(text) > 240 else ""))
        if len(lines) >= 8:
            break
    return lines


#: 要回显给玩家的判定功能（暗骰故意不在其中：暗骰只给 DM 看）
_PLAYER_RULING_TOOLS = (
    "check",
    "roll_dice",
    "attack",
    "enemy_attack",
    "cast_spell",
    "heal",
)


def _one_line(text: str) -> str:
    """把多行结算文案压成一行（判定块每行一条）。"""
    return " ".join(str(text or "").split())


def _player_rulings(
    save, actions, order, context, skip: set[str] | None = None
) -> list[str]:
    """收集这一轮要发给玩家的判定明细（骰点 + 成败）。

    两个来源：① 规则判断器在提交行动时就已经掷掉的检定；② DM 这轮调用
    check / attack / cast_spell 等功能的返回。暗骰（secret_roll）永远不会进来。

    :param skip: 这些玩家的预结算检定**已经**在逐人结算时给过了，不再重复回显。
    """
    skip = skip or set()
    lines: list[str] = []
    for user_id in order:
        if str(user_id) in skip:
            continue
        resolution = _one_line((actions.get(user_id) or {}).get("resolution", ""))
        if resolution:
            lines.append(f"{_player_label(save, user_id)}：{resolution}")
    log = context.data.get("tool_log")
    if isinstance(log, list):
        for item in log:
            if not isinstance(item, dict):
                continue
            if str(item.get("function", "")) not in _PLAYER_RULING_TOOLS:
                continue
            text = _strip_dm_noise(_one_line(str(item.get("result", ""))))
            if text:
                lines.append(text)
    return list(dict.fromkeys(lines))


#: 判定明细里混着的「给 DM / 给系统」的话，玩家不需要也不该看到。
#: 「自动选用的武器：xx」尤其要剥掉——玩家自己拿了什么武器他是知道的，
#: 这句只是让**DM** 核对系统有没有猜错。
#:
#: 开头一次性吃掉整句：`自动选用的武器：` 之后的内容里可能带半角括号
#: （如「短弓（远程，常规射程 80 尺）」），所以用非贪婪的 `.+?` 配到
#: 第一个全角 `）`，再收掉随之多出来的一个右括号。
_DM_NOISE_RE = re.compile(r"（自动选用的武器：.+?））?")


def _strip_dm_noise(text: str) -> str:
    """判定明细发出去之前，先剥掉 DM 专有的说明。"""
    return _DM_NOISE_RE.sub("", text).strip()


def _party_wiped(save) -> bool:
    """所有玩家都已阵亡（死亡豁免 3 次失败）——本局该收尾了。"""
    ids = list(save.player_ids or [])
    if not ids:
        return False
    for user_id in ids:
        card = get_character(save, user_id)
        if card is None or not card.is_dead:
            return False
    return True


def _mark_wrapping_up(save) -> None:
    """全员阵亡时自动进入收尾流程（幂等）：让 wrap_up 能被调用、并写进复盘。"""
    if save.progress.get("wrapping_up"):
        return
    save.progress["wrapping_up"] = True
    save_save(save)
    log_event(save, "note", "全员阵亡：本局进入收尾", actor="dm")


def _ending_text(save) -> str:
    """本轮给 DM 的结档状态提示。

    **只在真的要收尾时注入**：纪元收束达成了结局、玩家发过 ``/跑团.结档``，
    或者全队已经阵亡。平时这一段整块不出现（省 token），
    而「不能自行结档」的规矩写在系统提示词里。
    """
    ending = str((save.progress or {}).get("ending") or "")
    if ending:
        title = ENDINGS.get(ending, (ending, ""))[0]
        summary = ENDINGS.get(ending, ("", ""))[1]
        return (
            f"收束判定达成了结局【{title}】：{summary}"
            "这是这一局的终点——请写一段收尾叙述，把这一纪元（乃至整局）如何落幕讲清楚，"
            "呼应提示词里给出的那个称号，然后在这一轮或最近几轮里调用 wrap_up 结束本局。"
        )
    if _party_wiped(save):
        return (
            "全员阵亡：队伍已经没有能行动的玩家了。请写一段收尾叙述，"
            "交代这一局是怎么落幕的，然后调用 wrap_up 结束本局。"
        )
    if save.progress.get("wrapping_up"):
        return (
            "玩家已经决定为本次跑团结档：请顺势把剧情推向收束——该收的伏笔收拢、"
            "该告别的人告别，并在这一轮或最近几轮的叙述里给出一个像样的结局；"
            "然后调用 wrap_up 结束本局（存档与复盘都会保留）。"
        )
    return ""


def _finish_save(save, chat_id: str, *, note: str = "本局就此结档。") -> None:
    """结档：清战斗与回合、标记结束、解绑会话，并在复盘末尾落一条结档线。"""
    clear_combat(save)
    clear_round(save)
    save.progress["ended"] = True
    save_save(save)
    if chat_id:
        clear_binding(str(chat_id))
    try:
        append_story_ending(save, note)
    except OSError as e:
        log.warning(f"写复盘结档线失败：{str_traceback(e)}")


async def build_scene_for_save(save, generic: str) -> str | None:
    """用场景构建器把**通用场景**改造成**本局专属场景**；失败返回 ``None``。

    这是链路上**唯一**允许看章节卡的地方：本局专属化只发生在存档的场景副本里，
    世界库那份通用场景原样不动——所以换一个存档、换一条路线跑，
    同一个世界的骨架还是同一副。
    """
    try:
        with bind_save(save.save_id):
            result = await scene_builder_agent.run(
                scene=generic,
                adventure=_chapter_brief(save),
                party=_party_text(save),
                progress=_progress_text(save),
                history=events_text(save, 10),
            )
            record_result(result)
    except Exception as e:
        log.warning(f"场景构建失败：{str_traceback(e)}")
        return None
    content = (result.content or "").strip()
    return content or None


async def open_scene(
    save, location: str, *, kind: str = "", region: str = ""
) -> tuple[str, str] | None:
    """切换到某个场景：缺什么生成什么，构建本局副本，并记录进出。

    链路：生成器产出**通用场景**（世界库）→ 构建器改成**本局专属场景** →
    写进存档的场景副本。之后以存档副本为准，变化由 ``update_scene`` 追加。

    :return: ``(场景键, 场景正文)``；无法定位地点时返回 ``None``。
    """
    resolved = await ensure_scene(
        save.world_key, location, kind=kind, region=region
    )
    if resolved is None:
        return None
    _kind, key, generic = resolved

    if not has_scene(save, key):
        built = await build_scene_for_save(save, generic)
        copy_scene(save, key, built if built is not None else generic)
        if built is None:
            log.warning(f"场景「{key}」构建失败，已退回通用场景")

    # 进出场景只是剧情上的转场，不往场景副本里记流水：
    # 那类记录不会影响下次读取，只会在提示词里堆噪声（转场已由复盘记下）。
    save.current_scene = key
    save_save(save)
    return key, read_scene(save, key) or generic


def _can_act(save, user_id: str) -> bool:
    """玩家还能不能真正提交行动：倒地 / 阵亡的不能。"""
    card = get_character(save, str(user_id))
    if card is None:
        return False
    return not (card.is_dead or card.is_downed)


def _filter_waiting(save, ids: list[str] | None) -> list[str] | None:
    """把倒地 / 阵亡的玩家从等待名单里剔掉。

    他们交不出真正的行动，留在名单里只会把整局卡住；他们若想说话仍可以 @我，
    提交的内容会带着「倒地 / 阵亡」标注一起交给 DM。
    """
    if ids is None:
        return None
    return [uid for uid in ids if _can_act(save, uid)]


def _downed_note(card) -> str:
    """倒地 / 阵亡玩家提交行动时的系统标注（交给 DM）。"""
    if card.is_dead:
        return "已阵亡，无法行动：玩家的描述只能当作遗言 / 旁白"
    if card.is_stable_down:
        return "昏迷不醒（0 血，伤势已稳定），无法行动：玩家的描述只能当作呓语"
    return (
        f"倒地濒死（死亡豁免 {card.death_saves_success} 成功 / "
        f"{card.death_saves_failures} 失败），无法行动：玩家的描述只能当作呓语"
    )


def _player_label(save, user_id: str) -> str:
    """玩家在提示词 / 文案里的显示名：优先 lk 绑定名，其次角色名，最后 QQ 号。"""
    card = get_character(save, user_id)
    return player_name(user_id, card.name if card is not None else "")


def is_started(save) -> bool:
    """这一局是否已经开过场（``/跑团.开始`` 之后才允许行动）。

    新存档由 ``_open_save`` 显式写 ``started=False``；没有这个标记的老存档按
    「已经跑过场」处理（有剧情轮次或当前场景），免得升级后老局卡在等待开局。
    """
    if save.progress.get("started") or save.progress.get("starting"):
        return True
    if "started" in save.progress:
        return False
    return bool(save.progress.get("_story_round") or (save.current_scene or "").strip())


#: DM 写「等待全员」时可用的词
_ALL_WORDS = {"全部", "所有", "所有人", "全员", "全体", "大家", "all", "*"}
#: DM 写「不等任何人」时可用的词（谁先提交就立刻主持）
_NONE_WORDS = {"无", "没有", "不等", "谁都可以", "任何人", "none", "0"}
#: 分隔名单里的多个玩家
_SPLIT_RE = re.compile(r"[、,，;；/\s]+")
#: 行动文本里的移动类关键词（战斗里用来记「本回合移动过」，只做软提示）
_MOVE_RE = re.compile(r"(移动|跑|走|冲|跳|爬|潜行|躲藏|后退|上前|靠近|绕到|窜|扑过去)")

#: 玩家明确表示「把东西收进装备栏」的说法。
#:
#: 这类句子不需要 DM 裁决（东西已经在战利品里了），走系统直接落盘就能完成，
#: 省掉一次模型往返。判定刻意保守：必须同时出现「收纳动词 + 东西」，
#: 且全文**只**在讲收纳时；带「问 / 看看 / 能不能」等试探语的一律不拦，
#: 交回 DM 处理（可能是在打听能不能捡）。
_EQUIP_TAKE_RE = re.compile(
    r"(收下|收起|装备上|装备|捡起|捡走|拿走|收好|揣|带上|披上|穿上|换上|装上)"
)
#: 试探性说法：玩家在问，不是在下指令
_EQUIP_HESITATE_RE = re.compile(
    r"(能不能|可以吗|可否|要不要|是否|询问|问问|找找|看看|看下|搜|翻)"
)
#: 出现这些词说明玩家在打而不是在收纳（「砍了地精的斧子」≠ 收下斧子）
_EQUIP_COMBAT_RE = re.compile(
    r"(砍|劈|刺|攻击|打|杀|射|砍向|劈向|刺向|投掷|砸|踢|撞|砍死|杀死|击败)"
)
#: 前置壳：主语与礼貌用语，剥掉后剩下的才是东西名
_EQUIP_SHELL_RE = re.compile(r"^(?:我要|我想|我|请|麻烦|帮我|给|把|将)+")
#: 切碎后落单的无义词（「收下」「穿上」被切剩的残片）
_EQUIP_NOISE_RE = re.compile(r"(?:下来|起来|上|走|了|吧|的|都|和|与|并)")
#: 玩家明确在打东西的说法
_ATTACK_RE = re.compile(
    r"(攻击|攻打|砍|劈|刺|射|砸|投掷|踢|撞|扑|杀|干掉|解决|放倒|砍倒|劈倒)"
)


def _stale_targets(text: str, combat) -> list[str]:
    """行动里点到的名字里，有几个是**已经退场**的敌人。

    玩家在战斗里砍一个上一轮就倒下的地精，是最常见的空转：DM 得读完整段
    叙述才发现目标不在，白白花掉一整轮。这里纯代码判定，零模型开销。
    """
    if combat is None:
        return []
    standing = {c.name for c in living_enemies(combat)}
    out_names = [
        c.name
        for c in combat.order
        if c.is_enemy and c.name not in standing and c.name and c.name in text
    ]
    # 玩家同时点了还在场的敌人 → 大概是想打那个，不拦
    if any(name in text for name in standing):
        return []
    return out_names


def _precheck_note(text: str, combat) -> str:
    """行动前置体检：发现「打空气」时给玩家一句提醒（空串 = 没问题）。

    只提醒不拦——玩家可能是在描述幻觉 或者干脆在演剧情，
    真要拦就变成跟玩家吵架了。
    """
    stale = _stale_targets(text, combat)
    if not stale:
        return ""
    alive = "、".join(c.name for c in living_enemies(combat)) or "（没有了）"
    return (
        f"💡 提醒：【{'、'.join(stale)}】已经不在场上了（可能已倒下或撤离）。\n"
        f"现在还站着的敌人：{alive}\n"
        "如果没别的打算，可以改成对上面这些名字发起行动。"
    )


def _stale_brief(text: str, combat) -> str:
    """同一体检，但只给 DM 一句话（省得他自己从叙述里推目标死没死）。"""
    stale = _stale_targets(text, combat)
    if not stale:
        return ""
    alive = "、".join(c.name for c in living_enemies(combat)) or "无"
    return f"系统提示：行动点到的 {'、'.join(stale)} 已退场，现存敌人 {alive}"


def _player_status_line(save, state) -> str:
    """轮到下一名玩家时，给他一份自己的状态回执（纯数据推导，零模型开销）。

    战斗中玩家最容易缺的信息是**自己还剩多少血、法术位用掉几个、有没有挂状态**——
    这些数据全在角色卡上，只是没机会告诉他。以前要等他自己问、或等 DM 偶然提起。
    这里直接拼一行：只给「有变化或有风险」时才出声，全满全空时不刷屏。
    """
    actor = current_actor(state)
    if actor is None or actor.kind != "player":
        return ""
    card = get_character(save, actor.cid)
    if card is None:
        return ""
    parts = [f"HP {card.hp}/{card.max_hp}　AC {effective_ac(save, actor, state)}"]
    # 只在真有问题时提示：低血、临时生命、状态、力竭、法术位
    if card.hp > 0 and card.hp <= max(1, card.max_hp // 3):
        parts.append("⚠️ 血量偏低")
    if card.temp_hp:
        parts.append(f"临时生命 {card.temp_hp}")
    conditions = card.conditions_text()
    if conditions:
        parts.append(f"状态：{conditions}")
    slots = card.spell_slots_text()
    # 非法术师别显示「（无法术位）」——那是在说他没有法术位，不是说他只剩这些
    if slots and card.max_spell_slots():
        parts.append(f"法术位 {slots}")
    # 倒地 / 失能的人别列「还可用动作」——他根本不能行动，说了就是骗他
    if not is_out(save, actor) and not is_incapacitated(
        combatant_conditions(save, actor)
    ):
        left = _remaining_actions(actor)
        if left:
            parts.append(f"本回合还可用：{left}")
    death = card.death_save_text()
    if death:
        parts.append(death)
    return "　".join(parts)


#: 玩家会看到的回合资源（顺序即展示顺序，与 combat.TURN_RESOURCES 对齐）
_RESOURCE_LABELS = (("action", "动作"), ("bonus", "附赠动作"), ("reaction", "反应"))


def _remaining_actions(actor) -> str:
    """这一回合**还剩下什么可做**（比「已用」更直接可用）。

    玩家看到「本回合已用：动作」时得自己推「那我现在还能干嘛」；直接把剩余的
    说出来省一次心算。移动按剩余距离给，且单独成句——混进动作列表里会被读成
    「移动是一个可以选的动作」，而它其实是额度。
    """
    left = [
        label
        for key, label in _RESOURCE_LABELS
        if not getattr(actor, f"{key}_used", False)
    ]
    text = "、".join(left)
    if not actor.moved:
        text = f"{text}、移动" if text else "移动"
    else:
        rest = actor.speed - actor.move_used
        if rest > 0:
            text = f"{text}（还可移动 {rest} 尺）" if text else f"还可移动 {rest} 尺"
    return text


def _equip_take_request(text: str) -> list[str]:
    """玩家是不是在明确地「把东西收进装备栏」；是的话给出东西名列表。

    只认「收纳动词 + 列举东西」这一种句式，且排除试探语气与战斗动词，
    避免把「我想砍死拿斧子的地精」这类正常战斗行动误吞掉。

    宾语在动词前（「把长剑收下」）还是在后（「捡起那把短剑」）都说得通，
    所以不按位置切，而是把动词当分隔符通吃整句，再清掉壳词。
    """
    body = str(text or "").strip()
    if not body or len(body) > 60:
        return []
    if not _EQUIP_TAKE_RE.search(body):
        return []
    if _EQUIP_HESITATE_RE.search(body) or _EQUIP_COMBAT_RE.search(body):
        return []
    # 只把**第一个**动词当分隔符：「收起战利品里的所有装备」里的「装备」
    # 是被收纳的东西，不能再当一次动词，否则尾巴被吃空。
    body = _EQUIP_TAKE_RE.sub("、", body, count=1)
    # 去掉主语与前置壳，保留「那把」这类定语（装备表认得就行）
    body = _EQUIP_SHELL_RE.sub("", body, count=1).strip("，,、 ")
    names = [n.strip() for n in _SPLIT_RE.split(body) if n.strip()]
    names = [n for n in names if not _EQUIP_NOISE_RE.fullmatch(n)]
    return names[:6]


def resolve_players(save, text: str) -> tuple[list[str], list[str]]:
    """把 DM 写的「等待名单」解析成 ``(user_id 列表, 没认出来的名字)``。

    - 空 /「全员」类写法 → 存档里的所有玩家；
    - 「无」类写法 → 空名单（谁先提交就立刻主持）；
    - 其余按 `、,，;；/` 与空格拆分，逐个按角色名或 user_id 认人。
    """
    raw = str(text or "").strip()
    if not raw:
        return [], []
    lowered = raw.lower()
    if lowered in _ALL_WORDS:
        return list(save.player_ids), []
    if lowered in _NONE_WORDS:
        return [], []

    ids: list[str] = []
    unknown: list[str] = []

    def _add(user_id: str) -> None:
        if user_id not in ids:
            ids.append(user_id)

    for part in _SPLIT_RE.split(raw):
        part = part.strip()
        if not part:
            continue
        token = part.lower()
        if token in _ALL_WORDS:
            for uid in save.player_ids:
                _add(uid)
            continue
        if token in _NONE_WORDS:
            continue
        card = find_character(save, part)
        if card is None or card.user_id not in save.player_ids:
            if part not in unknown:
                unknown.append(part)
            continue
        _add(card.user_id)
    return ids, unknown


#: 最后一名玩家提交后交给 DM 的整轮要求
_FINAL_PHASE = (
    "本轮所有玩家都已提交行动：把每个人的结果按顺序串起来，写出这些行动叠加后的局势变化，"
    "再给出「接下来你们可以」。已经掷过的检定不要重掷。**篇幅 250 字以内**。"
)


def _combat_banner(save, state, narration: str) -> str:
    """开战那一刻给玩家的系统提示：战斗现状 + 怎么行动（只进玩家消息，不进复盘）。

    DM 的功能调用结果只有 DM 自己看得到，玩家此前只能收到 DM 的叙述——进入战斗时
    往往不知道已经切成回合制、现在轮到谁、能做什么。这里补一份系统级提示：
    先攻顺序、站位与距离照 ``combat_text`` 给，操作说明单独两行。
    """
    lines = ["⚔️ 战斗开始！先攻顺序与站位如下，之后每回合系统都会提示轮到谁："]
    for line in combat_text(save, state, with_map=False).splitlines():
        # 「地图布局与上一轮相同」是给 DM 的地图节流说明，开战第一帧没有上一轮
        if line.startswith("（地图布局"):
            continue
        lines.append(line)
    actor = current_actor(state)
    if actor is not None:
        lines.append(
            f"🎯 现在轮到【{_player_label(save, actor.cid)}】行动：@我 描述你要做什么——"
            "攻击 / 移动 / 施法 / 躲避 / 掩护队友 / 使用道具都可以；"
            "命中与伤害由系统掷骰，状态与抗性自动结算。"
        )
    lines.append(
        "📏 每回合 = 1 个动作 + 1 个附赠动作 + 1 次移动 + 1 次反应（系统自动记账）；"
        "武器够不着会先被打回，靠近再打；倒地 / 阵亡的玩家回合会被系统跳过。"
    )
    return f"{narration}\n\n" + "\n".join(lines)


def _partial_phase(save, state, user_id: str) -> str:
    """逐人结算时告诉 DM：现在只处理这一名玩家，别提前收束整轮。"""
    waiting = [uid for uid in (state.waiting or []) if uid in state.actions]
    index = waiting.index(str(user_id)) + 1 if str(user_id) in waiting else len(waiting)
    total = len(state.waiting or []) or len(save.player_ids) or 1
    return (
        f"这是本轮的逐人结算（第 {index}/{total} 名玩家）。现在**只处理**"
        f"「{_player_label(save, user_id)}」这一次行动：只写他这一行动的即时结果与影响，"
        "不总结全场、不替其他还没提交的玩家行动、不写「接下来你们可以」，也不要宣告本轮结束"
        "——等其他玩家都行动完，系统会再让你做整轮总结。**篇幅 150 字以内**。"
    )


async def submit_action(
    *,
    save_id: str,
    user_id: str,
    action: str,
    chat_id: str = "",
    history_limit: int = 10,
) -> TurnResult:
    """接住一名玩家的行动：规则把关 → 存进本轮 → 逐人结算 / 末位整轮总结。

    - 规则判断不通过：直接打回 / 追问，**不**保存；
    - 通过且名单上还有人没提交：**立刻**只把这一名玩家的结果讲出来（逐人结算），
      并提示还在等谁；在那之前他仍可再 @ 覆盖自己的行动；
    - 名单（由 DM 决定，默认全员）都提交后：带全部行动由 DM 输出整轮结果；
    - **战斗中**：名单由先攻顺序决定，只有当前回合的玩家能提交，提交即结算该回合。
    """
    save = load_save(save_id)
    if save is None:
        return TurnResult(
            Verdict.NEED_INFO,
            message="这局冒险的存档不见了，发送 /跑团.开局 重新开启。",
        )

    text = (action or "").strip()
    card = get_character(save, user_id)
    if card is None:
        return TurnResult(
            Verdict.NEED_INFO,
            message=f"你还没有加入这局冒险，发送 /跑团.加入 {save.save_id} 加入。",
        )
    if not is_started(save):
        return TurnResult(
            Verdict.NEED_INFO,
            message=(
                "🎲 这一局还在等人加入，开局之前不能行动：\n"
                f"· 加入：/跑团.加入 {save.save_id}\n"
                "· 人齐后由任意已加入的玩家发 /跑团.开始 正式开局"
            ),
        )
    if not text:
        return TurnResult(Verdict.NEED_INFO, message="请描述你想让角色做什么。")

    # 「把 X 收下」这类纯收纳动作：东西已经在战利品里，不需要 DM 裁决，
    # 系统直接落盘并回执——省掉一整轮模型往返，也省玩家等。
    take = _equip_take_request(text)
    if take and card is not None:
        known, unknown = card.equip(take)
        set_character(save, card)
        got = [*known, *unknown]
        if got:
            return TurnResult(
                Verdict.ACCEPT,
                message=(
                    f"🎒 已收进【{card.name}】的装备栏：{'、'.join(got)}\n"
                    f"当前装备：{'、'.join(card.equipment) or '（空）'}"
                ),
            )

    # 倒地 / 阵亡的玩家：不能真正行动，但**不硬挡**——他提交的内容会带上标注
    # 交给 DM（当作呓语 / 遗言），这样全队倒下时剧情也不会卡死。
    downed_note = _downed_note(card) if (card.is_dead or card.is_downed) else ""

    judged = await dnd_judge.judge_action(text, card)
    if not judged.judge.accepted:
        return TurnResult(judged.judge.verdict, message=judged.judge.message())

    # 规则判断器若已直接结算了检定 / 掷骰，把结果一并留给 DM，避免重复掷骰。
    resolution = judged.judge.reason if judged.needs_check else ""

    state = load_round(save)
    combat = load_combat(save)
    # 「打空气」提醒（只有战斗里才有）；不拦，只是提醒，见 _precheck_note。
    stale_note = ""
    stale_brief = ""
    if combat is not None:
        actor = current_actor(combat)
        if actor is not None and is_out(save, actor):
            # 当前该行动的玩家已倒地 / 阵亡 / 状况外：推进到下一名能行动的玩家
            # （路过倒地濒死的玩家时，advance_turn 会顺手替他掷死亡豁免）
            advance_turn(save, combat)
            save_combat(save, combat)
            actor = current_actor(combat)
        if actor is not None and str(user_id) != actor.cid:
            return TurnResult(
                Verdict.NEED_INFO,
                message=(
                    f"⚔️ 现在是【{actor.name}】的回合，等他行动完再轮到你。\n\n"
                    + combat_text(save, combat)
                ),
            )
        state.waiting = [actor.cid] if actor is not None else []
        state.actions = {}
        # 行动经济（软提示）：行动里写了移动类动作就记一次「本回合移动过」。
        if actor is not None and _MOVE_RE.search(text):
            use_resource(save, combat, actor.cid, "move")
        # 前置体检：玩家打的是已经退场的敌人时，先提醒一句再照常提交。
        # 不拦（他可能另有安排），但省掉 DM 花一整轮才发现目标不在。
        stale_note = _precheck_note(text, combat)
        # DM 侧只补一句，不新增提示词行；玩家若已改行动，下轮就不会再提。
        stale_brief = _stale_brief(text, combat) if stale_note else ""
    elif state.waiting is None:
        # DM 还没指定过等待名单：开局默认等全员（倒地 / 阵亡的除外）
        state.waiting = _filter_waiting(save, list(save.player_ids))

    state.actions[str(user_id)] = {
        "action": text,
        "resolution": resolution,
        "note": downed_note,
        "hint": stale_brief,
    }
    save_round(save, state)

    waiting = state.pending_ids()
    if waiting:
        names = "、".join(_player_label(save, uid) for uid in waiting)
        prefix = f"⚠️ {card.name} 现在{downed_note}。\n" if downed_note else ""
        if combat is not None or not settings.use_partial(save):
            # 战斗中按先攻逐回合结算；或者设置里关掉了逐人结算（省模型调用）
            hint = f"\n\n{stale_note}" if stale_note else ""
            return TurnResult(
                Verdict.ACCEPT,
                message=(
                    prefix + f"✅ 已记下你的行动（{_player_label(save, user_id)}）。\n"
                    f"还在等：{names}。\n"
                    "DM 会在名单上的玩家都提交后统一主持；在那之前你可以再 @我 修改自己的行动。"
                    + hint
                ),
                waiting_for=waiting,
            )
        # 剧情轮：先把**这一名玩家**的结果讲出来；整轮总结等最后一人提交后再给。
        partial = await run_dm_round(
            save_id=save.save_id,
            actions={str(user_id): state.actions[str(user_id)]},
            chat_id=chat_id,
            history_limit=history_limit,
            phase=_partial_phase(save, state, str(user_id)),
            partial=True,
        )
        partial.waiting_for = waiting
        if partial.narration:
            partial.narration += f"\n\n（还在等 {'、'.join(names)} 提交行动。）"
        return partial

    # 名单上的人都提交了：清空本轮行动（保留名单），再交给 DM 做整轮总结。
    actions = state.actions
    state.actions = {}
    save_round(save, state)
    return await run_dm_round(
        save_id=save.save_id,
        actions=actions,
        chat_id=chat_id,
        history_limit=history_limit,
        phase=_FINAL_PHASE if combat is None else "",
        # 前面几名的检定结果已经在逐人结算时回显过，整轮总结里不重复
        skip_resolution=(
            {str(uid) for uid in actions if str(uid) != str(user_id)}
            if combat is None and settings.use_partial(save)
            else None
        ),
    )


#: 开场轮交给 DM 的「行动」文本：代替玩家提交，让 DM 直接给出开场白
_OPENING_PROMPT = (
    "【系统】本局冒险正式开始。这是开场轮，没有玩家行动："
    "请做开场白——结合世界起点与故事设定开篇，点出在场角色的处境与眼前的机会；"
    "然后用 move_scene 把玩家们带进第一个地点并交代入场；"
    "最后用 await_players 声明第一轮要等待行动的玩家（默认全员）。"
    "开场白的结尾，按叙述要求给玩家两三条「现在可以做什么」的具体提示。"
)


async def run_dm_round(
    *,
    save_id: str,
    actions: dict[str, dict[str, str]],
    chat_id: str = "",
    history_limit: int = 10,
    opening: bool = False,
    phase: str = "",
    partial: bool = False,
    skip_resolution: set[str] | None = None,
) -> TurnResult:
    """把本轮的玩家行动交给 DM 主持、落事件日志，并记下 DM 决定的下一轮等待名单。

    :param actions: ``{user_id: {"action": ..., "resolution": ...}}``。
    :param opening: ``True`` 时为开场轮：没有玩家行动，DM 直接给出开场白。
    :param phase: 本轮进度说明（逐人结算 / 整轮总结），只说给 DM 听。
    :param partial: ``True`` 表示逐人结算：只讲这一名玩家的结果，**不**推进等待名单、
        不推进战斗回合、不写复盘。
    """
    save = load_save(save_id)
    if save is None:
        return TurnResult(
            Verdict.NEED_INFO,
            message="这局冒险的存档不见了，发送 /跑团.开局 重新开启。",
        )

    # 先按等待名单的顺序排，再补上名单外也提交了的玩家。
    order = [uid for uid in (load_round(save).waiting or []) if uid in actions]
    for uid in actions:
        if uid not in order:
            order.append(uid)
    if not order:
        return TurnResult(Verdict.NEED_INFO, message="这一轮还没有收到任何玩家的行动。")

    blocks: list[str] = []
    resolved_any = False
    for user_id in order:
        item = actions.get(user_id) or {}
        action = str(item.get("action", "") or "").strip()
        if not action and not opening:
            continue
        resolution = str(item.get("resolution", "") or "").strip()
        note = str(item.get("note", "") or "").strip()
        hint = str(item.get("hint", "") or "").strip()
        if opening:
            # 开场轮没有玩家行动，DM 直接主持。
            continue
        head = f"### {_player_label(save, user_id)}"
        if note:
            head += f"（⚠️ {note}）"
        entry = f"{head}\n{action}"
        if hint:
            entry += f"\n（{hint}）"
        if resolution:
            entry += f"\n（规则判断器已结算：{resolution}）"
            resolved_any = True
        blocks.append(entry)

        # 逐人结算先不记行动流水：整轮总结时会把所有行动一起记下来，
        # 免得历史里同一句行动出现两遍（那一轮的叙述照样会记）。
        if not partial:
            log_event(save, "action", action, actor=str(user_id))
            if resolution:
                log_event(save, "check", resolution, actor=str(user_id))

    if opening:
        action_text = _OPENING_PROMPT
    else:
        if not blocks:
            return TurnResult(
                Verdict.NEED_INFO, message="这一轮还没有收到任何玩家的行动。"
            )

        action_text = "\n\n".join(blocks)
        if len(blocks) > 1 and not partial:
            action_text = "本轮有多名玩家同时行动，请一并处理：\n\n" + action_text

    # 场景只给简介（名称 / 定位 / 氛围 / 最近变化）；要看布局、在场者、隐藏信息，
    # 由 DM 自己用 get_scene 查完整副本。
    scene = scene_brief(save)

    # 世界圣经：专有名词与事实的唯一来源（人名 / 势力 / 地名不跑偏的关键）；
    # 没有圣经的老世界照常空着，不影响主持。
    bible = bible_text(save.world_key, max_chars=settings.world_bible_max_chars())

    # 全队阵亡：自动进入收尾流程，让这一轮就能收尾（wrap_up 才不会被拦）
    if not partial and _party_wiped(save):
        _mark_wrapping_up(save)

    # 历史与 NPC 名单按设置裁剪：每轮都会重发，条数/长度直接乘在成本上。
    #
    # 「已发生」这一块有两部分：
    # - **剧情记忆摘要**（跨场景，结构化：已发生 / 待回收 / 关系）——替代原来那
    #   几百字符的流水账，换场景也不会失忆；
    # - **最近两三条原始事件**——保住「上一轮具体发生了什么」的即时细节。
    # 两部分合起来比原来更短，且覆盖全场景；更早的旧事由 DM 用 recall 按需查。
    limit = max(1, min(history_limit, settings.history_events()))
    if partial:
        limit = max(1, limit // 2)
    history = _history_block(
        save,
        limit,
        summary_max=_MEMORY_TEXT_MAX_PARTIAL if partial else _MEMORY_TEXT_MAX,
    )
    # 本局出场过的具名 NPC：带着旧伤与旧账，供 DM 复用而不是重新造人。
    roster_limit = settings.npc_roster_limit()
    roster = (
        npcs_text(save, limit=roster_limit)
        if roster_limit > 0 and list_npcs(save)
        else ""
    )

    # 角色卡：焦点玩家给完整摘要，其余人一行（多人局省一大块）
    focus = order[0] if (partial or len(order) == 1) else ""
    party_block = _party_block(save, order, focus)

    # 战斗中：把先攻顺序、当前回合与随后的敌人回合一并告诉 DM。
    combat = load_combat(save)
    combat_label = (combat.note or "").strip() if combat is not None else ""
    scene_before = (save.current_scene or "").strip()
    combat_block = ""
    if combat is not None:
        following, next_player = peek_following(save, combat)
        # 地图只在布局变化时附：每轮重画十几行很贵，坐标与距离另外给
        digest = map_digest(combat)
        with_map = settings.battle_map_always() or digest != combat.map_hash
        combat_block = combat_text(save, combat, with_map=with_map)
        if with_map and digest:
            combat.map_hash = digest
            save_combat(save, combat)
        if following:
            combat_block += (
                "\n本回合结束后，紧接着由 "
                + "、".join(c.name for c in following)
                + " 行动（请在本次叙述中一并结算它们的回合）。"
            )
        if next_player is not None:
            combat_block += f"\n下一名行动的是：{next_player.name}。"

    context = AgentContext(
        user_id=str(order[0]), save_id=save.save_id, chat_id=str(chat_id)
    )
    # 玩家行动原文放进 context：check / attack 等工具要靠它定难度档位
    # （「我悄悄摸过去」比「我走过去」容易），不塞进来就只能拿技能基准难度。
    context.data["action_text"] = action_text
    try:
        with bind_save(save.save_id):
            result = await dm_agent.run(
                context=context,
                action=action_text,
                character=party_block,
                scene=scene,
                npcs=roster,
                bible=bible,
                chapter=_chapter_brief(save),
                ending=_ending_text(save),
                combat=combat_block,
                history=history,
                phase=phase,
            )
            record_result(result)
    except AgentError as e:
        log.warning(f"DM 主持失败：{str_traceback(e)}")
        return TurnResult(
            Verdict.ACCEPT, message=f"⚠️ DM 一时语塞：{e}\n请稍后再 @我 试试。"
        )

    # DM 的功能调用（切场景 / 改进度）与计费都是各自读写存档副本的：重新读一份最新的，
    # 免得下面用跑团开始前的旧对象落盘，把切好的场景、进度和刚记上的用量写回旧值。
    latest = load_save(save.save_id)
    if latest is not None:
        save = latest

    narration = (result.content or "").strip() or "……（DM 沉默了片刻）"
    # 「接下来你们可以」只用于提醒正在玩的玩家：进历史 / 复盘时去掉。
    archived = strip_player_hints(narration)
    log_event(save, "narration", archived, actor="dm")
    rulings_for_player = _player_rulings(
        save, actions, order, context, skip=skip_resolution
    )

    # 逐人结算：这一名玩家的结果已经讲完，等待名单 / 战斗回合 / 复盘都不动，
    # 等最后一名玩家提交后再做整轮总结。
    if partial:
        return TurnResult(
            Verdict.ACCEPT,
            narration=narration,
            resolved=resolved_any,
            rulings=rulings_for_player,
        )

    # 下一轮要等谁：DM 可以用 await_players 声明；战斗中则由先攻顺序决定。
    declared = context.data.get("await_players")
    ids: list[str] | None = None
    if declared is not None:
        ids, unknown = resolve_players(save, str(declared))
        if unknown:
            log.warning("DM 指定的等待名单里有认不出的玩家：" + "、".join(unknown))
        ids = _filter_waiting(save, ids)

    state = load_round(save)
    state.actions = {}
    combat = load_combat(save)
    if combat is not None and not all_enemies_out(combat):
        if not context.data.get("combat_started"):
            # 本回合结束，推进到下一名玩家；中间的敌人回合交给下一轮叙述
            advance_turn(save, combat)
            save_combat(save, combat)
        _following, next_player = peek_following(save, combat)
        state.waiting = [next_player.cid] if next_player is not None else []
        # 玩家侧提示：开战那一条给完整的战斗现状 + 操作说明，往后每轮只报下一位
        if context.data.get("combat_started"):
            narration = _combat_banner(save, combat, narration)
        elif next_player is not None:
            # 轮到下一位了：给**他自己**一份状态回执（HP / 法术位 / 状态 / 已用资源）。
            # 以前玩家只能靠问才知道这些，而问一次就是一轮模型往返。
            status_line = _player_status_line(save, combat)
            turn_hint = (
                f"\n\n⏭️ 下一回合轮到【{_player_label(save, next_player.cid)}】行动。"
            )
            if status_line:
                turn_hint += f"\n{status_line}"
            narration += turn_hint
    elif combat is not None:
        # 敌人全部退场：先结算经验与战利品，再自动结束战斗，等待名单交回 DM
        report = settle_combat(save, combat)
        clear_combat(save)
        settlement = report.text()
        log_event(save, "combat", "战斗结束：敌人全部退场\n" + settlement, actor="dm")
        # 结算要玩家也看得见：XP / 升级 / 战利品是他们的
        narration = f"{narration}\n\n{settlement}"
        archived = f"{archived}\n\n{settlement}"
        rulings_for_player = [*rulings_for_player, settlement]
        state.waiting = ids
    else:
        state.waiting = ids if ids is not None else state.waiting
    save_round(save, state)

    # —— 复盘：DM 主持结束后追加一次（玩家行动 + 裁定摘要 + DM 叙述）——
    scene_after = (save.current_scene or "").strip()
    transition = ""
    if scene_before and scene_after and scene_after != scene_before:
        transition = f"{scene_before} → {scene_after}"
    rulings = _story_rulings(context)
    for user_id in order:
        item = actions.get(user_id) or {}
        resolution = str(item.get("resolution", "") or "").strip()
        if resolution:
            rulings.insert(0, f"{_player_label(save, user_id)}：{resolution}")
    try:
        append_story(
            save,
            round_no=next_story_round(save),
            scene=scene_after or scene_before,
            combat=combat_label,
            actions=(
                []
                if opening
                else [
                    (
                        _player_label(save, uid),
                        str((actions.get(uid) or {}).get("action", "")),
                    )
                    for uid in order
                ]
            ),
            rulings=rulings,
            narration=archived,
            transition=transition,
        )
    except OSError as e:  # 复盘写失败不该影响这一轮的叙述
        log.warning(f"写复盘失败：{str_traceback(e)}")

    # —— 结档：玩家 /跑团.结档（DM 用 wrap_up 收尾），或全队阵亡后自动结档 ——
    ended = bool(context.data.get("wrap_up"))
    if ended:
        _finish_save(save, chat_id)
    elif not partial and _party_wiped(save):
        # 兜底：最后一击发生在这次叙述里（DM 没来得及 wrap_up）也不能卡在半局
        _finish_save(save, chat_id, note="全员阵亡，本局就此结档。")
        narration = (
            narration + "\n\n（全员阵亡，本局就此结档。想开新的一局用 /跑团.开局。）"
        )
        ended = True

    return TurnResult(
        Verdict.ACCEPT,
        narration=narration,
        resolved=resolved_any,
        ended=ended,
        rulings=rulings_for_player,
    )


async def run_opening(*, save_id: str, chat_id: str = "") -> TurnResult:
    """开场轮：不等玩家行动，直接由 DM 给出开场白并把玩家带进第一个场景。

    ``/跑团.开局`` 只建存档、不跑开场；等玩家陆续 ``/跑团.加入`` 之后，由任意
    已加入的玩家发 ``/跑团.开始`` 调到这里正式开局。

    开局前会先织出**第一章**：这是章节线唯一「不问玩家」的自动一步——第一张卡
    由 DM 依章节规则选定走向（``take_chapter("1")``），之后再每一章都由玩家挑。
    """
    save = load_save(save_id)
    if save is None:
        return TurnResult(
            Verdict.NEED_INFO,
            message="这局冒险的存档不见了，发送 /跑团.开局 重新开启。",
        )
    if current_card(save) is None:
        from .chapter_flow import ensure_opening_chapter, weave_chapter

        try:
            await ensure_opening_chapter(save)
            outcome = await weave_chapter(save, "1")
        except Exception as e:
            # 织不出章节不该拦住开局：DM 照样能主持，只是这一局没有任务卡
            log.warning(f"开局织第一章失败（继续开局）：{str_traceback(e)}")
            outcome = None
        if outcome is not None and outcome.problems:
            log.warning(f"开局第一章有校验提醒：{outcome.problems}")
        save_save(save)
    actions = {str(uid): {"action": "", "resolution": ""} for uid in save.player_ids}
    result = await run_dm_round(
        save_id=save.save_id, actions=actions, chat_id=chat_id, opening=True
    )
    # 开场白出得来才整理手册：没开局成功就没必要给玩家一份背景资料。
    # 结果挂到 result 上，由 handler 决定要不要发群文件。
    if result.narration.strip():
        latest = load_save(save.save_id) or save
        path, text = build_world_brief(latest)
        result.world_brief_path = path
        result.world_brief_text = text
    return result
