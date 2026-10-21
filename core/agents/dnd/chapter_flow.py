"""章节线编排：把「世界 + 存档」变成「下一章可以打的分支」。

这一层刻意放在 :mod:`...agents.dnd` 而不是 :mod:`...dnd.chapter`，因为它要**读存档**
（角色等级、事件日志、世界库），而纯逻辑层不碰存档——那条纪律让
:mod:`...dnd.chapter` 与 :mod:`...dnd.encounter_budget` 全是可直接断言的纯函数。

分工照旧是「程序算得出来的绝不交给模型」：

1. :func:`chapter_plan` 算难度——队伍等级、表现系数、目标 CR、本章经验预算；
2. :func:`offer_branches` 织候选走向——纯程序（骨架去重、元素配额、地点候选），不调模型；
3. :func:`weave_chapter` 在**玩家选定走向之后**才调 :class:`~.gen_chapter.ChapterAgent`
   写正文，并用 :func:`~...dnd.chapter.validate_card` 校验；不合格就带问题重织，
   **不做静默修补**；
4. :func:`advance_chapter` 结束当前章、结算表现系数、织下一章。

为什么「进入时才织」：章节卡生成器能看到的输入（已完成章节、已知线索、队伍水平、
上一章余波）只有在玩家真的把上一章玩完之后才存在。开局就织的卡只能对着世界地图空想，
那正是旧「故事推力」文档的毛病。
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ...dnd.chapter import (
    ARC_LABELS,
    ChapterBranch,
    ChapterCard,
    ChapterState,
    Recipe,
    chapter_summary,
    complete_chapter,
    current_card,
    next_chapter_no,
    set_branches,
    start_chapter,
    take_branch,
    validate_card,
    weave_branches,
)
from ...dnd.chapter import (
    advance_chapter as _advance,
)
from ...dnd.encounter_budget import (
    Performance,
    chapter_cr_text,
    chapter_xp_budget,
    performance_factor,
    summarize_performance,
)
from ...dnd.save_store import Save, recent_events
from ...dnd.threat import ThreatProfile, profile_for
from ...dnd.world_bible import load_bible
from ...dnd.world_store import MAIN_DOC_KEY, get_world_map, list_docs, load_doc
from ..base import UsageEntry
from .gen_chapter import ChapterAgent, chapter_agent

__all__ = [
    "ChapterPlan",
    "WeaveOutcome",
    "advance_chapter",
    "branch_choices_text",
    "chapter_plan",
    "ensure_opening_chapter",
    "offer_branches",
    "sync_world",
    "weave_chapter",
    "world_place_names",
]

#: 进度回调
ProgressCallback = Callable[[str], "Awaitable[None] | None"]

#: 参与难度自适应的事件条数上限。取最近 200 条：够覆盖两三次战斗的
#: 回合与倒地记录，又不会把整局日志都读进来（存档跑久了事件文件会很长）。
_PERF_EVENT_LIMIT = 200

#: 织卡失败时的重试次数。校验器是「拒绝」而不是「修补」，
#: 所以不给它一次机会的话模型会稳定地写出同一种错；两次足够暴露真问题。
_WEAVE_RETRIES = 2


# --------------------------------------------------------------------------- #
# 计划：程序算难度
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ChapterPlan:
    """下一章的难度计划（全是程序算出来的，不含任何模型产物）。"""

    chapter_no: int = 1
    level: int = 1
    party_size: int = 4
    #: 表现系数原始统计与折算结果
    perf: Performance = field(default_factory=Performance)
    factor: float = 0.0
    #: 目标 CR 的文本写法（含威胁层级提示）与本章经验预算
    target_cr: str = ""
    xp_budget: int = 0
    difficulty: str = "medium"
    #: 织法（从存档的 ``_recipe`` 读，读不到用默认）
    recipe: Recipe = field(default_factory=Recipe)
    #: 威胁剖面（见 :mod:`~...dnd.threat`）：这一章叠加了哪些后期加压档位
    threat: ThreatProfile = field(default_factory=ThreatProfile)
    #: 世界演化轨（见 :mod:`~...dnd.evolution`）：世界自己往前走带来的 CR 加成
    pressure: int = 0

    @property
    def adapts(self) -> bool:
        return bool(self.recipe.adapt)

    @property
    def threat_tags(self) -> list[str]:
        return self.threat.tags


def sync_world(save: Save, chapter: int) -> int:
    """把世界演化到第 ``chapter`` 章，返回本档的世界压力加成。

    **幂等**（见 :func:`~...dnd.evolution.advance_world`），所以 ``chapter_plan``
    与 ``advance_chapter`` 两处都调它也没关系——这正是把它做成幂等的原因。
    读世界圣经失败时按「没有势力」处理，不该拦住算难度。
    """
    from ...dnd.evolution import (
        advance_world,
        load_evolution,
        pressure_bonus,
        store_evolution,
    )

    state = load_evolution(save)
    entities: list[object] = []
    try:
        entities = load_bible(save.world_key).entities
    except (OSError, ValueError) as e:
        log.warning(f"读世界圣经失败（世界演化轨按无势力处理）：{str_traceback(e)}")
    advance_world(state, chapter, entities=entities)
    store_evolution(save, state)
    return pressure_bonus(state)


def _perf_of(save: Save, *, enabled: bool = True, difficulty: str = "medium") -> tuple[Performance, float]:
    """从事件日志算表现统计与系数。

    第一次读世界时事件文件可能还不存在，``recent_events`` 会返回空列表——
    那是正常的（新档没有任何历史），系数为 0 表示「按基准难度来」。
    """
    try:
        events = recent_events(save, limit=_PERF_EVENT_LIMIT)
    except OSError as e:  # 事件文件读不到不该拦住开局
        log.warning(f"读事件日志失败，表现系数按 0 处理：{e}")
        events = []
    perf = summarize_performance(events)
    return perf, performance_factor(perf, difficulty=difficulty, enabled=enabled)


def chapter_plan(
    save: Save,
    *,
    recipe: Recipe | None = None,
    difficulty: str = "medium",
) -> ChapterPlan:
    """算下一章的难度计划。

    难度是**三轨**的（见 :func:`~...dnd.encounter_budget.chapter_cr`）：
    队伍平均等级打底、章节序号爬升、表现系数修正。20 级之后另有威胁层级，
    走规则层而不动数值；世界演化轨（:mod:`~...dnd.evolution`）则把势力位置
    的变化换成一点 CR。
    """
    from ...dnd.combat import party_level

    rec = recipe or Recipe.from_dict((save.progress or {}).get("_recipe") or {})
    perf, factor = _perf_of(save, enabled=rec.adapt, difficulty=difficulty)

    level = party_level(save)
    size = max(1, len(save.player_ids) or 4)
    chapter_no = next_chapter_no(save)
    points = rec.story_points
    pressure = sync_world(save, chapter_no)

    return ChapterPlan(
        chapter_no=chapter_no,
        level=level,
        party_size=size,
        perf=perf,
        factor=factor,
        target_cr=chapter_cr_text(
            level=level,
            chapter=chapter_no,
            party_size=size,
            difficulty=difficulty,
            perf_factor=factor if rec.adapt else 0.0,
            world_pressure=pressure,
        ),
        xp_budget=chapter_xp_budget(
            level, points, party_size=size, difficulty=difficulty
        ),
        difficulty=difficulty,
        recipe=rec,
        threat=profile_for(
            chapter=chapter_no,
            level=level,
            party_size=size,
        ),
        pressure=pressure,
    )


def _state_of(save: Save) -> ChapterState:
    """取存档里的章节状态（薄封装，避免上层直接摸 ``progress``）。"""
    from ...dnd.chapter import _load

    return _load(save)


# --------------------------------------------------------------------------- #
# 世界里可用的地点（章节只能引用，不能新增专有名词）
# --------------------------------------------------------------------------- #


def world_place_names(world_key: str) -> list[str]:
    """列出这个世界里章节可以引用的地名。

    **顺序即优先级**，而优先级按世界层级来定：

    1. 场景（已经有场景的地方 = 走过的地方，最该再用一次）；
    2. 地点（据点 / 野外地点 / 地下城）——**一章真正发生的地方**；
    3. 区域（省）——可以作为「这一章在这一带」的说法；
    4. 王国（地区）——**排在最后**：一章落在一个王国上是空话，
       落在某个据点或某处地下城才有画面。

    这里曾经把「地区」排在最前，于是章节最容易引用「白港」这种量级的名字，
    织出来的目标也就只能说「弄清楚白港出了什么事」——层级错了，叙述自然就虚。
    """
    names: list[str] = []

    def add(value: object) -> None:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)

    try:
        docs = list_docs(world_key)
    except (OSError, ValueError):
        docs = {}
    for kind in ("scene", "city", "place", "dungeon", "province"):
        for name in docs.get(kind, []):
            add(name)

    # 圣经里登记过、但还没有文档的地名（省文档点过名、还没人去过的地方）
    try:
        bible = load_bible(world_key)
    except (OSError, ValueError):
        bible = None
    if bible is not None:
        for entity in bible.entities:
            if entity.kind == "place":
                add(entity.name)

    # 王国放最后
    try:
        regions, _adjacency = get_world_map(world_key)
    except (OSError, ValueError):
        regions = []
    for region in regions:
        add(region)
    return names


# --------------------------------------------------------------------------- #
# 织分支（纯程序）
# --------------------------------------------------------------------------- #


def offer_branches(
    save: Save,
    *,
    recipe: Recipe | None = None,
    difficulty: str = "medium",
    branch_count: int = 3,
    rng: random.Random | None = None,
) -> list[ChapterBranch]:
    """织出下一章的候选走向，写进存档并返回。

    这一步**一次模型都不调**：骨架、钩子地点、去重窗口、元素配额全是程序决定的，
    所以便宜、可复现（传 ``rng``）而且不会写出越界的地名。
    模型只在玩家选定之后才写正文（见 :func:`weave_chapter`）。
    """
    plan = chapter_plan(save, recipe=recipe, difficulty=difficulty)
    state = _state_of(save)
    branches = weave_branches(
        chapter=plan.chapter_no,
        world_names=world_place_names(save.world_key),
        used_elements=state.used_elements,
        recent_arcs=state.recent_arcs,
        recipe=plan.recipe,
        rng=rng or random.Random(),
        branch_count=branch_count,
    )
    set_branches(save, branches)
    return branches


def branch_choices_text(save: Save) -> str:
    """待选分支给 DM 看的文本（分支只有走向，还没写正文）。"""
    state = _state_of(save)
    if not state.branches:
        return ""
    lines = [f"【第 {next_chapter_no(save)} 章】接下来可以走的方向："]
    for index, branch in enumerate(state.branches, start=1):
        arc = ARC_LABELS.get(branch.arc, branch.arc)
        lines.append(f"{index}. {arc}（{ '、'.join(branch.locations) or '地点待定' }）")
        if branch.parallel:
            lines.append("　（并行支线：与主线目标不完全一致，做了会改变后面的局势）")
    lines.append("选定之后 DM 才能把这一章写细。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 织正文（选定走向之后才调模型）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class WeaveOutcome:
    """一次织卡的结果。"""

    card: ChapterCard | None = None
    #: 最后一轮校验的问题（空 = 通过）
    problems: list[str] = field(default_factory=list)
    #: 重试次数（0 = 一次就过）
    attempts: int = 0
    #: 本次开章跨过的纪元结算（没跨边界时为 ``None``）
    epoch: object | None = None
    usages: list[UsageEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.card is not None

    def summary(self) -> str:
        if self.ok:
            card = self.card
            assert card is not None
            done = "；".join(self.problems) if self.problems else ""
            tail = f"（{done}）" if done else ""
            head = f"第 {card.title or '未命名'}章已就绪{tail}"
            if self.epoch is not None:
                head = f"{head}\n{self.epoch.summary()}"
            return head
        return "这一章暂时织不出来：" + "；".join(self.problems or ["未知原因"])


def _agent_inputs(
    save: Save,
    branch: ChapterBranch,
    plan: ChapterPlan,
    *,
    carry: str = "",
) -> dict[str, object]:
    """拼给 :class:`~.gen_chapter.ChapterAgent` 的输入。

    :param carry: 上一章的 ``fail_forward``（余波）。**由调用方在结束上一章之前
        取好传进来**—— :func:`~...dnd.chapter.complete_chapter` 会把当前卡清空，
        事后再读就只剩空字符串了。
    """
    state = _state_of(save)
    world_text = load_doc(save.world_key, "world", MAIN_DOC_KEY) or ""

    context: list[str] = []
    if state.history:
        context.append("已完成章节：" + "、".join(state.history[-5:]))
    if state.clues:
        context.append("已知线索：" + "、".join(state.clues[-6:]))
    context.append(f"队伍 {plan.party_size} 人，平均 {plan.level} 级")
    if plan.perf.combats:
        context.append(
            f"上一章打了 {plan.perf.combats} 场"
            f"（平均 {plan.perf.rounds_avg:.1f} 回合，倒地 {plan.perf.knockdowns} 次，"
            f"长休 {plan.perf.long_rests} 次）"
        )
    if carry:
        context.append("上一章没收尾的事：" + carry)

    difficulty = f"目标 CR {plan.target_cr}；本章经验预算 {plan.xp_budget} XP"
    return {
        "world": world_text,
        "arc": ARC_LABELS.get(branch.arc, branch.arc)
        + ("（并行支线，与主线目标冲突）" if branch.parallel else ""),
        "locations": "、".join(branch.locations) or "（无候选，随便挑一个世界里已有的地名）",
        "difficulty": difficulty,
        "context": "\n".join(context),
    }


async def weave_chapter(
    save: Save,
    ref: str,
    *,
    recipe: Recipe | None = None,
    difficulty: str = "medium",
    agent: ChapterAgent = chapter_agent,
    on_progress: ProgressCallback | None = None,
) -> WeaveOutcome:
    """选定一个走向并把它织成完整的章节卡，落库并挂进存档。

    :param ref: 分支序号（1 起）、分支 id 或标题，见 :func:`~...dnd.chapter.take_branch`。
    :returns: :class:`WeaveOutcome`；``card is None`` 表示织不出来
        （模型连续给了非法输出，或分支引用不存在）。
    """
    from .epoch_flow import close_chapter, settle_epoch

    outcome = WeaveOutcome()
    branch_card = take_branch(save, ref)
    if branch_card is None:
        outcome.problems = [f"没有这个分支：{ref}"]
        return outcome

    # 上一章的余波要在结束它**之前**取：complete_chapter 会把当前卡清空，
    # 事后再读就只剩空字符串了（这条上下文之前恒为空，就是这个顺序问题）。
    state = _state_of(save)
    carry = state.card.fail_forward
    # 开新章 = 先把旧章收掉。这是 completed 唯一的生产写入点，也是
    # 难度序号轨 / 威胁层级 / 世界演化 / 纪元结算能推进的前提。
    closed = close_chapter(save, difficulty=difficulty)
    # 章数跨过纪元边界就在这里结算——**不是**织下一章时，
    # 否则第 4 章的结算会晚到第 5 章才发生。
    epoch = await settle_epoch(save, difficulty=difficulty, on_progress=on_progress)
    if epoch.settled:
        outcome.epoch = epoch
        outcome.usages.extend(epoch.usages)
    if closed is not None:
        log.debug(f"已结束上一章：{closed.title or closed.id}")

    plan = chapter_plan(save, recipe=recipe, difficulty=difficulty)
    world_names = world_place_names(save.world_key)
    state = _state_of(save)
    inputs = _agent_inputs(save, _branch_of(branch_card), plan, carry=carry)

    card = branch_card
    problems: list[str] = []
    for attempt in range(_WEAVE_RETRIES + 1):
        outcome.attempts = attempt + 1
        await _notify(on_progress, f"正在织第 {plan.chapter_no} 章（{attempt + 1} 次尝试）……")
        try:
            result = await agent.run(**inputs)
        except Exception as e:  # 单章失败不该炸掉整轮
            log.warning(f"章节卡生成失败：{str_traceback(e)}")
            problems = [f"模型调用失败：{e}"]
            continue
        outcome.usages.extend(getattr(result, "usages", None) or [])

        candidate = result.card
        problems = list(result.problems)
        if candidate is not None:
            # 分支的程序决定项由系统说了算，模型只补它该写的字段
            candidate.id = card.id
            candidate.arc = card.arc
            candidate.parallel = card.parallel
            if not candidate.locations:
                candidate.locations = list(card.locations)
            candidate.locations = [
                name for name in candidate.locations if name in set(world_names)
            ] or list(card.locations)
            candidate.story_points = plan.recipe.story_points
            problems.extend(
                validate_card(
                    candidate,
                    world_names=world_names,
                    quota=plan.recipe.element_quota,
                    used_elements=state.used_elements,
                )
            )
            # fail_forward 是唯一允许补的字段，补完就不算问题了
            if not candidate.fail_forward.strip():
                continue
            blocking = [p for p in problems if "fail_forward" not in p]
            if not blocking:
                problems = blocking
                card = candidate
                break
        # 把问题原样带回给下一次：模型得知道上一版哪里不合法才会改
        inputs["context"] = (
            str(inputs.get("context") or "")
            + "\n\n上一次输出被退回，请修正这些问题：\n- "
            + "\n- ".join(problems)
        )

    outcome.problems = problems
    if problems and card is branch_card:
        # 重试全都没通过；仍然落卡（分支信息至少是程序算对的合法走向），
        # 但把它记成「只有走向没有正文」，让 DM 知道要自己补。
        log.warning(f"章节卡未通过校验，仅保留程序走向：{problems}")
        start_chapter(
            save,
            card,
            target_cr=plan.target_cr,
            xp_budget=plan.xp_budget,
            world_key=save.world_key,
        )
        outcome.card = card
        return outcome

    outcome.card = start_chapter(
        save,
        card,
        target_cr=plan.target_cr,
        xp_budget=plan.xp_budget,
        world_key=save.world_key,
    )
    await _notify(on_progress, f"第 {plan.chapter_no} 章已就绪：{card.title}")
    return outcome


def _branch_of(card: ChapterCard) -> ChapterBranch:
    """把 :func:`take_branch` 产出的卡还原成分支（生成器的输入形状）。"""
    return ChapterBranch(
        id=card.id,
        title=card.title,
        arc=card.arc,
        goal=card.goal,
        locations=list(card.locations),
        parallel=card.parallel,
    )


# --------------------------------------------------------------------------- #
# 开局与推进
# --------------------------------------------------------------------------- #


async def ensure_opening_chapter(
    save: Save,
    *,
    recipe: Recipe | None = None,
    difficulty: str = "medium",
    branch_count: int = 3,
    agent: ChapterAgent = chapter_agent,
    on_progress: ProgressCallback | None = None,
) -> WeaveOutcome:
    """开局：织出候选走向，让玩家（或 DM）选一个。

    **不自动选**。开局的第一个决定应该交给玩家——那也是「章节分支」这件事
    存在的意义。已经有当前章时直接返回那张卡，不重复织。
    """
    existing = current_card(save)
    if existing is not None:
        return WeaveOutcome(card=existing)

    offer_branches(save, recipe=recipe, difficulty=difficulty, branch_count=branch_count)
    await _notify(on_progress, branch_choices_text(save))
    return WeaveOutcome()


def advance_chapter(
    save: Save,
    *,
    recipe: Recipe | None = None,
    difficulty: str = "medium",
    card: ChapterCard | None = None,
    note: str = "",
    write_to_world: bool = True,
) -> ChapterCard | None:
    """结束当前章并把下一章落卡（模型正文已备好时用这个）。

    :param card: 下一章的卡；留空则只结束当前章、不开新章。
    :returns: 新的当前章卡；``card`` 为空时返回 ``None``。

    **不在这里做纪元结算**：这一函数是同步的，而结算要调模型命名称号。
    纪元结算统一在 :func:`~.weave_chapter` 里跟着「开新章」一起发生，
    所以走本函数开章时要自己补一次 :func:`~.epoch_flow.settle_epoch`。
    """
    # 难度要在 complete_chapter 之前算：那一步会把已完成章数推进，
    # 之后 chapter_plan 拿到的就是新一章的章号，预算也随之重算。
    plan = chapter_plan(save, recipe=recipe, difficulty=difficulty)
    if card is None:
        complete_chapter(save, factor=plan.factor, note=note)
        return None
    return _advance(
        save,
        card,
        factor=plan.factor,
        note=note,
        target_cr=plan.target_cr,
        xp_budget=plan.xp_budget,
        world_key=save.world_key if write_to_world else "",
    )


def current_plan_text(save: Save) -> str:
    """给 DM 看的一行：现在第几章、难度多少、还剩多少进度。"""
    card = current_card(save)
    if card is None:
        return ""
    lines = [chapter_summary(save, for_player=False)]
    done, total, _objs = _progress_of(save)
    if total:
        lines.append(f"进度：{done}/{total} 条完成条件已勾")
    return "\n".join(lines)


def _progress_of(save: Save) -> tuple[int, int, list]:
    from ...dnd.chapter import objective_progress

    return objective_progress(save)


async def _notify(on_progress: ProgressCallback | None, message: str) -> None:
    """调用进度回调；回调出错只记日志，不影响织章。"""
    if on_progress is None:
        return
    try:
        result = on_progress(message)
        if hasattr(result, "__await__"):
            await result
    except Exception as e:
        log.warning(f"章节进度回调失败：{str_traceback(e)}")
