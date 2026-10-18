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

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ...dnd.billing import bind_save, record_result
from ...dnd.combat import (
    advance_turn,
    all_enemies_out,
    clear_combat,
    combat_text,
    current_actor,
    is_out,
    load_combat,
    peek_following,
    save_combat,
    use_resource,
)
from ...dnd.judge import dnd_judge
from ...dnd.npcs import list_npcs, npcs_text
from ...dnd.save_store import (
    append_scene_change,
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
)
from ...dnd.world_store import load_doc, read_doc
from ...trpg.rule import Verdict
from ..base import AgentError
from ..thinking import AgentContext
from .build_scene import scene_builder_agent
from .dm import dm_agent
from .on_demand import ensure_scene

__all__ = [
    "TurnResult",
    "build_scene_for_save",
    "open_scene",
    "resolve_players",
    "run_dm_round",
    "run_opening",
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

    @property
    def text(self) -> str:
        """实际要发给玩家的正文。"""
        return self.narration or self.message

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
    doc = read_doc("scene", key)
    return doc.content if doc is not None else ""


def _party_text(save) -> str:
    """本局角色的摘要，供构建器把场景里的 NPC / 线索挂到具体角色上。"""
    lines: list[str] = []
    for user_id in save.player_ids:
        card = get_character(save, user_id)
        if card is not None:
            lines.append(card.summary())
    return "\n\n".join(lines)


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


def _ending_text(save) -> str:
    """本轮给 DM 的结档状态提示。"""
    if save.progress.get("wrapping_up"):
        return (
            "玩家已经决定为本次跑团结档：请顺势把剧情推向收束——该收的伏笔收拢、"
            "该告别的人告别，并在这一轮或最近几轮的叙述里给出一个像样的结局；"
            "然后调用 wrap_up 结束本局（存档与复盘都会保留）。"
        )
    return (
        "玩家还没有决定结档：**不要**自行结束本局，wrap_up 会被系统打回。"
        "若有玩家流露出收尾的意思，提醒他/她可以发送 /跑团.结档 来正式收尾即可。"
    )


async def build_scene_for_save(save, generic: str) -> str | None:
    """用场景构建器把**通用场景**改造成**本局专属场景**；失败返回 ``None``。"""
    try:
        with bind_save(save.save_id):
            result = await scene_builder_agent.run(
                scene=generic,
                story=load_doc("story", "main") or "",
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
    resolved = await ensure_scene(location, kind=kind, region=region)
    if resolved is None:
        return None
    _kind, key, generic = resolved

    if not has_scene(save, key):
        built = await build_scene_for_save(save, generic)
        copy_scene(save, key, built if built is not None else generic)
        if built is None:
            log.warning(f"场景「{key}」构建失败，已退回通用场景")

    old = (save.current_scene or "").strip()
    if old and old != key:
        append_scene_change(save, old, "玩家离开该场景", kind="离开")

    save.current_scene = key
    append_scene_change(save, key, "玩家进入该场景", kind="进入")
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
    """玩家在提示词 / 文案里的显示名：优先角色名，退回 user_id。"""
    card = get_character(save, user_id)
    if card is not None and card.name:
        return card.name
    return str(user_id)


#: DM 写「等待全员」时可用的词
_ALL_WORDS = {"全部", "所有", "所有人", "全员", "全体", "大家", "all", "*"}
#: DM 写「不等任何人」时可用的词（谁先提交就立刻主持）
_NONE_WORDS = {"无", "没有", "不等", "谁都可以", "任何人", "none", "0"}
#: 分隔名单里的多个玩家
_SPLIT_RE = re.compile(r"[、,，;；/\s]+")
#: 行动文本里的移动类关键词（战斗里用来记「本回合移动过」，只做软提示）
_MOVE_RE = re.compile(r"(移动|跑|走|冲|跳|爬|潜行|躲藏|后退|上前|靠近|绕到|窜|扑过去)")


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


async def submit_action(
    *,
    save_id: str,
    user_id: str,
    action: str,
    chat_id: str = "",
    history_limit: int = 10,
) -> TurnResult:
    """接住一名玩家的行动：规则把关 → 存进本轮 → 名单上的人都提交了交给 DM。

    - 规则判断不通过：直接打回 / 追问，**不**保存；
    - 通过：存进本轮；名单上还有人没提交就只回一句等待，玩家可再次 @ 覆盖自己的行动；
    - 名单（由 DM 决定，默认全员）都提交后：一次性交给 DM 统一主持。
    - **战斗中**：名单由先攻顺序决定，只有当前回合的玩家能提交，提交即主持。
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
    if not text:
        return TurnResult(Verdict.NEED_INFO, message="请描述你想让角色做什么。")

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
    if combat is not None:
        # 战斗中：等待名单由先攻顺序决定，只有当前回合的玩家能行动。
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
    elif state.waiting is None:
        # DM 还没指定过等待名单：开局默认等全员（倒地 / 阵亡的除外）
        state.waiting = _filter_waiting(save, list(save.player_ids))

    state.actions[str(user_id)] = {
        "action": text,
        "resolution": resolution,
        "note": downed_note,
    }
    save_round(save, state)

    waiting = state.pending_ids()
    if waiting:
        names = "、".join(_player_label(save, uid) for uid in waiting)
        prefix = f"⚠️ {card.name} 现在{downed_note}。\n" if downed_note else ""
        return TurnResult(
            Verdict.ACCEPT,
            message=(
                prefix + f"✅ 已记下你的行动（{_player_label(save, user_id)}）。\n"
                f"还在等：{names}。\n"
                "DM 会在名单上的玩家都提交后统一主持；在那之前你可以再 @我 修改自己的行动。"
            ),
            waiting_for=waiting,
        )

    # 名单上的人都提交了：清空本轮行动（保留名单），再交给 DM。
    actions = state.actions
    state.actions = {}
    save_round(save, state)
    return await run_dm_round(
        save_id=save.save_id,
        actions=actions,
        chat_id=chat_id,
        history_limit=history_limit,
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
) -> TurnResult:
    """把本轮的玩家行动一起交给 DM 主持、落事件日志，并记下 DM 决定的下一轮等待名单。

    :param actions: ``{user_id: {"action": ..., "resolution": ...}}``。
    :param opening: ``True`` 时为开场轮：没有玩家行动，DM 直接给出开场白。
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
    party: list[str] = []
    resolved_any = False
    for user_id in order:
        item = actions.get(user_id) or {}
        action = str(item.get("action", "") or "").strip()
        if not action and not opening:
            continue
        resolution = str(item.get("resolution", "") or "").strip()
        note = str(item.get("note", "") or "").strip()
        card = get_character(save, user_id)
        if card is not None:
            party.append(card.summary())
        if opening:
            # 开场轮没有玩家行动，DM 直接主持。
            continue
        head = f"### {_player_label(save, user_id)}"
        if note:
            head += f"（⚠️ {note}）"
        entry = f"{head}\n{action}"
        if resolution:
            entry += f"\n（规则判断器已结算：{resolution}）"
            resolved_any = True
        blocks.append(entry)

        log_event(save, "action", action, actor=str(user_id))
        if resolution:
            log_event(save, "check", resolution, actor=str(user_id))

    if opening:
        action_text = _OPENING_PROMPT
    else:
        if not blocks:
            return TurnResult(Verdict.NEED_INFO, message="这一轮还没有收到任何玩家的行动。")

        action_text = "\n\n".join(blocks)
        if len(blocks) > 1:
            action_text = "本轮有多名玩家同时行动，请一并处理：\n\n" + action_text

    scene = scene_text(save)
    history = events_text(save, history_limit, scene=save.current_scene or None)
    # 本局出场过的具名 NPC：带着旧伤与旧账，供 DM 复用而不是重新造人。
    roster = npcs_text(save) if list_npcs(save) else ""

    # 战斗中：把先攻顺序、当前回合与随后的敌人回合一并告诉 DM。
    combat = load_combat(save)
    combat_label = (combat.note or "").strip() if combat is not None else ""
    scene_before = (save.current_scene or "").strip()
    combat_block = ""
    if combat is not None:
        following, next_player = peek_following(save, combat)
        combat_block = combat_text(save, combat)
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
    try:
        with bind_save(save.save_id):
            result = await dm_agent.run(
                context=context,
                action=action_text,
                character="\n\n".join(party),
                scene=scene,
                npcs=roster,
                ending=_ending_text(save),
                combat=combat_block,
                history=history,
            )
            record_result(result)
    except AgentError as e:
        log.warning(f"DM 主持失败：{str_traceback(e)}")
        return TurnResult(
            Verdict.ACCEPT, message=f"⚠️ DM 一时语塞：{e}\n请稍后再 @我 试试。"
        )

    narration = (result.content or "").strip() or "……（DM 沉默了片刻）"
    log_event(save, "narration", narration, actor="dm")

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
    elif combat is not None:
        # 敌人全部退场：自动结束战斗，等待名单交回 DM（没声明就默认全员）
        clear_combat(save)
        log_event(save, "combat", "战斗结束：敌人全部退场", actor="dm")
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
            narration=narration,
            transition=transition,
        )
    except OSError as e:  # 复盘写失败不该影响这一轮的叙述
        log.warning(f"写复盘失败：{str_traceback(e)}")

    # —— 结档：只有玩家先 /跑团.结档（DM 用 wrap_up 宣告收尾）才会走到这里 ——
    ended = bool(context.data.get("wrap_up"))
    if ended:
        clear_combat(save)
        clear_round(save)  # 丢掉没凑齐的本轮，重开时不会又等旧名单
        save.progress["ended"] = True
        save_save(save)
        if chat_id:
            clear_binding(str(chat_id))
        try:
            append_story_ending(save)
        except OSError as e:
            log.warning(f"写复盘结档线失败：{str_traceback(e)}")

    return TurnResult(
        Verdict.ACCEPT, narration=narration, resolved=resolved_any, ended=ended
    )


async def run_opening(*, save_id: str, chat_id: str = "") -> TurnResult:
    """开局后的开场轮：不等玩家行动，直接由 DM 给出开场白并把玩家带进第一个场景。"""
    save = load_save(save_id)
    if save is None:
        return TurnResult(
            Verdict.NEED_INFO,
            message="这局冒险的存档不见了，发送 /跑团.开局 重新开启。",
        )
    actions = {str(uid): {"action": "", "resolution": ""} for uid in save.player_ids}
    return await run_dm_round(
        save_id=save.save_id, actions=actions, chat_id=chat_id, opening=True
    )
