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

from nonebot import get_bot, get_driver
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.internal.matcher import Matcher
from nonebot.params import ArgPlainText, CommandArg
from nonebot.rule import Rule

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.service import is_in_service

from . import plugin
from .core.dnd import (
    ABILITY_MAP,
    SKILL_MAP,
    CreationError,
    CreationSession,
    CreationStep,
    DndCharacter,
    add_player,
    clear_binding,
    clear_combat,
    clear_round,
    combat_text,
    create_save,
    creation_prompt,
    delete_save,
    delete_session,
    get_binding,
    get_character,
    get_skill,
    get_world_map,
    list_npcs,
    list_saves,
    list_sessions,
    load_card,
    load_combat,
    load_save,
    load_session,
    npcs_text,
    parse_item_stack,
    parse_skill_request,
    purge_expired,
    save_card,
    save_save,
    save_session,
    set_binding,
    story_files,
)
from .core.trpg import DiceError, roll
from .matchers import (
    create_card,
    edit_card,
    end_game,
    finish_game,
    join_save,
    make_check,
    roll_dice,
    show_card,
    show_save,
    start_game,
)

#: 改卡（重做建卡某一步）会话在 ``matcher.state`` 里的键
_EDIT_SESSION_KEY = "trpg_edit_session"

#: 建卡过程中可用来中途放弃的关键词
_CANCEL_WORDS = {"取消", "退出", "cancel", "算了", "不建了", "不建了。"}

#: 一次骰子指令里多个表达式的分隔符
_DICE_SEP = re.compile(r"[,，;；、]+")


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
        delete_session(user_id)
        _unmark_active(user_id)
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
            save_card(character)
        except Exception as e:  # 落盘失败不应让玩家以为建好了
            log.warning(f"保存角色卡失败：{str_traceback(e)}")
            return f"⚠️ 保存角色卡失败：{e}", None

        delete_session(session.user_id)
        return (
            f"{character.summary()}\n\n🎲 角色卡已保存！"
            "发送 /跑团.看卡 查看，/跑团.检定 察觉 DC15 开始检定。",
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
    await show_card.finish(card.summary())


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

    parsed = parse_skill_request(raw)
    if parsed is None:
        await make_check.finish(
            "没看懂这个检定。示例：「察觉」「力量检定」「力量豁免 DC15」「隐匿 劣势」。"
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

    await make_check.finish(result.describe())


# --------------------------------------------------------------------------- #
# 改卡
# --------------------------------------------------------------------------- #

#: 可重做的建卡步骤：步骤 -> 触发词（第一个为展示名）
_REDO_STEPS: tuple[tuple[CreationStep, tuple[str, ...]], ...] = (
    (CreationStep.ABILITY, ("属性", "ability", "roll", "掷属性")),
    (CreationStep.RACE, ("种族", "race")),
    (CreationStep.PROFESSION, ("职业", "profession", "class")),
    (CreationStep.BACKGROUND, ("背景", "background")),
    (CreationStep.SKILL, ("技能", "skill")),
    (CreationStep.EQUIPMENT, ("装备", "装备包", "equipment")),
    (CreationStep.SPELLS, ("法术", "法术表", "spell", "spells")),
)

#: 触发词 -> 步骤
_STEP_LOOKUP: dict[str, CreationStep] = {
    word.lower(): step for step, words in _REDO_STEPS for word in words
}

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
    """把已保存的角色卡还原成建卡会话，供「重做某一步」使用。

    生成角色卡时，背景提供的技能 / 装备已经合并进卡里；这里再拆出来，
    这样重做背景时不会残留旧背景的内容（``build_character`` 会重新合并）。
    """
    session = CreationSession(user_id=card.user_id)
    session.system = "DND 5e"
    session.name = card.name
    session.base_scores = dict(card.base_scores)
    session.ability_pool = sorted(card.base_scores.values(), reverse=True)
    session.race = card.race
    session.subrace = card.subrace
    session.profession = card.profession
    session.background = card.background

    background = card.background_obj
    bg_skills = set(background.skills) if background else set()
    bg_items = (
        {str(parse_item_stack(item)) for item in background.equipment}
        if background
        else set()
    )
    session.skills = [k for k in card.skill_proficiencies if k not in bg_skills]
    session.equipment = [i for i in card.equipment if i not in bg_items]
    session.equipment_package = card.equipment_package
    session.spells = list(card.spells)
    return session


def _reset_dependents(session: CreationSession, step: CreationStep) -> list[str]:
    """重做某步后清理与它冲突的下游选择，返回给玩家的提示。"""
    if step is not CreationStep.PROFESSION:
        return []
    profession = session.profession_obj
    if not profession:
        return []

    notes: list[str] = []
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

    notes = _reset_dependents(session, step)
    try:
        character = session.build()
    except CreationError as e:
        return None, f"⚠️ {e}\n\n{creation_prompt(session.step, session)}"

    message = f"✅ 已重做「{step.value}」。"
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
            await edit_card.send(message)
            return
        await _save_or_finish(new_card)
        await edit_card.finish(f"{message}\n\n{new_card.summary()}")

    await edit_card.send(f"重做「{step.value}」：\n\n{creation_prompt(step, session)}")


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
    await edit_card.finish(f"{message}\n\n{new_card.summary()}")


# --------------------------------------------------------------------------- #
# 冒险：开局 / 加入 / 存档 / 结束
# --------------------------------------------------------------------------- #
#
# 一局冒险 = 一份存档（``core.dnd.save_store``）。本会话（群 / 私聊）与存档的绑定
# 记在 ``saves/bindings.json``；绑定之后，玩家 @ 机器人描述行动就进入 DM 主持。
#
# 场景切换不在玩家指令里：完全由 DM 通过 ``move_scene`` 决定，并由 DM 叙述离场 / 入场。

#: 生成世界时进度汇报的最小间隔（秒），避免刷屏
_PROGRESS_INTERVAL = 4.0

#: 后台任务（生成世界等），持有引用防止被垃圾回收
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _chat_key(event: MessageEvent) -> str:
    """本会话的绑定键：群聊按群，私聊按人。"""
    group_id = getattr(event, "group_id", None)
    return f"group_{group_id}" if group_id else f"user_{event.get_user_id()}"


def _split_theme(raw: str) -> tuple[str, bool]:
    """解析「开局」参数：返回 ``(主题, 是否强制重生成世界)``。"""
    force = False
    kept: list[str] = []
    for part in re.split(r"\s+", raw.strip()):
        if not part:
            continue
        if part in {"重生成", "regenerate", "--force"}:
            force = True
            continue
        kept.append(part)
    return " ".join(kept), force


def _open_save(user_id: str, key: str) -> tuple[str, str]:
    """建立存档、复制角色卡并绑定本会话，返回 ``(存档 ID, 给玩家的文案)``。"""
    card = load_card(user_id)
    save = create_save(player_cards=[card] if card is not None else [])
    set_binding(key, save.save_id)

    text = (
        f"🎲 冒险已就绪：存档 {save.save_id}\n"
        "世界地图与故事已生成；\n"
        "区域 / 城镇 / 自然地点 / 地下城 / 场景会在**用到时按需生成**。\n\n"
        "直接 @我 描述角色的行动即可开始（例如「我环顾四周」「我想去最近的城镇」）；\n"
        "去往何处、何时离场入场都由 DM 决定，并由 DM 叙述相应的剧情。\n"
        "· /跑团.加入 <存档ID> 让同群的朋友加入\n"
        "· /跑团.存档 查看当前存档与角色状态"
    )
    return save.save_id, text


async def _attach_opening(save_id: str, key: str, text: str) -> str:
    """开局建好存档后立刻让 DM 给出开场白；开场失败不影响存档。"""
    from .core.agents.dnd import run_opening

    fallback = "\n\n（开场白没生成出来：@我 说出第一个行动即可继续。）"
    try:
        result = await run_opening(save_id=save_id, chat_id=key)
    except Exception as e:
        log.warning(f"生成开场白失败：{str_traceback(e)}")
        return text + fallback

    narration = (result.narration or "").strip()
    if not narration:
        log.warning(f"开场白为空：{result.message}")
        return text + fallback
    return f"{text}\n\n📖 开场白：\n{narration}"


async def _generate_world(
    bot, event: MessageEvent, key: str, user_id: str, theme: str
) -> None:
    """后台生成世界（入场流程），完成后建立存档并汇报。"""
    from .core.agents.dnd import EntryOptions, run_entry_flow

    last = [0.0]

    async def progress(message: str) -> None:
        now = time.monotonic()
        if now - last[0] < _PROGRESS_INTERVAL:
            return
        last[0] = now
        await bot.send(event, message)

    try:
        report = await run_entry_flow(
            options=EntryOptions(theme=theme), on_progress=progress
        )
    except Exception as e:
        log.warning(f"生成世界失败：{str_traceback(e)}")
        await bot.send(event, f"⚠️ 世界生成失败：{e}")
        return

    if not report.ok:
        # 世界生成失败：立刻停止，不建存档，避免开出一局空冒险。
        await bot.send(
            event,
            "⚠️ 世界生成失败，已中止开局（未创建存档）。\n"
            + report.summary()
            + "\n请检查模型 / 工作配置后重新发送 /跑团.开局。",
        )
        return

    save_id, text = _open_save(user_id, key)
    await bot.send(
        event, f"{report.summary()}\n\n" + await _attach_opening(save_id, key, text)
    )


@start_game.handle()
async def _start_game(
    event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
):
    key = _chat_key(event)
    user_id = event.get_user_id()

    if load_card(user_id) is None:
        await start_game.finish("你还没有角色卡，先发送 /跑团.建卡 创建一张吧。")

    bound = get_binding(key)
    if bound and load_save(bound) is not None:
        await start_game.finish(
            f"本会话已在进行冒险（存档 {bound}）。\n"
            "要继续就 @我 描述行动；想重开请先 /跑团.结束 删除。"
        )

    theme, force = _split_theme(args.extract_plain_text())
    regions, _adjacency = get_world_map()
    if regions and not force:
        save_id, text = _open_save(user_id, key)
        await start_game.finish(await _attach_opening(save_id, key, text))

    await matcher.send(
        "开始生成世界……（需要一些时间，完成后我会汇报；期间可以先做别的）"
    )
    task = asyncio.create_task(_generate_world(get_bot(), event, key, user_id, theme))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


@join_save.handle()
async def _join_save(event: MessageEvent, args: Message = CommandArg()):
    key = _chat_key(event)
    raw = args.extract_plain_text().strip()
    save_id = raw.split()[0] if raw else ""
    if not save_id:
        await join_save.finish(
            "用法：/跑团.加入 <存档ID>（用 /跑团.存档 查看现有存档）"
        )

    save = load_save(save_id)
    if save is None:
        await join_save.finish(f"没有找到存档 {save_id}。")

    card = load_card(event.get_user_id())
    if card is None:
        await join_save.finish("你还没有角色卡，先发送 /跑团.建卡 创建一张吧。")

    add_player(save, card)
    set_binding(key, save.save_id)
    await join_save.finish(
        f"已加入存档 {save.save_id}，本会话的冒险已绑定到它"
        f"（当前 {len(save.player_ids)} 名玩家）。\n直接 @我 描述行动即可。"
    )


@show_save.handle()
async def _show_save(event: MessageEvent, args: Message = CommandArg()):
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

    lines = [
        f"存档 {save.save_id}",
        f"玩家 {len(save.player_ids)} 人",
        f"世界：{save.world_key}    当前场景：{save.current_scene or '（未设置）'}",
        "",
    ]
    if save.progress.get("ended"):
        lines.append("🏁 这一局已经结档。想开新的一局用 /跑团.开局。")
        lines.append("")
    elif save.progress.get("wrapping_up"):
        lines.append("🏁 已收到结档要求：DM 会在接下来的叙述里推进结局。")
        lines.append("")
    combat = load_combat(save)
    if combat is not None:
        lines.append(combat_text(save, combat))
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
    else:
        lines.append(card.summary())
    await show_save.finish("\n".join(lines))


@finish_game.handle()
async def _finish_game(event: MessageEvent):
    """结档：把本局标记为「准备结档」，由 DM 在剧情里写出结局。"""
    key = _chat_key(event)
    save_id = get_binding(key)
    if not save_id:
        await finish_game.finish("本会话没有进行中的冒险。先发送 /跑团.开局 开一局吧。")

    save = load_save(save_id)
    if save is None:
        await finish_game.finish("这局冒险的存档不见了，发送 /跑团.开局 重新开启。")
    if save.progress.get("ended"):
        await finish_game.finish("这一局已经结档了。想开新的一局用 /跑团.开局。")
    if save.progress.get("wrapping_up"):
        await finish_game.finish(
            "已经收到你的结档要求了：DM 会在接下来的叙述里把剧情推向收束。\n"
            "（想立刻停手可以直接 /跑团.结束；存档与复盘都会保留。）"
        )

    save.progress["wrapping_up"] = True
    save_save(save)
    await finish_game.finish(
        "🏁 已把本局标记为「准备结档」。\n"
        "DM 会在接下来的叙述里收拢伏笔、交代各人结局，并在合适的时候为这一局收官；\n"
        "收官后本会话会自动解除绑定，**存档与复盘都会保留**。\n"
        "（想立刻停手可以直接 /跑团.结束。）"
    )


@end_game.handle()
async def _end_game(event: MessageEvent, args: Message = CommandArg()):
    key = _chat_key(event)
    save_id = get_binding(key)
    if not save_id:
        await end_game.finish("本会话没有进行中的冒险。")

    clear_binding(key)
    ended = load_save(save_id)
    if ended is not None:
        clear_round(ended)  # 丢掉没凑齐的本轮，免得以后重新绑定时又等旧名单
        clear_combat(ended)  # 战斗也不再继续
    if args.extract_plain_text().strip() in {"删除", "delete", "--delete"}:
        delete_save(save_id)
        await end_game.finish(f"已结束冒险并删除存档 {save_id}。")
    await end_game.finish(
        f"已结束本会话的冒险（存档 {save_id} 仍保留）。想重开用 /跑团.开局。"
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
    result = await submit_action(
        save_id=get_binding(key),
        user_id=event.get_user_id(),
        action=event.get_plaintext().strip(),
        chat_id=key,
    )
    text = result.text
    if result.ended:
        text += (
            "\n\n🏁 本局就此结档（存档与复盘都保留）。"
            "想开新的一局用 /跑团.开局；想回味这一局可以翻一下存档目录里的 story.md。"
        )
    await dm_action.finish(text)
