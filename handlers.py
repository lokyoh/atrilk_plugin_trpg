"""跑团插件的指令处理逻辑。

所有交互都围绕「玩家 → 机器人 → 玩家」的对话循环展开：

- ``建卡``：多步状态机（:class:`~.core.dnd.creation.CreationSession`），每走一步
  都会落盘（``core.dnd.session_store``）。整条流程**完全由磁盘驱动**：玩家回复的
  消息由 ``continue_card`` 这条常驻响应器接住，而不是依赖静置
  ``session_expire_timeout``（默认 60 秒）就会被销毁的临时 ``reject`` 循环，
  因此建卡可以慢慢来，跨小时、跨天，甚至机器人重启后都能接着走。
- 其余指令都是一次性的，直接读取 / 写回角色卡（``core.dnd.storage``）。

本插件只支持 OneBot 适配器，因此事件统一按 ``MessageEvent`` 处理。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from nonebot import get_bot, get_driver
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.params import ArgPlainText, CommandArg
from nonebot.rule import Rule

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.message import img_msg
from ATRI.service import is_in_service

from . import plugin
from .core.dnd import (
    ABILITY_MAP,
    SKILL_MAP,
    CreationError,
    CreationSession,
    CreationStep,
    DndCharacter,
    Save,
    add_player,
    clean_name,
    clear_binding,
    clear_combat,
    clear_round,
    combat_text,
    create_save,
    creation_prompt,
    current_actor,
    delete_save,
    delete_session,
    format_report,
    get_binding,
    get_character,
    get_skill,
    get_weapon,
    get_world,
    get_world_map,
    list_npcs,
    list_saves,
    list_sessions,
    list_worlds,
    load_card,
    load_combat,
    load_save,
    load_session,
    npcs_text,
    parse_item_stack,
    parse_skill_request,
    player_name,
    purge_expired,
    read_doc,
    record_usages,
    report,
    save_card,
    save_save,
    save_session,
    set_binding,
    set_character,
    story_files,
    strip_reroll,
    wants_reroll,
)
from .core.dnd.clock import clock_text
from .core.dnd.creation import session_from_card
from .core.dnd.event_progress import event_summary
from .core.dnd.events import load_pool
from .core.dnd.image_render import (
    combat_to_html,
    data_card_html,
    narration_to_html,
    player_panel_html,
)
from .core.trpg import DiceError, reroll_check, roll
from .matchers import (
    create_card,
    edit_card,
    end_game,
    finish_game,
    join_save,
    make_check,
    query_data_cmd,
    resume_game,
    roll_dice,
    show_card,
    show_save,
    start_game,
    start_round,
)

#: 改卡（重做建卡某一步）会话在 ``matcher.state`` 里的键
_EDIT_SESSION_KEY = "trpg_edit_session"

#: 建卡过程中可用来中途放弃的关键词
_CANCEL_WORDS = {"取消", "退出", "cancel", "算了", "不建了", "不建了。"}

#: 一次骰子指令里多个表达式的分隔符
_DICE_SEP = re.compile(r"[,，;；、]+")


# --------------------------------------------------------------------------- #
# 角色卡图片渲染
# --------------------------------------------------------------------------- #
#
# 看卡 / 建卡完成 / 改卡完成 / 看存档 都改用 ``html_to_pic`` 把角色卡渲染成图片发送；
# 浏览器未就绪或渲染异常时退回纯文本卡（``card.summary()``），不让展示这一步卡死。

_CARD_VIEWPORT = {"width": 540, "height": 10}


async def _card_image(card: DndCharacter) -> MessageSegment:
    """把角色卡渲染成一张图片消息段；渲染失败时退回纯文本卡。

    ``html_to_pic`` 在函数内**延迟导入**：插件加载阶段就 import 它会让
    ATRI 的加载器报「htmlrender is not loaded as a plugin」（模块先进了
    ``sys.modules``），渲染时机反正远在加载完成之后，不急于这一时。
    """
    try:
        from ATRI.system.htmlrender import html_to_pic

        data = await html_to_pic(
            card.to_html(),
            viewport=_CARD_VIEWPORT,
            device_scale_factor=2,
        )
        return img_msg(data)
    except Exception as e:  # 浏览器未安装 / 渲染异常：退回文字版，保证能看
        log.warning(f"角色卡图片渲染失败，退回文字版：{str_traceback(e)}")
        return MessageSegment.text("\n\n" + card.summary())


async def _card_result(text: str, card: DndCharacter) -> Message:
    """把一段说明文字与角色卡图片拼成一条消息（图片失败则拼文字卡）。

    图片成功渲染时，底部附一行常用指令名词，方便玩家点按复制后发指令。
    """
    msg = Message(text)
    seg = await _card_image(card)
    msg += seg
    if getattr(seg, "type", "") == "image":
        msg += Message("\n\n" + _quick_terms_footer("card"))
    return msg


# 图片消息底部附的「常用名词」：玩家点按即可复制，粘进消息里 @ 机器人描述行动或发指令。
# 角色卡图给常用指令（静态）；战斗图优先用 _combat_quick_footer 收集的本场真实名词
# （敌人名 / 武器名 / 法术名），收集不到时才退回这里的静态行动动词。
_CARD_QUICK_TERMS = ["/跑团.看卡", "/跑团.改卡", "/跑团.检定", "/跑团.骰", "/跑团.存档"]
_COMBAT_QUICK_TERMS = ["攻击", "施法", "移动", "躲闪", "协助", "撤离", "专注", "长休"]


def _quick_terms_footer(kind: str) -> str:
    """图片消息底部附一行可复制的常用名词。

    kind='card' 给角色卡常用指令；kind='combat' 给战斗常用行动动词。
    """
    if kind == "combat":
        return "📋 常用行动：" + " · ".join(_COMBAT_QUICK_TERMS)
    return "📋 常用指令：" + " · ".join(_CARD_QUICK_TERMS)


def _combat_quick_footer(save_id: str) -> str:
    """战斗图底部的常用名词：这场仗里真实可点按复制的内容——
    敌人名、武器名、法术名，粘进 @我的消息里就能直接用
    （如「攻击 地精1」「施法 火球术」）。收集不到时退回静态行动动词。"""
    try:
        save = load_save(save_id)
        if save is None:
            return _quick_terms_footer("combat")
        combat = load_combat(save)
        if combat is None:
            return _quick_terms_footer("combat")

        enemies: list[str] = []
        weapons: list[str] = []
        spells: list[str] = []
        for c in combat.order:
            if c.kind == "enemy" and not c.out and c.name not in enemies:
                enemies.append(c.name)
            if c.kind != "player" or c.out:
                continue
            card = get_character(save, c.cid)
            if card is None:
                continue
            for raw in card.equipment:
                stack = parse_item_stack(str(raw))
                if get_weapon(stack.name) is not None and stack.name not in weapons:
                    weapons.append(stack.name)
            for s in card.known_spells():
                if s.cn not in spells:
                    spells.append(s.cn)

        parts: list[str] = []
        if enemies:
            shown = enemies[:4]
            if len(enemies) > 4:
                shown.append(f"等{len(enemies)}个敌人")
            parts.append("敌人：" + " · ".join(shown))
        if weapons:
            parts.append("武器：" + " · ".join(weapons[:4]))
        if spells:
            parts.append("法术：" + " · ".join(spells[:4]))
        if not parts:
            return _quick_terms_footer("combat")
        return "📋 " + "　".join(parts)
    except Exception as e:  # 附词是锦上添花，出错退回静态词表
        log.warning(f"收集战斗常用名词失败，退回静态词表：{str_traceback(e)}")
        return _quick_terms_footer("combat")


# --------------------------------------------------------------------------- #
# 战斗：每轮附战场图 + 当前玩家面板（装备 / 法术 / 可用动作）图片
# --------------------------------------------------------------------------- #
#
# 进入战斗后，每一轮把战场现状与当前行动者的面板渲染成图片随叙述一起发出；
# 渲染失败（浏览器未就绪等）单张退回文字，不让这一轮卡住。

_COMBAT_VIEWPORT = {"width": 540, "height": 10}


async def _render_html_image(html_text: str) -> MessageSegment | None:
    """把一段 HTML 渲染成图片消息段；失败返回 ``None``（交给调用方退回文字）。"""
    try:
        from ATRI.system.htmlrender import html_to_pic

        data = await html_to_pic(
            html_text, viewport=_COMBAT_VIEWPORT, device_scale_factor=2
        )
        return img_msg(data)
    except Exception as e:  # 浏览器未安装 / 渲染异常：退回文字，保证能看
        log.warning(f"战斗图片渲染失败，退回文字版：{str_traceback(e)}")
        return None


async def _combat_images(save_id: str) -> list[MessageSegment]:
    """当前若在战斗中，返回 [战场图, 当前玩家面板] 图片段；没有战斗返回空列表。"""
    try:
        save = load_save(save_id)
        if save is None:
            return []
        combat = load_combat(save)
        if combat is None or not combat.order:
            return []

        actor = current_actor(combat)
        current_cid = actor.cid if actor is not None else None

        # 玩家的状态以角色卡为准：逐个读出来传给战场图
        conditions_by_cid: dict[str, str] = {}
        for c in combat.order:
            if c.kind == "player":
                card = get_character(save, c.cid)
                if card is not None:
                    cond = card.conditions_text()
                    if cond:
                        conditions_by_cid[c.cid] = cond

        segs: list[MessageSegment] = []
        battlefield = await _render_html_image(
            combat_to_html(
                combat,
                conditions_by_cid=conditions_by_cid,
                current_cid=current_cid,
            )
        )
        if battlefield is not None:
            segs.append(battlefield)

        # 只给「当前该行动的玩家」出面板：他把装备 / 法术 / 还剩什么动作看得最清楚
        if actor is not None and actor.kind == "player":
            card = get_character(save, actor.cid)
            if card is not None:
                panel = await _render_html_image(
                    player_panel_html(card, combatant=actor)
                )
                if panel is not None:
                    segs.append(panel)
        return segs
    except Exception as e:  # 附图是锦上添花，任何异常都不许影响本轮叙述
        log.warning(f"生成战斗图片失败（叙述照常发送）：{str_traceback(e)}")
        return []


def _combat_actor_hint(save_id: str) -> str:
    """战斗中补一句「现在轮到谁」——之前只在 DM 提示词与等待提示里提过，
    玩家自己的叙述里常常看不出此刻该谁动。"""
    try:
        save = load_save(save_id)
        if save is None:
            return ""
        combat = load_combat(save)
        if combat is None:
            return ""
        actor = current_actor(combat)
        if actor is None:
            return ""
        return f"🎯 当前行动者：【{actor.name}】"
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# DM 叙述卡：把叙述渲染成图片，顶部带此刻的时间 / 地点 / 事件
# --------------------------------------------------------------------------- #


def _world_time_text(save) -> str:
    """当前世界时间的展示文本；取不到就留空（这一条就不显示）。"""
    try:
        return clock_text(load_pool(save))
    except Exception:
        return ""


def _scene_title(save) -> str:
    """当前场景的展示名：优先文档标题，取不到就退回文档键。"""
    key = (save.current_scene or "").strip()
    if not key:
        return ""
    try:
        doc = read_doc(save.world_key, "scene", key)
    except Exception:
        doc = None
    title = (doc.title or "").strip() if doc is not None else ""
    return title or key


def _event_line(save) -> str:
    """眼下这件事（「【第 2 件事】黑帆疑云」）；没有事件就留空。"""
    try:
        lines = event_summary(save).splitlines()
    except Exception:
        return ""
    return lines[0].strip() if lines else ""


async def _story_image(
    save_id: str, narration: str, *, rulings: list[str] | None = None
) -> MessageSegment | None:
    """把 DM 叙述渲染成一张卡；渲染失败返回 ``None``（调用方退回纯文本）。"""
    try:
        save = load_save(save_id)
        time_text = place_text = event_text = ""
        in_combat = False
        if save is not None:
            time_text = _world_time_text(save)
            place_text = _scene_title(save)
            event_text = _event_line(save)
            in_combat = load_combat(save) is not None
        return await _render_html_image(
            narration_to_html(
                narration,
                time_text=time_text,
                place_text=place_text,
                event_text=event_text,
                combat=in_combat,
                rulings=rulings,
            )
        )
    except Exception as e:  # 卡是加分项：出错就退回纯文本，绝不让叙述发不出去
        log.warning(f"DM 叙述卡渲染失败，退回纯文本：{str_traceback(e)}")
        return None


# --------------------------------------------------------------------------- #
# 建卡：会话落盘 + 常驻续接
# --------------------------------------------------------------------------- #
#
# 磁盘会话（``core.dnd.session_store``）是唯一真相，内存里的 ``_ACTIVE_USERS``
# 只是为了少做磁盘判断。机器人重启后由 ``_hydrate_active`` 从磁盘恢复。

#: 正在建卡的玩家（字符串 user_id）
_ACTIVE_USERS: set[str] = set()


def _mark_active(user_id: str) -> None:
    _ACTIVE_USERS.add(str(user_id))


def _unmark_active(user_id: str) -> None:
    _ACTIVE_USERS.discard(str(user_id))


def _hydrate_active() -> int:
    """启动时清理过期会话，并把未过期的建卡会话恢复为「进行中」。"""
    purge_expired()
    user_ids = list_sessions()
    _ACTIVE_USERS.update(user_ids)
    return len(user_ids)


# 插件加载时就把磁盘上的在建会话接回来，机器人重启也不会丢进度。
_hydrate_active()


async def _has_active_session(event: Event) -> bool:
    """续接规则：该玩家有未完成的建卡会话，且这条消息不是指令。"""
    user_id = str(getattr(event, "user_id", ""))
    if not user_id or user_id not in _ACTIVE_USERS:
        return False
    text = (event.get_plaintext() or "").strip()
    starts = tuple(get_driver().config.command_start or {"/"})
    return not (text and starts and text.startswith(starts))


#: 建卡续接：把建卡会话的后续消息接住，不依赖随时会过期的临时响应器。
continue_card = plugin.on_message(
    name="",
    rule=is_in_service("跑团") & Rule(_has_active_session),
    priority=95,
    block=True,
)


@continue_card.handle()
async def _continue_creation(event: MessageEvent):
    user_id = event.get_user_id()
    session = load_session(user_id)
    if session is None:
        # 会话已过期或被清掉：放行给其它插件，并停止续接。
        _unmark_active(user_id)
        await continue_card.skip()

    text = event.get_plaintext().strip()
    if text.lower() in _CANCEL_WORDS:
        is_levelup = session.purpose == "levelup"
        delete_session(user_id)
        _unmark_active(user_id)
        if is_levelup:
            await continue_card.finish(
                "已跳过本次升级选择（角色卡保持原样）。"
                "随时可以用 /跑团.改卡 子职 或 /跑团.改卡 属性加点 补上。"
            )
        await continue_card.finish("已取消建卡，随时发送 /跑团.建卡 重新开始。")

    out, character = await _advance(session, text)
    if character is not None:
        _unmark_active(user_id)
    await continue_card.finish(out)


def _reply(session: CreationSession) -> str:
    """取当前步骤的指引文本。"""
    return creation_prompt(session.step, session)


async def _advance(
    session: CreationSession, text: str
) -> tuple[str, DndCharacter | None]:
    """把玩家回复推进一格并落盘。

    :return: ``(要发送的文案, 完成的角色卡)``；尚未完成时角色卡为 ``None``。
    """
    try:
        out = session.handle(text)
    except CreationError as e:
        save_session(session)
        return f"⚠️ {e}\n\n{_reply(session)}\n（不想建了可以回复「取消」）", None

    save_session(session)

    if session.step is CreationStep.DONE:
        try:
            character = session.build()
        except CreationError as e:  # 正常流程不会走到这里
            return f"⚠️ {e}\n\n{_reply(session)}", None

        try:
            if session.purpose == "levelup" and session.save_id:
                # 升级发生在游戏内：新卡写回**存档**（战斗与检定读的是存档副本），
                # 同时同步插件目录的全局卡，保证 /跑团.看卡 在局外也一致。
                save = load_save(session.save_id)
                if save is not None:
                    set_character(save, character)
                save_card(character)
            else:
                save_card(character)
        except Exception as e:  # 落盘失败不应让玩家以为建好了
            log.warning(f"保存角色卡失败：{str_traceback(e)}")
            return f"⚠️ 保存角色卡失败：{e}", None

        delete_session(session.user_id)
        if session.purpose == "levelup":
            return (
                await _card_result(
                    "✅ 升级选择完成，已写回角色卡，继续游戏吧！",
                    character,
                ),
                character,
            )
        return (
            await _card_result(
                "🎲 角色卡已保存！\n"
                "发送 /跑团.看卡 查看，/跑团.检定 察觉 DC15 开始检定。",
                character,
            ),
            character,
        )

    # 各步骤的回复已由核心自带下一步指引，这里不再重复追加。
    return out, None


@create_card.handle()
async def _start_creation(event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    inline = args.extract_plain_text().strip()

    existing = load_card(user_id)
    session = load_session(user_id)
    is_new = session is None or session.step is CreationStep.DONE
    if is_new:
        session = CreationSession(user_id=user_id)

    # 标记为「正在建卡」并立刻落盘，后续消息由 continue_card 接住。
    _mark_active(user_id)
    save_session(session)

    if inline:
        # 允许「/跑团.建卡 精灵」这样直接带上一步的回答。
        out, character = await _advance(session, inline)
        if character is not None:
            _unmark_active(user_id)
            await create_card.finish(out)
        await create_card.send(out)
        return

    prompt = _reply(session)
    if is_new and existing is not None:
        prompt = f"⚠️ 你已有一张角色卡，完成建卡后会将其覆盖。\n\n{prompt}"
    await create_card.send(f"{prompt}\n\n（回复「取消」可中途放弃建卡）")


# --------------------------------------------------------------------------- #
# 看卡
# --------------------------------------------------------------------------- #


@show_card.handle()
async def _show_card(event: MessageEvent):
    card = load_card(event.get_user_id())
    if card is None:
        await show_card.finish("你还没有角色卡，发送 /跑团.建卡 创建一张吧。")
    await show_card.finish(await _card_image(card))


# --------------------------------------------------------------------------- #
# 掷骰
# --------------------------------------------------------------------------- #


@roll_dice.handle()
async def _roll_dice(args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await roll_dice.finish(
            "用法：/跑团.骰 2d6+3\n"
            "支持 1d20、4d6、2d10-1 等写法，多个表达式用逗号分隔（如「2d6+3, 1d8」）。"
        )

    parts = [p.strip() for p in _DICE_SEP.split(raw) if p.strip()]
    lines: list[str] = []
    for expr in parts:
        try:
            result = roll(expr, label="掷骰")
        except DiceError as e:
            await roll_dice.finish(f"⚠️ {e}")
        lines.append(result.describe())
    await roll_dice.finish("\n".join(lines))


# --------------------------------------------------------------------------- #
# 检定
# --------------------------------------------------------------------------- #


@make_check.handle()
async def _make_check(event: MessageEvent, args: Message = CommandArg()):
    card = load_card(event.get_user_id())
    if card is None:
        await make_check.finish("你还没有角色卡，发送 /跑团.建卡 创建一张后再检定吧。")

    raw = args.extract_plain_text().strip()
    if not raw:
        await make_check.finish(
            "用法：/跑团.检定 察觉 DC15 优势\n"
            "可写技能（察觉 / 运动）、属性（力量）或豁免（力量豁免 DC20）。"
        )

    # 写了「重骰 / 重投」就消耗英雄激励重掷一次——与行动判定同一套口径：
    # 只有玩家自己写明才消耗，且**必须采用新结果**。
    wants = wants_reroll(raw)
    parsed = parse_skill_request(strip_reroll(raw) if wants else raw)
    if parsed is None:
        await make_check.finish(
            "没看懂这个检定。示例：「察觉」「力量检定」「力量豁免 DC15」「隐匿 劣势」；"
            "手上有英雄激励时可加「重骰」。"
        )

    key, dc, advantage = parsed
    try:
        if "豁免" in raw and key in ABILITY_MAP:
            result = card.check_save(key, dc, advantage=advantage)
        elif key in SKILL_MAP:
            result = card.check_skill(key, dc, advantage=advantage)
        else:
            result = card.check_ability(key, dc, advantage=advantage)
    except KeyError as e:  # 理论上不会发生
        await make_check.finish(f"⚠️ {e}")

    note = ""
    if wants:
        if card.spend_inspiration():
            result = reroll_check(result)
            save_card(card)
        else:
            note = "\n（想重骰，但身上没有英雄激励：这次结果照旧）"
    await make_check.finish(result.describe() + note)


# --------------------------------------------------------------------------- #
# 查询数据（优先返回图片，渲染失败退回文本）
# --------------------------------------------------------------------------- #


@query_data_cmd.handle()
async def _query_data(event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw or " " not in raw:
        await query_data_cmd.finish(
            "用法：/跑团.查 <表> <名字>\n"
            "表：怪物 / 法术 / 种族 / 背景 / 职业 / 子职 / 武器 / 护甲 / 魔法物品\n"
            "例：/跑团.查 怪物 地精武者　/跑团.查 法术 火球术　/跑团.查 子职 狂战士"
        )
    table, name = raw.split(" ", 1)
    name = name.strip()

    # 优先返回图片：能做成「卡」的静态数据表走图片，其余（或渲染失败）退回文本
    try:
        html = data_card_html(table, name)
    except Exception as e:  # 准备图片卡失败直接退回文本
        log.warning(f"查询数据图片准备失败，退回文本：{str_traceback(e)}")
        html = None
    if html:
        seg = await _render_html_image(html)
        if seg is not None:
            await query_data_cmd.finish(seg)

    # 文本兜底（含「查不到」的提示）
    try:
        from .core.agents.dnd.tools import query_data_text
    except Exception:
        query_data_text = None
    text = query_data_text(table, name) if query_data_text else ""
    if not text:
        text = (
            "没查到相关内容，换个表或名字试试（表：怪物 / 法术 / 种族 / 背景 / "
            "职业 / 武器 / 护甲 / 魔法物品）。"
        )
    await query_data_cmd.finish(text)


# --------------------------------------------------------------------------- #
# 改卡
# --------------------------------------------------------------------------- #

#: 可重做的建卡步骤：步骤 -> 触发词（第一个为展示名）
_REDO_STEPS: tuple[tuple[CreationStep, tuple[str, ...]], ...] = (
    (CreationStep.LEVEL, ("等级", "level")),
    (CreationStep.ABILITY, ("属性", "ability", "roll", "掷属性")),
    (CreationStep.RACE, ("种族", "race")),
    (CreationStep.LANGUAGES, ("语言", "language", "languages")),
    (CreationStep.PROFESSION, ("职业", "profession", "class")),
    (CreationStep.SUBCLASS, ("子职", "子职业", "subclass")),
    (CreationStep.BACKGROUND, ("背景", "background")),
    (CreationStep.BOOST, ("属性提升", "起源提升", "背景属性提升", "boost")),
    (CreationStep.SKILL, ("技能", "skill")),
    (CreationStep.FEATURES, ("特性", "职业特性", "feature")),
    (CreationStep.EQUIPMENT, ("装备", "装备包", "equipment")),
    (CreationStep.SPELLS, ("法术", "法术表", "spell", "spells")),
    (CreationStep.ASI, ("属性加点", "asi", "加点")),
    (CreationStep.ALIGNMENT, ("阵营", "alignment")),
)

#: 触发词 -> 步骤
_STEP_LOOKUP: dict[str, CreationStep] = {
    word.lower(): step for step, words in _REDO_STEPS for word in words
}

#: 步骤 -> 展示名（提示文案里用「属性」而不是「roll属性」这种内部名）
_STEP_LABELS: dict[CreationStep, str] = {step: words[0] for step, words in _REDO_STEPS}

#: 通用用法提示：改卡就是重做建卡中的一步
_EDIT_USAGE = (
    "用法：/跑团.改卡 <步骤>\n"
    "改卡就是重做建卡中的一步：回复步骤后，按提示给出新的选择即可。\n"
    + "\n".join(f"· /跑团.改卡 {words[0]}" for _, words in _REDO_STEPS)
    + "\n也可以直接带上新选择，例如 /跑团.改卡 种族 精灵。"
)


def _resolve_step(token: str) -> CreationStep | None:
    """按触发词解析要重做的步骤，未命中返回 ``None``。"""
    return _STEP_LOOKUP.get(token.strip().lower())


def _session_from_card(card: DndCharacter) -> CreationSession:
    """把已保存的角色卡还原成建卡会话（实现挪到了 :func:`.creation.session_from_card`，
    升级选择会话也用它）。"""
    return session_from_card(card)


def _reset_dependents(session: CreationSession, step: CreationStep) -> list[str]:
    """重做某步后清理与它冲突的下游选择，返回给玩家的提示。"""
    if step is CreationStep.ASI:
        # 重做「属性加点」= 把旧的等级加点全部撤掉，按次数重新分配
        session.asi_done = 0
        if session.asi:
            session.asi = {}
            return ["旧的等级加点已清空，请按提示重新分配"]
        return []

    if step is CreationStep.LEVEL:
        # 等级变了，允许的加点次数跟着变：清空重分配，免得降级后还留着多出来的 +2
        session.asi_done = 0
        if session.asi:
            session.asi = {}
            return ["等级变了，旧的等级加点已清空，请按新等级重新分配"]
        return []

    if step is not CreationStep.PROFESSION:
        return []
    profession = session.profession_obj
    if not profession:
        return []

    notes: list[str] = []
    # 子职隶属于具体职业：换职业后旧子职无效（choose_profession 已清，这里兜底提示）
    sub = session.subclass_obj
    if sub and sub.profession != profession.key:
        session.subclass = ""
        notes.append("子职已清空（不属于新职业），可用 /跑团.改卡 子职 重新选")
    allowed = set(profession.skill_pool)
    removed = [k for k in session.skills if k not in allowed]
    if removed:
        session.skills = [k for k in session.skills if k in allowed]
        notes.append(
            "新职业没有的技能已移除：" + "、".join(get_skill(k).cn for k in removed)
        )

    # 装备包按职业区分（不同职业的包键可能重名），换职业后统一回到新职业的默认装备。
    session.equipment_package = ""
    session.equipment = list(profession.fixed_equipment)
    notes.append("装备已换回新职业的默认装备，可用 /跑团.改卡 装备 重新选择")

    # 法术按职业区分：换职业后旧法术可能不在新职业的法术表里，统一清空重选。
    cantrips, known = session.spell_picks()
    if cantrips or known:
        session.spells = []
        notes.append(
            f"法术已清空（{profession.cn} 可重选 戏法 {cantrips} 个 + 法术 {known} 个，"
            "可用 /跑团.改卡 法术）"
        )
    elif session.spells:
        session.spells = []
        notes.append(f"{profession.cn} 没有施法能力，法术已清空")

    # 1 级特性的选择按职业区分（圣职 / 原初职能 / 战斗风格 / 祈唤 / 专精 / 武器精通）
    if session.feature_choices or session.expertise or session.weapon_masteries:
        session.feature_choices = {}
        session.expertise = []
        session.weapon_masteries = []
        notes.append("1 级特性选择已清空，可用 /跑团.改卡 特性 重新选")
    return notes


def _apply_edit_step(
    session: CreationSession, text: str
) -> tuple[DndCharacter | None, str]:
    """重做会话当前这一步。

    :return: ``(新角色卡, 说明)``；失败时角色卡为 ``None``，说明是错误提示。
    """
    step = session.step
    try:
        session.handle(text)
    except CreationError as e:
        return None, f"⚠️ {e}\n\n{creation_prompt(session.step, session)}"

    if session.step is step:
        # 这一步还没走完（例如回复「roll」只是掷出候选属性组，还要玩家挑一组）。
        # 此时 session 里的属性仍是旧值，直接 build 会把刚掷出来的结果丢掉。
        return None, creation_prompt(session.step, session).strip()

    if session.step is CreationStep.BOOST:
        # 换背景后必须重选「属性提升」：此时 session.boost 已被清空，
        # 直接 build 会把规则给的三点丢掉，所以把这一步先交给玩家。
        return None, creation_prompt(session.step, session).strip()

    notes = _reset_dependents(session, step)
    try:
        character = session.build()
    except CreationError as e:
        return None, f"⚠️ {e}\n\n{creation_prompt(session.step, session)}"

    message = f"✅ 已重做「{_STEP_LABELS.get(step, step.value)}」。"
    if notes:
        message += "\n" + "\n".join(f"· {note}" for note in notes)
    return character, message


async def _save_or_finish(character: DndCharacter) -> None:
    """保存角色卡；写盘失败时直接结束这一轮。"""
    try:
        save_card(character)
    except Exception as e:  # 落盘失败不应让玩家以为改成功了
        log.warning(f"保存角色卡失败：{str_traceback(e)}")
        await edit_card.finish(f"⚠️ 保存角色卡失败：{e}")


@edit_card.handle()
async def _start_edit(
    event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
):
    card = load_card(event.get_user_id())
    if card is None:
        await edit_card.finish("你还没有角色卡，发送 /跑团.建卡 创建一张吧。")

    raw = args.extract_plain_text().strip()
    if not raw:
        await edit_card.finish(_EDIT_USAGE)

    parts = raw.split(maxsplit=1)
    step = _resolve_step(parts[0])
    if step is None:
        await edit_card.finish(f"没有「{parts[0]}」这个可重做的步骤。\n\n{_EDIT_USAGE}")

    session = _session_from_card(card)
    session.step = step
    matcher.state[_EDIT_SESSION_KEY] = session

    if len(parts) > 1:
        # 允许「/跑团.改卡 种族 精灵」这样直接带上新的选择。
        new_card, message = _apply_edit_step(session, parts[1])
        matcher.state[_EDIT_SESSION_KEY] = session
        if new_card is None:
            # 一步没走完（如「属性 roll」只掷出候选组）：接着等他回复，
            # 不能直接结束，否则掷出来的属性组会被丢掉。
            await edit_card.reject(message)
        await _save_or_finish(new_card)
        await edit_card.finish(await _card_result(message, new_card))

    await edit_card.send(
        f"重做「{_STEP_LABELS.get(step, step.value)}」：\n\n{creation_prompt(step, session)}"
    )


@edit_card.got("reply")
async def _continue_edit(
    event: MessageEvent,
    matcher: Matcher,
    reply: str = ArgPlainText("reply"),
):
    session = matcher.state.get(_EDIT_SESSION_KEY)
    if not isinstance(session, CreationSession):
        await edit_card.finish("改卡会话已过期，请重新发送 /跑团.改卡。")

    text = (reply or "").strip()
    if text.lower() in _CANCEL_WORDS:
        matcher.state.pop(_EDIT_SESSION_KEY, None)
        await edit_card.finish("已取消改卡。")

    new_card, message = _apply_edit_step(session, text)
    matcher.state[_EDIT_SESSION_KEY] = session
    if new_card is None:
        await edit_card.reject(message)

    await _save_or_finish(new_card)
    await edit_card.finish(await _card_result(message, new_card))


# --------------------------------------------------------------------------- #
# 冒险：开局（多世界 + 世界内多故事）/ 加入 / 存档 / 结束
# --------------------------------------------------------------------------- #
#
# 一局冒险 = 一份存档（``core.dnd.save_store``）。本会话（群 / 私聊）与存档的绑定
# 记在 ``saves/bindings.json``；绑定之后，玩家 @ 机器人描述行动就进入 DM 主持。
#
# 开局分两步选择：先选**世界**（或用 /跑团.开局 生成 [主题] 生成新世界），
# 再选这个世界里的**故事**（或生成新故事）。选择状态只保留在内存里，
# 超时后重新发送 /跑团.开局 即可重来。
#
# 场景切换不在玩家指令里：完全由 DM 通过 ``move_scene`` 决定，并由 DM 叙述离场 / 入场。

#: 生成世界时进度汇报的最小间隔（秒），避免刷屏
_PROGRESS_INTERVAL = 4.0

#: 「选择世界 / 故事」的有效期（秒）：在这段时间内 /跑团.开局 <序号> 即为确认
_START_PICK_TTL = 600.0

#: 「生成新世界 / 新故事」的触发词
_GEN_WORDS = {"生成", "重生成", "generate", "regenerate", "new"}

#: 「用无尽玩法开局」的触发词。与 :data:`...dnd.gamemode.MODES` 里的 words
#: 同源，这里再写一份是为了在**解析参数**阶段就能分辨「玩法词」与「世界名」，
#: 不必先把世界列表读出来。
_ENDLESS_WORDS = {"无尽", "endless", "--无尽", "--endless", "-无尽"}


@dataclass
class _StartPending:
    """本会话正在进行的开局选择状态。"""

    user_id: str
    #: 当前阶段：只剩 "world"（选世界）。原先还有 "story"（选故事），
    #: 故事推力文档下线后没有第二阶段了——事件是**建档之后**才织的。
    stage: str
    #: 已选中的世界键（预留：将来若要分阶段选择时用）
    world_key: str = ""
    at: float = field(default_factory=time.monotonic)


#: 等待选择世界 / 故事的会话：聊天键 -> 选择状态
_START_PENDING: dict[str, _StartPending] = {}

#: 后台任务（生成世界等），持有引用防止被垃圾回收
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _chat_key(event: MessageEvent) -> str:
    """本会话的绑定键：群聊按群，私聊按人。"""
    group_id = getattr(event, "group_id", None)
    return f"group_{group_id}" if group_id else f"user_{event.get_user_id()}"


def _pending_pick(key: str, user_id: str) -> _StartPending | None:
    """取本会话有效的开局选择状态；过期或换人时返回 ``None``。"""
    pending = _START_PENDING.get(key)
    if pending is None or pending.user_id != str(user_id):
        return None
    if time.monotonic() - pending.at > _START_PICK_TTL:
        _START_PENDING.pop(key, None)
        return None
    return pending


def _set_pending_pick(key: str, user_id: str, stage: str, world_key: str = "") -> None:
    """记录 / 重置本会话的开局选择状态。"""
    _START_PENDING[key] = _StartPending(
        user_id=str(user_id), stage=stage, world_key=world_key
    )


def _world_intro(world) -> str:
    """世界的展示简介：优先一句话简介，缺省回退到地区列表。"""
    if world.intro:
        return world.intro
    regions, _adjacency = get_world_map(world.key)
    if regions:
        preview = "、".join(regions[:3]) + ("…" if len(regions) > 3 else "")
        return f"地区 {len(regions)} 个：{preview}"
    return "（还没有世界地图）"


async def _ensure_worlds():
    """世界库是空的就先装内置预设世界（零模型调用），再返回世界列表。

    现生成一个世界要一两分钟、还要花钱；**没有世界时默认给预设**——装完照常
    出现在世界列表里，想现生成的仍然可以 ``/跑团.开局 生成 [主题]``。
    """
    worlds = list_worlds()
    if worlds:
        return worlds
    from .core.dnd.presets import ensure_preset, list_presets

    if not list_presets():
        return []
    try:
        ensure_preset()
    except Exception as e:  # 装不上就退回空列表，由调用方给人话提示
        log.warning(f"自动装预设世界失败：{str_traceback(e)}")
        return []
    return list_worlds()


def _world_chooser(worlds, note: str = "") -> str:
    """「选世界」的提示文案。"""
    lines: list[str] = []
    if note:
        lines.append(f"⚠️ {note}")
        lines.append("")
    lines.append(f"🌍 可用的世界（{len(worlds)} 个）：")
    for index, world in enumerate(worlds, 1):
        lines.append(f"· {index}. {world.title}——{_world_intro(world)}")
    lines += [
        "",
        "接下来：",
        "· 选一个世界开局 → /跑团.开局 <序号>（"
        f"{int(_START_PICK_TTL // 60)} 分钟内有效）",
        "· 生成一个新世界 → /跑团.开局 生成 [主题]（不给主题就随机）",
        "· 一条命令开无尽局 → /跑团.开局 无尽（装预设世界 + 按无尽节奏跑）",
        "",
        "世界是所有存档共享的；选完之后每一局会自己织自己的事件，互不影响。",
    ]
    return "\n".join(lines)


def _story_chooser(world_key: str, note: str = "") -> str:
    """「选起始故事」的提示文案。

    开局是**两步选择**：先选世界，再从这个世界里**现成的事件**里挑一件
    当这一局的起点。挑完才建档、等人加入、开局——DM 会按它写开场白，
    而它就是这一局的**入场事件**。

    这个世界没有现成事件时（老世界），直接建档，不多问一轮。
    """
    from .core.dnd.openings import opening_choices_text, opening_options

    options = opening_options(world_key)
    if not options:
        return ""
    lines: list[str] = []
    if note:
        lines.append(f"⚠️ {note}")
        lines.append("")
    lines.append("📖 这个世界里现成的几件事（挑一件当这一局的起点）：")
    lines.append("")
    lines.append(opening_choices_text(world_key))
    lines += [
        "",
        "接下来：",
        f"· 挑一件 → /跑团.开局 <序号>（{int(_START_PICK_TTL // 60)} 分钟内有效）",
        "",
        "选完的那一件就是这一局的入场事件，DM 会从它写开场白。",
        "如果你们想自己另起一个故事——开局时跟 DM 说一声就行，",
        "这些是**可选的**，不是全部。",
    ]
    return "\n".join(lines)


def _bound_save(key: str) -> Save | None:
    """本会话当前绑定的存档（绑定失效时返回 ``None``）。"""
    bound = get_binding(key)
    return load_save(bound) if bound else None


def _chat_saves(key: str) -> list[Save]:
    """属于本会话（开局时记下 ``chat_key``）的存档，按更新时间倒序。"""
    return [save for save in list_saves() if _save_chat_key(save) == key]


def _save_chat_key(save: Save) -> str:
    """存档归属的会话键（旧存档没有这个字段，返回空串）。"""
    return str(save.progress.get("chat_key") or "")


def _save_player_names(save: Save) -> list[str]:
    """存档里玩家的显示名（优先 lk 绑定名），给「谁加入了」这类文案用。"""
    names: list[str] = []
    for user_id in save.player_ids:
        card = get_character(save, user_id)
        name = player_name(user_id, card.name if card is not None else "")
        if name and name not in names:
            names.append(name)
    return names


def _clear_start_flag(save_id: str) -> None:
    """清掉「正在开局」标记：开场失败时让玩家可以再发一次 /跑团.开始。"""
    latest = load_save(save_id)
    if latest is None:
        return
    latest.progress.pop("starting", None)
    save_save(latest)


def _created_save(key: str, user_id: str) -> Save | None:
    """本会话里该玩家创建、还没结档的那一局（每人每群只能开一局）。"""
    for save in _chat_saves(key):
        if str(save.creator_id or "") != str(user_id):
            continue
        if save.progress.get("ended"):
            continue
        return save
    return None


def _paused_save(key: str) -> Save | None:
    """本会话里被 ``/跑团.结束`` 暂停、还没结档的存档。"""
    for save in _chat_saves(key):
        if save.progress.get("ended"):
            continue
        if save.progress.get("paused"):
            return save
    return None


def _open_save(
    user_id: str,
    key: str,
    world_key: str,
    *,
    mode: str = "",
    story_key: str = "",
) -> tuple[str, str]:
    """建立存档、复制角色卡并绑定本会话，返回 ``(存档 ID, 给玩家的文案)``。

    建好存档只是「准备就绪」：**不跑开场白**，等玩家陆续 ``/跑团.加入`` 之后，
    再由任意已加入的玩家发 ``/跑团.开始`` 正式开局（见 :func:`_start_round`）。

    :param mode: 玩法名（见 :mod:`...dnd.gamemode`）。**开局是玩法唯一决定时机**——
        织法与纪元长度必须在织第一章之前落盘，否则第一章就按默认参数织出来了。
    :param story_key: 玩家挑的**入场事件**（世界库 ``event`` 文档的键）。
        空 = 玩家没挑（要自己另起一个），开局时织一件新的，那一件就是入场事件。
    """
    from .core.dnd.gamemode import mode_text, start_mode

    card = load_card(user_id)
    save = create_save(player_cards=[card] if card is not None else [])
    save.creator_id = str(user_id)
    save.world_key = world_key or "main"
    save.story_key = str(story_key or "")
    save.progress["chat_key"] = key
    save.progress["started"] = False  # 等人齐 + /跑团.开始 之后才开局
    picked = start_mode(save, mode)
    save_save(save)
    set_binding(key, save.save_id)

    world = get_world(save.world_key)
    world_name = world.title if world else save.world_key

    text = (
        f"🎲 冒险已就绪：存档 {save.save_id}\n"
        f"世界「{world_name}」· {mode_text(save)}\n"
        "这一局的事件会在**开局时按队伍的实际水平织出来**；"
        "区域 / 城镇 / 自然地点 / 地下城 / 场景同样在用到时按需生成。\n\n"
        "先等大家加入，人齐了再开局：\n"
        f"· 加入：/跑团.加入 {save.save_id}（本群直接发 /跑团.加入 也行）\n"
        f"· 还没有角色卡的先 /跑团.建卡\n"
        "· 人齐后由**任意已加入的玩家**发 /跑团.开始 正式开局\n"
        "· /跑团.存档 查看谁已经加入、进度与用量"
    )
    if save.story_key:
        from .core.dnd.openings import opening_options

        chosen = next(
            (
                o
                for o in opening_options(save.world_key)
                if o.event_key == save.story_key
            ),
            None,
        )
        if chosen is not None:
            text += f"\n\n📖 入场事件：{chosen.title}（DM 会从它写开场白）"
    if picked.key != "standard":
        text += f"\n\n（{picked.title}模式：{picked.note}）"
    return save.save_id, text


async def _generate_world(
    bot, event: MessageEvent, key: str, user_id: str, theme: str
) -> None:
    """后台生成**新世界**，完成后建立存档并汇报。"""
    from .core.agents.dnd import EntryOptions, run_entry_flow

    last: list[tuple[float, str]] = []

    async def progress(message: str) -> None:
        now = time.monotonic()
        # 阶段提示都尽量送到，只压同一句话的重复刷屏
        if last and last[-1][1] == message and now - last[-1][0] < _PROGRESS_INTERVAL:
            return
        last.append((now, message))
        await bot.send(event, message)

    try:
        report_ = await run_entry_flow(
            options=EntryOptions(theme=theme), on_progress=progress
        )
    except Exception as e:
        log.warning(f"生成世界失败：{str_traceback(e)}")
        await bot.send(event, f"⚠️ 世界生成失败：{e}")
        return

    if not report_.ok:
        # 生成失败：立刻停止，不建存档，避免开出一局空冒险。
        await bot.send(
            event,
            "⚠️ 世界生成失败，已中止开局（未创建存档）。\n"
            + report_.summary()
            + "\n请检查模型 / 工作配置后重新发送 /跑团.开局 生成 [主题]。",
        )
        return

    save_id, text = _open_save(user_id, key, report_.world_key)
    # 世界是开局前生成的，用量补记到这一局的存档上
    record_usages(save_id, list(report_.usages))
    # 不在这里跑开场白：等玩家陆续加入，由 /跑团.开始 正式开局
    await bot.send(event, f"{report_.summary()}\n\n{text}")


def _spawn(task: asyncio.Task) -> None:
    """登记后台任务，完成后自动注销。"""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


@start_game.handle()
async def _start_game(
    event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
):
    key = _chat_key(event)
    user_id = event.get_user_id()

    if load_card(user_id) is None:
        await start_game.finish("你还没有角色卡，先发送 /跑团.建卡 创建一张吧。")

    bound = _bound_save(key)
    if bound is not None and bound.progress.get("paused"):
        await start_game.finish(
            f"本会话有一局暂停中的冒险（存档 {bound.save_id}）。\n"
            "· /跑团.继续 接着玩\n"
            "· /跑团.继续 之后再用 /跑团.结束 删除 丢掉存档，才能开新的一局"
        )
    if bound is not None:
        await start_game.finish(
            f"本会话已在进行冒险（存档 {bound.save_id}）。\n"
            "要继续就 @我 描述行动；想重开请先 /跑团.结束 删除。"
        )

    own = _created_save(key, user_id)
    if own is not None:
        _START_PENDING.pop(key, None)
        await start_game.finish(
            f"你在本群已经开过一局（存档 {own.save_id}），每人每群同时只能开一局。\n"
            f"· 接着那一局：/跑团.继续 {own.save_id}\n"
            f"· 想换新的一局：先 /跑团.继续 {own.save_id}，再 /跑团.结束 删除"
        )

    raw = args.extract_plain_text().strip()
    parts = raw.split(maxsplit=1)
    head = parts[0] if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""
    worlds = list_worlds()
    # 顺手清掉过期的开局选择状态（这个调用的副作用就是清理，返回值用不上）
    pending = _pending_pick(key, user_id)

    # ·「选起始故事」阶段：世界已经选好，这一轮的数字是**故事序号**。
    if pending is not None and pending.stage == "story":
        from .core.dnd.openings import opening_options

        options = opening_options(pending.world_key)
        if not options:
            # 这个世界没有开场清单（中途换过世界？）：退回直接建档
            _START_PENDING.pop(key, None)
            world = next((w for w in worlds if w.key == pending.world_key), None)
            if world is None:
                await start_game.finish(
                    "🌍 那个世界已经不在了。发送 /跑团.开局 重新选择。"
                )
            save_id, text = _open_save(user_id, key, world.key)
            await start_game.finish(text)
        if not head.isdigit():
            await start_game.finish(
                _story_chooser(
                    pending.world_key, note="要选一条起始故事，请给一个序号。"
                )
            )
        index = int(head)
        if not (1 <= index <= len(options)):
            await start_game.finish(
                _story_chooser(
                    pending.world_key, note=f"没有第 {index} 条开场剧情，请重新选择。"
                )
            )
        chosen = options[index - 1]
        _START_PENDING.pop(key, None)
        world = next((w for w in worlds if w.key == pending.world_key), None)
        world_title = world.title if world else pending.world_key
        await matcher.send(
            f"🌍 使用世界「{world_title}」· 入场事件「{chosen.title}」，正在建立存档"
            "（之后等大家加入再 /跑团.开始 开局）……"
        )
        save_id, text = _open_save(
            user_id,
            key,
            pending.world_key,
            story_key=chosen.event_key,
        )
        await start_game.finish(text)

    # ·「无尽」：一条命令开局。装预设世界（零模型调用，已装则复用）+ 建档 +
    #   把无尽玩法写进存档（预设世界平时是开局时自动装的，这里只是省掉选世界那一步）。
    if head.lower() in _ENDLESS_WORDS:
        from .core.dnd.presets import ensure_preset, list_presets

        if not list_presets():
            await start_game.finish("插件里没有内置的预设世界（presets/ 是空的）。")
        _START_PENDING.pop(key, None)
        try:
            preset_key = ensure_preset()
        except Exception as e:
            log.warning(f"准备无尽模组失败：{str_traceback(e)}")
            await start_game.finish(f"⚠️ 无尽模组装不上：{e}")
        info = get_world(preset_key)
        await matcher.send(
            f"🌍 使用预设世界「{info.title if info is not None else preset_key}」，"
            "正在建立无尽模式的存档……"
        )
        save_id, text = _open_save(user_id, key, preset_key, mode="endless")
        await start_game.finish(text)

    # ·「生成」：生成一个新世界（事件在建档、开局时才织，不在这一步）
    if head in _GEN_WORDS:
        _START_PENDING.pop(key, None)
        await matcher.send(
            "🎲 开始准备这一局（需要一些时间，完成后我会汇报）：\n"
            "① 生成世界地图　② 建立存档、等人加入\n"
            "开局时会按队伍的实际水平织出第一章。\n"
            "期间可以先做别的，不必等在这里。"
        )
        _spawn(
            asyncio.create_task(_generate_world(get_bot(), event, key, user_id, rest))
        )
        return

    # · 序号 / 名称：选世界
    if head:
        target = clean_name(head)
        if target.isdigit():
            index = int(target)
            if not (1 <= index <= len(worlds)):
                _set_pending_pick(key, user_id, "world")
                await start_game.finish(
                    _world_chooser(worlds, note=f"没有第 {index} 个世界，请重新选择。")
                )
            world = worlds[index - 1]
        else:
            world = next(
                (w for w in worlds if target == w.title or target == w.key), None
            )
            if world is None:
                if not worlds:
                    # 世界库是空的：先装内置预设（一两秒），装完再匹配一次
                    worlds = await _ensure_worlds()
                    world = next(
                        (w for w in worlds if target == w.title or target == w.key),
                        None,
                    )
                if not worlds:
                    await start_game.finish(
                        "🌍 还没有任何世界，插件里也没有内置的预设世界。\n"
                        "发送 /跑团.开局 生成 [主题] 创建一个（不给主题就随机）。"
                    )
                if world is None:
                    _set_pending_pick(key, user_id, "world")
                    await start_game.finish(
                        _world_chooser(worlds, note=f"没有找到世界「{target}」。")
                    )

        # 选中世界：**先看这个世界有没有现成的事件可选**。
        # 有的话多问一轮（挑一件入场事件），没有就直接建档——
        # 老世界没有现成事件，多问一轮只会让人莫名其妙。
        mode = "endless" if rest.lower() in _ENDLESS_WORDS else ""
        story_key = ""
        if not mode:
            from .core.dnd.openings import opening_options

            options = opening_options(world.key)
            if options:
                if rest.isdigit():
                    # 一步到位：/跑团.开局 2 3（世界 2，事件 3）
                    index = int(rest)
                    if 1 <= index <= len(options):
                        story_key = options[index - 1].event_key
                if not story_key:
                    _set_pending_pick(key, user_id, "story", world.key)
                    await start_game.finish(
                        _story_chooser(world.key, note=f"已选世界「{world.title}」。")
                    )
                    return

        _START_PENDING.pop(key, None)
        await matcher.send(
            f"🌍 使用世界「{world.title}」，正在建立存档"
            "（之后等大家加入再 /跑团.开始 开局）……"
        )
        save_id, text = _open_save(
            user_id,
            key,
            world.key,
            mode=mode,
            story_key=story_key,
        )
        await start_game.finish(text)

    # · 裸 /跑团.开局：**还没有世界就先装内置预设**（零模型调用、一两秒就完），
    #   不再一上来就调模型现生成一个——现生成要一两分钟、还要花钱，而绝大多数
    #   人只是想直接开跑。想现生成的仍然可以 /跑团.开局 生成 [主题]。
    if not worlds:
        worlds = await _ensure_worlds()
        if not worlds:
            await start_game.finish(
                "🌍 还没有任何世界，插件里也没有内置的预设世界。\n"
                "发送 /跑团.开局 生成 [主题] 创建一个（不给主题就随机）。"
            )
    _set_pending_pick(key, user_id, "world")
    await start_game.finish(_world_chooser(worlds))


@start_round.handle()
async def _start_round(event: MessageEvent, bot: Bot):
    """开始：开局建好存档后，等玩家都加入，再由任意已加入的玩家发它以正式开局。"""
    from .core.agents.dnd import is_started, run_opening

    key = _chat_key(event)
    save = _bound_save(key)
    if save is None:
        await start_round.finish(
            "本会话还没有进行中的冒险。\n"
            "· /跑团.开局 开一局新的\n"
            "· /跑团.继续 回到暂停中的那一局"
        )
    if save.progress.get("ended"):
        await start_round.finish(
            f"存档 {save.save_id} 已经结档，不能再开局了。想开新的一局用 /跑团.开局。"
        )
    if save.progress.get("paused"):
        await start_round.finish(
            f"这一局还在暂停中（存档 {save.save_id}）："
            "先 /跑团.继续 回到这一局，再发 /跑团.开始。"
        )
    if save.progress.get("starting"):
        await start_round.finish("⏳ 正在开局，开场白马上就来……")
    if is_started(save):
        await start_round.finish(
            "这一局已经开局了，直接 @我 描述行动就行；想看进度用 /跑团.存档。"
        )
    if get_character(save, event.get_user_id()) is None:
        await start_round.finish(
            f"你还没加入这一局，先发 /跑团.加入 {save.save_id}"
            "（本群直接发 /跑团.加入 也行）。"
        )

    names = _save_player_names(save)
    save.progress["starting"] = True
    save_save(save)
    try:
        result = await run_opening(save_id=save.save_id, chat_id=key)
    except Exception as e:
        log.warning(f"开场白生成失败：{str_traceback(e)}")
        _clear_start_flag(save.save_id)
        await start_round.finish(
            f"⚠️ 开场白没生成出来：{e}\n存档与加入状态都还在，稍后再发 /跑团.开始 试试。"
        )

    narration = (result.narration or "").strip()
    if not narration:
        # 没拿到开场白（例如 DM 调用失败）：不标记「已开局」，让玩家可以重试
        _clear_start_flag(save.save_id)
        await start_round.finish(
            "⚠️ 开场白没有生成出来："
            + (result.message or "请稍后再发 /跑团.开始 试试。")
        )

    latest = load_save(save.save_id) or save
    latest.progress["started"] = True
    latest.progress.pop("starting", None)
    save_save(latest)

    # 世界背景手册：存进存档了，再发一份给玩家方便随时翻。
    # 必须在 finish() 之前发——finish 会抛异常终止，后面代码不会再跑。
    brief_note = await _send_world_brief(event, result, latest.save_id, bot)

    # 开场白同样做成一张叙述卡（顶部带时间 / 地点 / 事件）；渲染失败退回原文
    story = await _story_image(
        latest.save_id, narration, rulings=list(getattr(result, "rulings", []) or [])
    )
    head = "🎬 开局！本局玩家：" + ("、".join(names) or "（只有你）")
    if story is not None:
        msg: Message = Message(head + "\n\n") + story
    else:
        extra = f"\n\n{result.rulings_text}" if result.rulings_text else ""
        msg = Message(head + f"\n\n📖 {narration}" + extra)
    msg += Message(brief_note)
    await start_round.finish(msg)


async def _send_world_brief(
    event: MessageEvent, result, save_id: str, bot: Bot | None = None
) -> str:
    """把开局整理出的世界背景手册发给玩家。

    文件名带上存档 ID（``世界背景手册_<存档ID>.md``）：一个群可能同时有好几局，
    同名文件互相覆盖后玩家就分不清哪份是哪局的。

    发送按三级退级（能到哪一级算哪一级，全失败也不影响已经开好的局）：

    1. **文件消息段**（``ATRI.message.file_msg``）：把字节流拼成 OneBot 的
       ``file`` 段直接发，协议端（NapCat / Lagrange 等）会落成群文件；
    2. **上传 API**（``upload_group_file`` / ``upload_private_file``）：
       协议端不支持 ``file`` 段时改走官方上传接口；
    3. **直接贴正文**：群文件两条路都不通（协议端没开文件、机器人没权限）时，
       至少把手册内容当消息发出去，别让玩家两手空空。

    :param save_id: 本局存档 ID，用于拼文件名。
    :param bot: 发送用的 Bot；留空则按当前会话取。
    :return: 给玩家的一句话说明；没生成或发不出去时返回空串（**不影响已经开好的局**）。
    """
    from ATRI.message import file_msg

    text = str(getattr(result, "world_brief_text", "") or "")
    path = getattr(result, "world_brief_path", None)
    if not text or path is None:
        return ""
    try:
        with open(path, "rb") as file:
            data = file.read()
    except OSError as e:
        log.warning(f"读世界背景手册失败：{path}（{str_traceback(e)}）")
        return ""
    name = f"世界背景手册_{_safe_file_name(save_id)}.md"
    if bot is None:
        bot = _session_bot()
    if bot is None:
        log.warning("世界背景手册发不出去：拿不到 Bot 对象")
        return ""

    # 1) 文件消息段
    try:
        await bot.send(event, file_msg(name, data))
        return f"\n\n📁 世界背景手册已发到群文件（{name}），随时可翻。"
    except Exception as e:
        log.warning(f"世界背景手册按文件段发送失败：{str_traceback(e)}")

    # 2) 上传 API
    if await _upload_brief_file(bot, event, name, data):
        return f"\n\n📁 世界背景手册已发到群文件（{name}），随时可翻。"

    # 3) 兜底：把正文当消息发出去
    try:
        for chunk in _split_text_for_msg(text):
            await bot.send(event, chunk)
    except Exception as e:
        log.warning(f"世界背景手册连正文都发不出去：{str_traceback(e)}")
        return ""
    return f"\n\n📖 世界背景手册发不成文件，已直接贴在下面（{name}）。"


def _session_bot() -> Bot | None:
    """取当前会话的 Bot；取不到就返回 None（不抛异常）。"""
    try:
        return get_bot()
    except Exception:
        return None


async def _upload_brief_file(
    bot: Bot, event: MessageEvent, name: str, data: bytes
) -> bool:
    """走协议端的上传接口把手册传上去。

    群聊用 ``upload_group_file``，私聊用 ``upload_private_file``；协议端没实现
    这些接口时会抛异常，这里一律吞掉返回 False（交给下一级兜底）。

    :return: 是否上传成功。
    """
    import base64

    payload = f"base64://{base64.b64encode(data).decode('utf-8')}"
    try:
        # 鸭子判断群/私聊：只有群事件才有 group_id
        group_id = getattr(event, "group_id", None)
        if group_id is not None:
            await bot.call_api(
                "upload_group_file",
                group_id=int(group_id),
                file=payload,
                name=name,
            )
        else:
            await bot.call_api(
                "upload_private_file",
                user_id=int(event.get_user_id()),
                file=payload,
                name=name,
            )
    except Exception as e:
        log.warning(f"世界背景手册按上传接口发送失败：{str_traceback(e)}")
        return False
    return True


def _split_text_for_msg(text: str, limit: int = 3000) -> list[str]:
    """把正文切成一条条能直接发出去的消息（按空行分段，过长再硬切）。"""
    body = str(text or "").strip()
    if not body:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in body.split("\n\n"):
        piece = paragraph.strip()
        if not piece:
            continue
        if size + len(piece) + 2 > limit and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        current.append(piece)
        size += len(piece) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _safe_file_name(text: str) -> str:
    """把存档 ID 收拾成能当文件名的一段（去掉路径分隔符等危险字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(text or "").strip())
    return cleaned.strip("._") or "save"


@resume_game.handle()
async def _resume_game(event: MessageEvent, args: Message = CommandArg()):
    """继续：把一局暂停中的冒险重新绑到本会话，接着玩。"""
    from .core.agents.dnd import is_started

    key = _chat_key(event)
    user_id = event.get_user_id()
    raw = args.extract_plain_text().strip()

    bound = _bound_save(key)

    if raw:
        target = raw.split()[0]
        save = load_save(target)
        if save is None:
            await resume_game.finish(f"没有找到存档 {target}。")
    else:
        save = bound if bound is not None else _paused_save(key)

    if save is None:
        await resume_game.finish(
            "没有找到可以继续的冒险。\n"
            "· /跑团.开局 开一局新的\n"
            "· /跑团.存档 看看现有存档，再用 /跑团.继续 <存档ID> 指定某一局"
        )
    if save.progress.get("ended"):
        await resume_game.finish(
            f"存档 {save.save_id} 已经结档，不能再继续了。想开新的一局用 /跑团.开局。"
        )
    if (
        bound is not None
        and bound.save_id == save.save_id
        and not save.progress.get("paused")
    ):
        if is_started(save):
            await resume_game.finish(
                f"本会话已经绑定了存档 {bound.save_id}，直接 @我 描述行动就能接着玩。"
            )
        await resume_game.finish(
            f"本会话已经绑定了存档 {bound.save_id}，这一局还在等人加入：\n"
            "· 大家 /跑团.加入 加入这一局\n"
            "· 人齐后由任意已加入的玩家发 /跑团.开始 开局"
        )

    # 换到别的存档：原来那一局按「暂停」处理，之后还能用 /跑团.继续 找回
    if (
        bound is not None
        and bound.save_id != save.save_id
        and not bound.progress.get("paused")
        and not bound.progress.get("ended")
    ):
        bound.progress["paused"] = True
        bound.progress["paused_at"] = time.time()
        bound.progress["chat_key"] = key
        save_save(bound)

    card = load_card(user_id)
    if card is None:
        await resume_game.finish("你还没有角色卡，先发送 /跑团.建卡 创建一张吧。")
    if get_character(save, user_id) is None:
        add_player(save, card)
        joined = True
    else:
        joined = False

    # 暂停时丢掉的本轮 / 战斗不复活：接着玩时重新起一轮，避免等不到人
    clear_round(save)
    clear_combat(save)
    save.progress.pop("paused", None)
    save.progress.pop("paused_at", None)
    save.progress["chat_key"] = key
    save_save(save)
    set_binding(key, save.save_id)

    lines = [
        f"▶️ 已回到存档 {save.save_id}（玩家 {len(save.player_ids)} 人）。",
        f"当前场景：{save.current_scene or '（未设置）'}",
    ]
    if joined:
        lines.append("（你的角色卡刚加入这一局。）")
    if save.progress.get("wrapping_up"):
        lines.append("（这一局已标记「准备结档」，DM 会继续把剧情推向收束。）")
    lines.append("")
    if is_started(save):
        lines.append(
            "直接 @我 描述角色的行动就能接着玩；DM 会在每段叙述结尾提示可以做什么。"
        )
    else:
        lines.append(
            "这一局还没开局：等大家 /跑团.加入 之后，"
            "由任意已加入的玩家发 /跑团.开始 开局。"
        )
    lines.append(format_report(save.save_id))
    await resume_game.finish("\n".join(lines))


@join_save.handle()
async def _join_save(event: MessageEvent, args: Message = CommandArg()):
    from .core.agents.dnd import is_started

    key = _chat_key(event)
    raw = args.extract_plain_text().strip()
    # 不带存档 ID 就加入本会话当前绑定的那一局（开局后大家直接 /跑团.加入 就行）
    save_id = (raw.split()[0] if raw else "") or get_binding(key)
    if not save_id:
        await join_save.finish(
            "本会话还没有进行中的冒险。\n"
            "· 用 /跑团.加入 <存档ID> 加入指定的某一局（/跑团.存档 可查看）\n"
            "· 或先 /跑团.开局 开一局"
        )

    save = load_save(save_id)
    if save is None:
        await join_save.finish(f"没有找到存档 {save_id}。")
    if save.progress.get("ended"):
        await join_save.finish(f"存档 {save.save_id} 已经结档，不能再加入了。")
    if save.progress.get("paused"):
        await join_save.finish(
            f"存档 {save.save_id} 正在暂停中：先 /跑团.继续 {save.save_id} 回到这一局。"
        )

    card = load_card(event.get_user_id())
    if card is None:
        await join_save.finish(
            f"你还没有角色卡，先发送 /跑团.建卡 创建一张，再发 /跑团.加入 {save.save_id}。"
        )

    started = is_started(save)
    me = player_name(event.get_user_id(), card.name)
    if started and get_character(save, event.get_user_id()) is not None:
        # 已经开局了就别再复制卡：那会把存档里的当前血量 / 状态覆盖回原卡
        set_binding(key, save.save_id)
        await join_save.finish(
            f"「{me}」已经在存档 {save.save_id} 里了，直接 @我 描述行动就行。"
        )

    add_player(save, card)
    set_binding(key, save.save_id)
    lines = [
        f"✅「{me}」加入了冒险（存档 {save.save_id}，当前 {len(save.player_ids)} 人："
        + "、".join(_save_player_names(save))
        + "）。"
    ]
    if started:
        lines.append("这一局已经开局了，直接 @我 描述行动即可。")
    else:
        lines.append(
            "等人齐了，由**任意已加入的玩家**发 /跑团.开始 正式开局"
            "（在那之前 @我 不会开场）。"
        )
    await join_save.finish("\n".join(lines))


@show_save.handle()
async def _show_save(event: MessageEvent, args: Message = CommandArg()):
    from .core.agents.dnd import is_started

    raw = args.extract_plain_text().strip()
    save_id = raw or get_binding(_chat_key(event))

    if not save_id:
        saves = list_saves()
        if not saves:
            await show_save.finish("还没有任何存档。发送 /跑团.开局 开启一局吧。")
        lines = ["现有存档："]
        for save in saves[:10]:
            scene = save.current_scene or "未设置"
            lines.append(
                f"· {save.save_id}（玩家 {len(save.player_ids)}，场景 {scene}）"
            )
        lines.append("用 /跑团.加入 <存档ID> 加入某一局。")
        await show_save.finish("\n".join(lines))

    save = load_save(save_id)
    if save is None:
        await show_save.finish(f"没有找到存档 {save_id}。")

    world = get_world(save.world_key)
    world_name = world.title if world else save.world_key
    event_line = event_summary(save, for_player=False).splitlines()
    event_no = ""
    if event_line:
        event_no = event_line[0].split("】")[-1].strip()

    started = is_started(save)
    names = _save_player_names(save)
    lines = [
        f"存档 {save.save_id}",
        f"玩家 {len(save.player_ids)} 人" + (f"：{'、'.join(names)}" if names else ""),
        f"世界：{world_name}    事件：{event_no or '（未开第一章）'}    "
        f"当前场景：{save.current_scene or ('（还没开局）' if not started else '（未设置）')}",
        "",
    ]
    if save.progress.get("ended"):
        lines.append("🏁 这一局已经结档。想开新的一局用 /跑团.开局。")
        lines.append("")
    elif not started and not save.progress.get("paused"):
        lines.append("⏳ 正在等人加入：/跑团.加入（人齐后发 /跑团.开始 才开局）")
        lines.append("")
    elif save.progress.get("wrapping_up"):
        lines.append("🏁 已收到结档要求：DM 会在接下来的叙述里推进结局。")
        lines.append("")
    elif save.progress.get("paused"):
        lines.append("⏸️ 这一局已暂停（存档保留）：/跑团.继续 回到这一局。")
        lines.append("")
    combat = load_combat(save)
    if combat is not None:
        lines.append(combat_text(save, combat, with_map=False))
        lines.append("")
    if list_npcs(save):
        lines.append(npcs_text(save))
        lines.append("")
    volumes = story_files(save.save_id)
    if volumes:
        lines.append(
            "复盘：saves/"
            + save.save_id
            + "/"
            + "、".join(path.name for path in volumes)
        )
        lines.append("")
    card = get_character(save, event.get_user_id())
    if card is None:
        lines.append(f"（你还没加入这局，用 /跑团.加入 {save.save_id} 加入）")
    if report(save.save_id):
        lines.append("")
        lines.append(format_report(save.save_id))
    msg = Message("\n".join(lines))
    if card is not None:
        # 角色卡单独渲染成图片，和文字存档报告拼在一起发；图片底部附常用指令
        seg = await _card_image(card)
        msg += seg
        if getattr(seg, "type", "") == "image":
            msg += Message("\n\n" + _quick_terms_footer("card"))
    await show_save.finish(msg)


@finish_game.handle()
async def _finish_game(event: MessageEvent):
    """结档：把本局标记为「准备结档」，由 DM 在剧情里写出结局。"""
    from .core.agents.dnd import is_started

    key = _chat_key(event)
    save_id = get_binding(key)
    if not save_id:
        await finish_game.finish("本会话没有进行中的冒险。先发送 /跑团.开局 开一局吧。")

    save = load_save(save_id)
    if save is None:
        await finish_game.finish("这局冒险的存档不见了，发送 /跑团.开局 重新开启。")
    if save.progress.get("ended"):
        await finish_game.finish("这一局已经结档了。想开新的一局用 /跑团.开局。")
    if not is_started(save):
        await finish_game.finish(
            f"这一局还没开局（存档 {save.save_id}）：先等大家 /跑团.加入，"
            "再由任意已加入的玩家发 /跑团.开始。"
        )
    if save.progress.get("wrapping_up"):
        await finish_game.finish(
            "已经收到你的结档要求了：DM 会在接下来的叙述里把剧情推向收束。\n"
            "（想先歇一歇可以直接 /跑团.结束 暂停；存档与复盘都会保留。）"
        )

    save.progress["wrapping_up"] = True
    save_save(save)
    await finish_game.finish(
        "🏁 已把本局标记为「准备结档」。\n"
        "DM 会在接下来的叙述里收拢伏笔、交代各人结局，并在合适的时候为这一局收官；\n"
        "收官后本会话会自动解除绑定，**存档与复盘都会保留**。\n"
        "（想先歇一歇可以直接 /跑团.结束 暂停，之后用 /跑团.继续 接着玩。）"
    )


@end_game.handle()
async def _end_game(event: MessageEvent, args: Message = CommandArg()):
    """结束：暂停本会话的冒险并给出用量汇报；加「删除」则连存档一起删掉。"""
    key = _chat_key(event)
    save_id = get_binding(key)
    raw = args.extract_plain_text().strip()

    if not save_id:
        paused = _paused_save(key)
        if paused is not None:
            await end_game.finish(
                f"本会话的冒险已经暂停了（存档 {paused.save_id}，进度都还在）。\n"
                "· /跑团.继续 接着玩\n"
                f"· 想彻底删除：/跑团.继续 {paused.save_id} 之后 /跑团.结束 删除"
            )
        await end_game.finish("本会话没有进行中的冒险。发送 /跑团.开局 开一局吧。")

    save = load_save(save_id)
    clear_binding(key)
    if save is not None:
        clear_round(save)  # 丢掉没凑齐的本轮，免得以后重新绑定时又等旧名单
        clear_combat(save)  # 战斗也不再继续

    if raw in {"删除", "delete", "--delete"}:
        delete_save(save_id)
        await end_game.finish(f"已结束冒险并删除存档 {save_id}。")

    if save is None:
        await end_game.finish(f"存档 {save_id} 已经不在了，已解除本会话的绑定。")

    save.progress["paused"] = True
    save.progress["paused_at"] = time.time()
    save.progress["chat_key"] = key  # 旧存档补记归属，方便之后 /跑团.继续 找回
    save_save(save)
    await end_game.finish(
        f"⏸️ 已暂停本会话的冒险（存档 {save_id} 保留，进度不会丢）。\n"
        f"{format_report(save_id)}\n\n"
        "· /跑团.继续 回到这一局接着玩\n"
        "· /跑团.结档 让 DM 先把这一局的结局写出来\n"
        f"· /跑团.结束 删除 彻底删除存档 {save_id}"
    )


# --------------------------------------------------------------------------- #
# DM 主持：@ 机器人描述行动
# --------------------------------------------------------------------------- #


def _is_dm_action(event: MessageEvent) -> bool:
    """DM 回合的触发条件：已绑定冒险，且是私聊或 @ 机器人，且不是指令。"""
    if not get_binding(_chat_key(event)):
        return False

    is_private = str(getattr(event, "message_type", "")) == "private"
    try:
        to_me = bool(event.is_tome())
    except Exception:
        to_me = False
    if not (is_private or to_me):
        return False

    text = (event.get_plaintext() or "").strip()
    starts = tuple(get_driver().config.command_start or {"/"})
    return bool(text) and not (starts and text.startswith(starts))


#: DM 主持：把玩家的行动描述接住并攒成本轮，所有玩家都提交后交给 DM（在建卡续接之后运行）
dm_action = plugin.on_message(
    name="行动",
    docs="向 DM 描述角色行动（@ 机器人）",
    rule=is_in_service("跑团") & Rule(_is_dm_action),
    priority=90,
    block=True,
)


@dm_action.handle()
async def _dm_action(event: MessageEvent):
    from .core.agents.dnd import submit_action

    key = _chat_key(event)
    save_id = get_binding(key)
    result = await submit_action(
        save_id=save_id,
        user_id=event.get_user_id(),
        action=event.get_plaintext().strip(),
        chat_id=key,
    )

    # 只是提示（还没轮到你 / 请描述行动）：一句话回完就走，不拼前缀也不附图
    if result.notice_only:
        await dm_action.finish(result.text)

    actor_hint = _combat_actor_hint(save_id)
    in_combat = bool(actor_hint)

    # DM 的叙述做成一张卡（顶部带此刻的时间 / 地点 / 事件）。渲染不出来时
    # 退回原来的纯文本，内容一个字都不少——所以卡是加分项，不是唯一出路。
    story = None
    if result.narration:
        story = await _story_image(
            save_id, result.narration, rulings=list(result.rulings)
        )

    msg: Message = Message(f"{actor_hint}\n\n" if actor_hint else "")
    msg += story if story is not None else Message(result.text)

    if result.ended:
        msg += Message(
            "\n\n🏁 本局就此结档（存档与复盘都保留）。"
            "想开新的一局用 /跑团.开局；想回味这一局可以翻一下存档目录里的 story.md。\n"
            + format_report(save_id)
        )

    # 战斗中：每轮附战场图 + 当前玩家面板图片（常用名词附在最后）
    if in_combat:
        images = await _combat_images(save_id)
        if images:
            for seg in images:
                msg += seg
            msg += Message("\n\n" + _combat_quick_footer(save_id))
    await dm_action.finish(msg)
