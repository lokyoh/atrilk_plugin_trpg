"""章节卡：把「故事推力」换成「一章一章生成的模组」。

这一模块取代原来的 story（故事推力）。差别在于三点：

- **故事是推力**：一份静态文档，DM 从里面读「世界在往哪走」。
  章节是**任务**：一张卡片，写明这一章玩家要干什么、做完算完成。
- **章节是一张张织出来的**：不是开局一次写好八章，而是玩家走完一章才织下一张。
  原因是输入信息量——预写时生成器看不到玩家已经干了什么，
  而那恰恰是最该影响下一章的东西。
- **章节属于本局**：章节卡存在 :attr:`Save.progress` 里，**不写进世界库**。
  世界库是世界的样子，章节卡是这一局的样子；写进去就等于让这一局的剧情污染世界骨架。

**职责划分**（与 :mod:`.encounter_budget` 同一纪律）：程序算得出来的绝不交给模型。
程序负责抽骨架、分配元素配额、算难度、兜底失败前进；
模型只负责写「什么地方、什么人、什么场面」这些不可计算的部分。

三层混合分支（:func:`weave_branches`）：

- **主脊**：每章一个骨架（调查 / 潜入 / 守点 / 仪式 / 对峙 / 交涉）；
- **章内多解**：每张卡带一组可勾选的完成条件，客观条件由程序数，描述性目标由 DM 报；
- **并行线**：:attr:`Recipe.parallel_every` 指定每隔几章放一条与主脊冲突的支线，
  玩家可以中途切过去。

**章节卡存在哪**：织出来就落进世界库的 ``chapter`` 文档（:func:`save_card`），
所以同一个世界下次开局可以复用、可以挑。存档只持**引用**（章节键）与**进度**
（:attr:`ChapterState`），不持正文——像游戏存档那样：存档文件小，改库里的卡不会
让已开的局凭空变形（因为局里用的是自己那张键的副本，见 :func:`snapshot_card`）。

新地点 / 新人物**允许沉淀回世界库**：跑一局下来世界会多出玩家去过的地方，
这是特性而不是污染。为了能收拾，沉淀物一律带 ``via_chapter`` 标记，
:func:`~.on_demand.on_demand` 与人工都能据此识别哪些是章节带进来的。

难度由 :func:`.encounter_budget.chapter_cr` 三轨合成（等级 / 章节序号 / 表现系数），
这一模块只负责把算出来的数字带进章节卡。
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ATRI.log import log as _log

__all__ = [
    "ARC_LABELS",
    "ARC_WEIGHTS",
    "ChapterBranch",
    "ChapterCard",
    "ChapterState",
    "Objective",
    "Recipe",
    "advance_chapter",
    "card_to_text",
    "chapter_summary",
    "complete_chapter",
    "current_card",
    "current_card_key",
    "deposit_entities",
    "load_card",
    "objective_progress",
    "save_card",
    "set_branches",
    "start_chapter",
    "take_branch",
    "text_to_card",
    "validate_card",
    "weave_branches",
]


#: 章节骨架。键是内部用的短名，值是给 DM 看的中文标签。
ARC_LABELS: dict[str, str] = {
    "investigation": "调查",
    "surveillance": "盯梢",
    "audit": "审计",
    "heist": "潜入",
    "infiltration": "渗透",
    "defense": "守点",
    "holdout": "据守",
    "ritual": "仪式",
    "decoding": "解读",
    "confrontation": "对峙",
    "ambush": "伏击",
    "social": "交涉",
    "bargain": "交易",
}

#: 默认骨架权重。试探性内容（调查/交涉）偏多，正面冲突偏少——
#: 因为 5e 模组真正的战斗体验来自遭遇设计，而不是把所有事都打成一场。
ARC_WEIGHTS: dict[str, float] = {
    "investigation": 0.26,
    "heist": 0.14,
    "defense": 0.14,
    "ritual": 0.10,
    "confrontation": 0.22,
    "social": 0.14,
}

#: 副骨架 -> 主骨架。**它们存在的首要理由是容量**：主骨架只有 6 种，
#: 而「避开最近 3 章 + 本章内三个分支互不相同」需要 6 种同时可用——
#: 骨架池正好等于窗口大小时，本章第3 个分支必然被迫重复，去重形同虚设。
#: 补上副骨架后池子变成 13 种，窗口才真正有余量。
ARC_ALIASES: dict[str, str] = {
    "surveillance": "investigation",
    "audit": "investigation",
    "infiltration": "heist",
    "holdout": "defense",
    "decoding": "ritual",
    "ambush": "confrontation",
    "bargain": "social",
}

#: 副骨架的权重按主骨架的这个比例折算（保证是「换个口味」而非「另一种游戏」）
_ALIAS_WEIGHT_RATIO = 0.5


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Objective:
    """一条完成条件。

    :param text: 条件描述（进玩家手册）
    :param kind: ``clue`` 靠收集线索自动判定、``item`` 靠持有物判定、
        ``defeat`` 靠击败目标判定、``narrative`` 由 DM 判定
    :param ref: 判定依据（线索 key / 物品名 / 目标名）
    """

    text: str
    kind: str = "narrative"
    ref: str = ""

    @property
    def auto(self) -> bool:
        """客观条件（程序可验），与需要 DM 判定的描述性目标相对。"""
        return self.kind in ("clue", "item", "defeat")

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "kind": self.kind, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: Any) -> Objective:
        if isinstance(data, str):
            return cls(text=data)
        if isinstance(data, dict):
            return cls(
                text=str(data.get("text") or "").strip(),
                kind=str(data.get("kind") or "narrative").strip() or "narrative",
                ref=str(data.get("ref") or "").strip(),
            )
        return cls(text=str(data or "").strip())


@dataclass(slots=True)
class ChapterCard:
    """一张章节卡。

    一张卡 = 一章。卡上不写「怎么打」，只写「这一章要什么、做完算完、
    没做完会怎样」——具体场面由 DM 临场发挥，这与提示词里既有的
    「每问题至少三种解法、不预设玩家选择」是一致的。
    """

    id: str = ""
    title: str = ""
    #: 骨架（:data:`ARC_LABELS` 的键）
    arc: str = "investigation"
    #: 这一章玩家要干什么，一句话
    goal: str = ""
    #: 玩家是怎么被卷进来的
    entry: str = ""
    #: 钩子地点（必须是世界里已有的地名）
    locations: list[str] = field(default_factory=list)
    #: 在场人物
    npcs: list[str] = field(default_factory=list)
    #: 完成条件
    objectives: list[Objective] = field(default_factory=list)
    #: 没做完会怎样（必填，有缺省模板兜底）
    fail_forward: str = ""
    #: 节奏锚：几个推进点（一个推进点 ≈ 30 分钟）
    story_points: int = 3
    #: 本章难度预算（经验量）
    xp_budget: int = 0
    #: 本章目标 CR（文本，含威胁层级提示）
    target_cr: str = ""
    #: 是否为并行支线
    parallel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "arc": self.arc,
            "goal": self.goal,
            "entry": self.entry,
            "locations": list(self.locations),
            "npcs": list(self.npcs),
            "objectives": [o.to_dict() for o in self.objectives],
            "fail_forward": self.fail_forward,
            "story_points": self.story_points,
            "xp_budget": self.xp_budget,
            "target_cr": self.target_cr,
            "parallel": self.parallel,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ChapterCard:
        d = data if isinstance(data, dict) else {}
        return cls(
            id=str(d.get("id") or ""),
            title=str(d.get("title") or ""),
            arc=str(d.get("arc") or "investigation"),
            goal=str(d.get("goal") or ""),
            entry=str(d.get("entry") or ""),
            locations=[str(x) for x in (d.get("locations") or [])],
            npcs=[str(x) for x in (d.get("npcs") or [])],
            objectives=[Objective.from_dict(x) for x in (d.get("objectives") or [])],
            fail_forward=str(d.get("fail_forward") or ""),
            story_points=int(d.get("story_points") or 3),
            xp_budget=int(d.get("xp_budget") or 0),
            target_cr=str(d.get("target_cr") or ""),
            parallel=bool(d.get("parallel")),
        )


@dataclass(slots=True)
class ChapterBranch:
    """一个待选的分支（玩家选了才会织成完整的卡）。

    织分支时**只给标题和钩子**，不织正文——所以玩家看到选项时，
    系统已经知道每个选项大概是什么，但还没决定里面具体发生什么。
    """

    id: str
    #: 标题留空——织分支时程序不编造（标题属于模型写的正文），
    #: 但要给默认值，否则每个构造点都得写一遍 title=""
    title: str = ""
    arc: str = "investigation"
    goal: str = ""
    locations: list[str] = field(default_factory=list)
    parallel: bool = False

    @property
    def arc_label(self) -> str:
        return ARC_LABELS.get(self.arc, self.arc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "arc": self.arc,
            "goal": self.goal,
            "locations": list(self.locations),
            "parallel": self.parallel,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ChapterBranch:
        d = data if isinstance(data, dict) else {}
        return cls(
            id=str(d.get("id") or ""),
            title=str(d.get("title") or ""),
            arc=str(d.get("arc") or "investigation"),
            goal=str(d.get("goal") or ""),
            locations=[str(x) for x in (d.get("locations") or [])],
            parallel=bool(d.get("parallel")),
        )


@dataclass(slots=True)
class ChapterState:
    """存档里的章节进度，落在 ``progress["_chapter"]``。

    以下划线开头，所以 :meth:`~.save_store.Save.to_dict` 之外的玩家侧文本
    过滤会挡住它，不会把内部状态原样发给玩家。
    """

    #: 已完成的章节序号（当前章 = 已完成数 + 1）
    completed: int = 0
    #: 当前章节卡
    card: ChapterCard = field(default_factory=ChapterCard)
    #: 待选分支
    branches: list[ChapterBranch] = field(default_factory=list)
    #: 已被选中、但还没落卡的章节骨架（见 :func:`take_branch`）
    pending: ChapterCard = field(default_factory=ChapterCard)
    #: 已勾上的条件（存 ``目标序号``）
    checked: list[int] = field(default_factory=list)
    #: 已收集的线索 key
    clues: list[str] = field(default_factory=list)
    #: 已用过的元素（地名 / 人物），复用从这里挑
    used_elements: list[str] = field(default_factory=list)
    #: 最近若干章的骨架，去重窗口
    recent_arcs: list[str] = field(default_factory=list)
    #: 上一章的表现系数，供DM 与手册展示
    last_factor: float = 0.0
    #: 每章收尾时记下的 ``[已勾数, 总数]``（顺序记，第 N 章在下标 N-1）。
    #: 纪元收束判定要「这个纪元的目标完成率」，跨章累加就得有历史，
    #: 否则只能看到最后一章的勾选情况。
    progresses: list[list[int]] = field(default_factory=list)
    #: 累计完成的章节标题（一行一个，进手册）
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "card": self.card.to_dict(),
            "branches": [b.to_dict() for b in self.branches],
            "pending": self.pending.to_dict(),
            "checked": list(self.checked),
            "clues": list(self.clues),
            "used_elements": list(self.used_elements),
            "recent_arcs": list(self.recent_arcs),
            "last_factor": self.last_factor,
            "progresses": [list(x) for x in self.progresses],
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: Any) -> ChapterState:
        d = data if isinstance(data, dict) else {}
        return cls(
            completed=int(d.get("completed") or 0),
            card=ChapterCard.from_dict(d.get("card") or {}),
            branches=[ChapterBranch.from_dict(b) for b in (d.get("branches") or [])],
            pending=ChapterCard.from_dict(d.get("pending") or {}),
            checked=[int(x) for x in (d.get("checked") or [])],
            clues=[str(x) for x in (d.get("clues") or [])],
            used_elements=[str(x) for x in (d.get("used_elements") or [])],
            recent_arcs=[str(x) for x in (d.get("recent_arcs") or [])],
            last_factor=float(d.get("last_factor") or 0.0),
            progresses=[
                [int(x[0]), int(x[1])]
                for x in (d.get("progresses") or [])
                if isinstance(x, (list, tuple)) and len(x) >= 2
            ],
            history=[str(x) for x in (d.get("history") or [])],
        )


# --------------------------------------------------------------------------- #
# 织法
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Recipe:
    """织法：章节怎么一张张织出来。全部是可调参数，不写死在代码里。"""

    #: 骨架权重（**已含副骨架**，由 :meth:`merged_weights` 展开）
    arc_weights: dict[str, float] = field(default_factory=dict)
    #: 最近几章用过的骨架不许再用
    no_repeat_window: int = 3
    #: 每章最多引入几个新元素（新地点 / 新人物）
    #: 织分支时**先给地点留一格**（:attr:`element_quota` 减一），
    #: 剩下的额度留给模型挑人物——否则地点把配额占满，
    #: 模型一加 NPC 就会超配额被校验打回。
    element_quota: int = 2
    #: 至少多大比例的元素来自已有内容
    reuse_ratio: float = 0.4
    #: 每隔几章放一条并行支线（0 = 不放）
    parallel_every: int = 6
    #: 一章几个推进点
    story_points: int = 3
    #: 是否启用表现系数自适应难度
    adapt: bool = True

    def merged_weights(self) -> dict[str, float]:
        """展开副骨架后的完整权重表（抽骨架时用这个）。

        副骨架默认继承主骨架的一半权重，所以「调查」抽到时，
        会以调查 / 盯梢 / 审计 ≈ 2 : 1 : 1 的比例落位——
        口味不变，但去重有余量。
        """
        base = dict(self.arc_weights) if self.arc_weights else dict(ARC_WEIGHTS)
        merged = dict(base)
        for alias, parent in ARC_ALIASES.items():
            if parent in base:
                merged[alias] = base[parent] * _ALIAS_WEIGHT_RATIO
        return {k: v for k, v in merged.items() if v > 0}

    def to_dict(self) -> dict[str, Any]:
        """序列化：写回 ``save.progress["_recipe"]`` 用。

        两件事必须做对：

        1. **写全部字段**，包括取默认值的那些——``from_dict`` 的缺省与 dataclass 的
           缺省并不完全一致（见那里的说明），少写一个字段就会让那一项静默变成另一个值；
        2. ``arc_weights`` 要写成**解析后的表**。空 dict 的含义是「用内置权重表」，
           但 ``from_dict`` 会把内置表读成实际值——不解析就永远往返不回去。
           存的是**主干骨架**表（不加副骨架），副骨架由 :meth:`merged_weights`
           在抽骨架时按比例展开，两处不该各存一份。
        """
        return {
            "arc_weights": dict(self.arc_weights) if self.arc_weights else dict(ARC_WEIGHTS),
            "no_repeat_window": self.no_repeat_window,
            "element_quota": self.element_quota,
            "reuse_ratio": self.reuse_ratio,
            "parallel_every": self.parallel_every,
            "story_points": self.story_points,
            "adapt": self.adapt,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Recipe:
        d = data if isinstance(data, dict) else {}
        weights = d.get("arc_weights")
        merged = dict(ARC_WEIGHTS)
        if isinstance(weights, dict):
            for key, value in weights.items():
                if key in ARC_LABELS:
                    try:
                        merged[key] = max(0.0, float(value))
                    except (TypeError, ValueError):
                        continue
        return cls(
            arc_weights=merged,
            no_repeat_window=max(0, int(d.get("no_repeat_window") or 3)),
            element_quota=max(0, int(d.get("element_quota", 2))),
            reuse_ratio=max(0.0, min(1.0, float(d.get("reuse_ratio", 0.4)))),
            # 缺省必须与 dataclass 的 ``parallel_every = 6`` 一致。原先写成
            # ``or 0``，于是「存档里没有这个键」被读成「不要并行支线」——
            # 而 ``_recipe`` 之前一个写入点都没有，等于并行支线从来没触发过。
            parallel_every=max(0, int(d.get("parallel_every", 6))),
            story_points=max(1, min(int(d.get("story_points") or 3), 12)),
            adapt=bool(d.get("adapt", True)),
        )


#: 失败前进的缺省模板。校验器发现 DM 没写就用它兜上并标记——
#: 这是唯一允许「补」而不是「拒绝」的字段，因为它必须有值。
_FAIL_FORWARD_TEMPLATE = (
    "这一章没做完的事不会消失：{where}的人会继续按自己的方式往下走，"
    "下一章从这里接手。"
)


def _fresh_card_id(world_key: str, chapter_no: int) -> str:
    """生成一个新的章节键（存档引用 + 世界库键共用同一个）。

    优先用世界库的 :func:`~.world_store.new_chapter_id` 以保证不与库里的卡冲突；
    拿不到存储层时退回到本地生成——这让 :mod:`.chapter` 在纯逻辑自检里也能跑。
    """
    try:
        from .world_store import new_chapter_id

        return new_chapter_id(world_key or "main", f"ch{max(1, int(chapter_no))}")
    except Exception:
        return f"ch{max(1, int(chapter_no))}_{uuid.uuid4().hex[:4]}"


# --------------------------------------------------------------------------- #
# 织分支
# --------------------------------------------------------------------------- #


def _pick_arc(
    weights: dict[str, float], banned: list[str], rng: random.Random
) -> str:
    """按权重抽一个骨架，避开 :paramref:`banned` 里的**全部**项。

    注意 banned 是**全量排除**而不是「最近 N 个」。同一次织章里要给三个分支挑骨架时，
    前两个已经挑过的必须一起排除，否则第 3 个分支会和第 1 个撞——
    玩家在同一章里看到两个同骨架的选项，等于没有选择。

    兜底是**分级放宽**：实在挑不出时从 banned 的最旧一项开始逐个放开，
    这样「实在没得选」和「正常去重」有区别，前者能在自检里被看见。
    """
    items = [b for b in banned if b]
    for relax in range(len(items), -1, -1):
        blocked = set(items[:relax])
        pool = {k: v for k, v in weights.items() if k not in blocked and v > 0}
        if pool:
            keys = list(pool)
            return rng.choices(keys, weights=[pool[k] for k in keys], k=1)[0]
    # 权重全为0（配置写坏了）：退回全表，保证不会返回 None
    usable = {k: v for k, v in weights.items() if v > 0} or dict(ARC_WEIGHTS)
    keys = list(usable)
    return rng.choices(keys, weights=[usable[k] for k in keys], k=1)[0]


def _split_pool(
    used: list[str], world_names: list[str], quota: int, reuse_ratio: float, rng: random.Random
) -> tuple[list[str], list[str]]:
    """按 :attr:`Recipe.element_quota` 与 :attr:`Recipe.reuse_ratio`
    把「这一章能用哪些地点」分成引入与复用两组。

    复用优先——后期章节大量使用**已有元素的新组合**：
    盐仓码头还能再打一次，但这次是涨潮、只有一条跳板、而仓正已经站在你这边了。
    同一个地点，不同局面。
    """
    reused = list(dict.fromkeys(used))
    fresh = [n for n in world_names if n not in set(reused)]
    rng.shuffle(reused)
    rng.shuffle(fresh)

    want_new = quota
    if reused and rng.random() < reuse_ratio:
        # 按比例把额度让给复用：新元素至少占 1 - reuse_ratio
        floor = max(1, int(round(quota * (1.0 - reuse_ratio))))
        want_new = min(want_new, floor)
    new_taken = fresh[: max(0, want_new)]
    reuse_taken = (reused + fresh[len(new_taken):])[: max(0, quota - len(new_taken))]
    return new_taken, reuse_taken


def weave_branches(
    *,
    chapter: int,
    world_names: list[str],
    used_elements: Iterable[str] = (),
    recent_arcs: Iterable[str] = (),
    recipe: Recipe | None = None,
    rng: random.Random | None = None,
    branch_count: int = 3,
) -> list[ChapterBranch]:
    """织出本章的候选分支（只给标题与钩子，正文等玩家选定后再织）。

    这一步**不调模型**，全是程序决策，所以便宜、可测、可复现
    （传入 ``rng`` 就能得到同一条序列）。

    :param chapter: 章节序号（从 1 起）
    :param world_names: 世界里已有的地点名，**只能从这里选**——
        章节卡不得往世界里新增专有名词，这是世界层与剧情解耦的纪律。
    :param used_elements: 已用过的元素（复用池）
    :param recent_arcs: 最近若干章的骨架（去重窗口）
    :return: 候选分支列表，长度不超过 ``branch_count``
    """
    rec = recipe or Recipe()
    rng = rng or random.Random()
    count = max(1, min(int(branch_count), 5))
    used = list(used_elements)
    recent = list(recent_arcs)
    names = [n for n in dict.fromkeys(world_names) if n]
    branches: list[ChapterBranch] = []
    weights = rec.merged_weights()

    # 主脊分支：避开最近若干章用过的骨架
    spine_arc = _pick_arc(weights, recent[-rec.no_repeat_window:], rng)
    # 本次已选的骨架：全局窗口 + 主脊。后面每个分支都要避开这一整串。
    used_arcs = list(recent[-rec.no_repeat_window:]) + [spine_arc]
    parallel_due = rec.parallel_every > 0 and chapter > 0 and chapter % rec.parallel_every == 0

    for index in range(count):
        is_parallel = parallel_due and index == count - 1
        if index == 0:
            arc = spine_arc
        else:
            arc = _pick_arc(weights, used_arcs, rng)
        used_arcs.append(arc)

        new_names, reuse_names = _split_pool(
            used, names, max(1, rec.element_quota - 1), rec.reuse_ratio, rng
        )
        picks = (new_names + reuse_names)[: max(1, rec.element_quota - 1)]
        branches.append(
            ChapterBranch(
                id=f"ch{chapter}b{index + 1}",
                title="",  # 标题交给模型；程序不编造地名专有名词
                arc=arc,
                goal="",
                locations=picks,
                parallel=is_parallel,
            )
        )
    return branches


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


def validate_card(
    card: ChapterCard,
    *,
    world_names: Iterable[str] = (),
    quota: int = 2,
    used_elements: Iterable[str] = (),
) -> list[str]:
    """校验一张章节卡，返回问题列表（空列表 = 通过）。

    **校验器的立场是拒绝而不是修补**：静默修补会教生成器「写错的反正会被改」，
    从而越来越随便；让不合法的输出直接被打回重做，分布才会落在合法区间里。
    唯一的例外是 :attr:`ChapterCard.fail_forward`——它有缺省模板，
    缺了会被补上并在这里报出来。
    """
    problems: list[str] = []
    known = set(world_names)
    if not card.title.strip():
        problems.append("章节卡缺标题")
    if not card.goal.strip():
        problems.append("章节卡缺目标（goal）")
    if card.arc not in ARC_LABELS:
        problems.append(f"未知骨架「{card.arc}」，可用：{'、'.join(ARC_LABELS)}")

    # 地点必须在世界里——章节只许引用，不许新增专有名词
    if known:
        for name in card.locations:
            if name not in known:
                problems.append(f"地点「{name}」不在世界库里；章节只能引用已有地名")

    # 元素配额：新出现的元素不能超过配额
    used = set(used_elements)
    fresh = [n for n in {*card.locations, *card.npcs} if n not in used]
    if quota >= 0 and len(fresh) > quota:
        problems.append(f"引入 {len(fresh)} 个新元素，超过配额 {quota}")

    if not card.fail_forward.strip():
        problems.append("缺 fail_forward（缺失时用缺省模板兜底）")

    if not card.objectives:
        problems.append("章节卡没有任何完成条件")
    return problems


def ensure_fail_forward(card: ChapterCard, *, where: str = "这件事") -> bool:
    """给缺 fail_forward 的卡补上缺省模板。

    :return: 是否补过（补过则说明模型偷懒了，值得在提示词上再敲一遍）
    """
    if card.fail_forward.strip():
        return False
    card.fail_forward = _FAIL_FORWARD_TEMPLATE.format(where=where)
    return True


# --------------------------------------------------------------------------- #
# 入库与读档
# --------------------------------------------------------------------------- #

#: 章节卡正文的机器可读块标记。世界库文档是 Markdown，
#: 机器可读部分统一放在末尾的 ``chapter-card`` 围栏里——
#: 这样人读得懂（正文就是给人看的）、机器也解析得动。
_CARD_FENCE = "chapter-card"


def card_to_text(card: ChapterCard) -> str:
    """把章节卡渲染成世界库文档正文：人读的前言 + 机器读的 JSON 块。

    前言给 DM 与 ``query_data`` 看（一眼能读完），JSON 块给
    :func:`text_to_card` 解析回来——复用时不必重新生成，读块即可。
    """
    import json

    lines = [f"# {card.title or '未命名章节'}", ""]
    if card.arc in ARC_LABELS:
        lines.append(f"骨架：{ARC_LABELS[card.arc]}" + ("（并行支线）" if card.parallel else ""))
    if card.goal:
        lines.append(f"目标：{card.goal}")
    if card.entry:
        lines.append(f"缘由：{card.entry}")
    if card.locations:
        lines.append(f"去处：{'、'.join(card.locations)}")
    if card.npcs:
        lines.append(f"人物：{'、'.join(card.npcs)}")
    if card.objectives:
        lines.append("完成条件：")
        for obj in card.objectives:
            lines.append(f"- {obj.text}")
    if card.fail_forward:
        lines.append(f"没做完：{card.fail_forward}")
    if card.target_cr:
        lines.append(f"难度：{card.target_cr}（本章预算 {card.xp_budget} XP）")
    lines += [
        "",
        f"```{_CARD_FENCE}",
        json.dumps(card.to_dict(), ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def text_to_card(text: str) -> ChapterCard | None:
    """从世界库文档正文解析出章节卡；解析不出来返回 ``None``。"""
    import json
    import re

    raw = str(text or "")
    if f"```{_CARD_FENCE}" not in raw:
        return None
    match = re.search(rf"```{_CARD_FENCE}\s*(\{{.*?\}})\s*```", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return ChapterCard.from_dict(data)


def save_card(world_key: str, card: ChapterCard, *, chapter_no: int = 0) -> str:
    """把章节卡写进世界库，返回文档键。

    键沿用卡自身的 id，所以**同一张卡重复保存是幂等的**——
    这正是「存档只持引用」能成立的前提。
    """
    from .world_store import new_chapter_id, save_doc

    key = card.id or new_chapter_id(world_key, f"ch{chapter_no or 1}")
    card.id = key
    meta = {
        "arc": card.arc,
        "chapter_no": chapter_no,
        "target_cr": card.target_cr,
        "locations": list(card.locations),
        "npcs": list(card.npcs),
        "parallel": card.parallel,
        "xp_budget": card.xp_budget,
    }
    save_doc(world_key, "chapter", key, card_to_text(card), title=card.title or key, meta=meta)
    return key


def load_card(world_key: str, key: str) -> ChapterCard | None:
    """按键读回章节卡。"""
    from .world_store import load_doc

    if not key:
        return None
    return text_to_card(load_doc(world_key, "chapter", key) or "")


def snapshot_card(card: ChapterCard) -> ChapterCard:
    """返回卡的一个副本。存档留一份只读副本，
    这样即便世界库那张卡后来被改写，这一局读到的仍是它自己织的那版。"""
    return ChapterCard.from_dict(card.to_dict())


def deposit_entities(world_key: str, card: ChapterCard, *, chapter_no: int = 0) -> int:
    """把这一章引入的新地点与人物**沉淀进世界圣经**，返回真正新增的条数。

    为什么允许沉淀：跑一局下来，世界应该多出玩家去过的地方——
    这是特性而不是污染。之前「世界层不带剧情」的纪律针对的是
    **开局就把某一局的剧情写进地区文档**，而不是「游玩后世界有所增长」。

    为了能收拾，登记时 ``source`` 一律写成 ``chapter:<卡id>``。
    将来想知道哪些专有名词是某一局带进来的，看 source 就知道。

    :param chapter_no: 只写进 note 方便人读；机器判断用 source。
    """
    from .world_bible import Entity, register_entities

    source = f"chapter:{card.id}"
    people = {str(n).strip() for n in card.npcs if str(n).strip()}
    items: list[Entity] = []
    seen: set[str] = set()
    for name in [*card.locations, *card.npcs]:
        name = str(name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(
            Entity(
                name=name,
                kind="person" if name in people else "place",
                note=f"第 {chapter_no or '?'} 章「{card.title or card.goal}」引入",
                source=source,
                public=True,
            )
        )
    if not items:
        return 0
    return int(register_entities(world_key, items, source=source) or 0)


# --------------------------------------------------------------------------- #
# 存档读写
# --------------------------------------------------------------------------- #


def _load(save: Any) -> ChapterState:
    raw = (getattr(save, "progress", None) or {}).get("_chapter")
    return ChapterState.from_dict(raw or {})


def _store(save: Any, state: ChapterState) -> None:
    progress = getattr(save, "progress", None)
    if progress is None:
        return
    progress["_chapter"] = state.to_dict()


def current_card(save: Any) -> ChapterCard | None:
    """当前章节卡；还没开始则为 ``None``。"""
    state = _load(save)
    return state.card if state.card.id else None


def current_card_key(save: Any) -> str:
    """当前章节卡在世界库里的键，也就是存档持有的那个引用。"""
    return _load(save).card.id


def next_chapter_no(save: Any) -> int:
    """**下一个要开的章**的序号（1 起）。

    刻意区别于 ``completed + 1``：当前那张卡还没结束时，玩家玩的是这一章，
    而 DM 在 ``offer_chapters`` 里看到的是**下一章**的候选走向。
    两处都用这个函数，候选走向的编号、章节摘要的标题、
    以及入库时的 ``chapter_no`` 才不会各说各话。
    """
    state = _load(save)
    return state.completed + 1 + (1 if state.card.id else 0)


def set_branches(save: Any, branches: Iterable[ChapterBranch]) -> int:
    """写入待选分支，返回数量。"""
    state = _load(save)
    state.branches = list(branches)
    _store(save, state)
    return len(state.branches)


def take_branch(save: Any, ref: str) -> ChapterCard | None:
    """按序号（1 起）或 id 或标题选中一个分支，返回据此生成的章节卡。

    这里**只填程序知道的部分**（骨架、钩子地点、难度预算）；
    标题、目标、人物、完成条件要由模型补齐后再调
    :func:`start_chapter` 落卡。

    **刻意不覆盖 :attr:`ChapterState.card`**：选中走向不等于新章开张，
    当前那张卡（含它的 ``fail_forward``）要一直留到编排层显式结束它为止——
    早先把骨架直接写进 ``card``，于是「上一章没收尾的事」这条上下文恒为空，
    而完成章数也永远停在 0（没人调 :func:`complete_chapter`）。
    选中的骨架暂存在 :attr:`ChapterState.pending`。
    """
    state = _load(save)
    if not state.branches:
        return None
    ref = str(ref or "").strip()
    picked: ChapterBranch | None = None
    for index, branch in enumerate(state.branches, start=1):
        # 序号与 id 是系统给的，可以直接匹配；标题要模型补齐后才拿得到，
        # 所以只在 ref 确实是标题时才参与匹配（否则空标题会拦掉按序号的选中）。
        if ref in (str(index), branch.id):
            picked = branch
            break
        if branch.title and ref == branch.title:
            picked = branch
            break
    if picked is None:
        return None

    card = ChapterCard(
        id=picked.id,
        title=picked.title,
        arc=picked.arc,
        goal=picked.goal,
        locations=list(picked.locations),
        parallel=picked.parallel,
        story_points=3,
    )
    state.pending = card
    state.branches = []
    _store(save, state)
    return card


def start_chapter(
    save: Any,
    card: ChapterCard,
    *,
    target_cr: str = "",
    xp_budget: int = 0,
    world_key: str = "",
    write_to_world: bool = True,
) -> ChapterCard:
    """正式落卡（模型补齐内容之后调用）。

    这一步做三件事：

    1. 补上缺省 :attr:`~ChapterCard.fail_forward`（唯一允许补的字段）；
    2. **把卡写进世界库**（:func:`save_card`），所以织过的章都能复玩；
    3. 存档只留**引用 + 只读快照**：``card.id`` 是世界库里的键，
       :attr:`~ChapterState.card` 是副本——改库里的卡不会让已开的局变形。

    :param world_key: 写哪个世界；留空则跳过入库（只留存档引用）。
    :param write_to_world: 关掉入库只留存档（例如自检、预览时不想污染世界库）。

    入库失败（磁盘、权限等）**不抛异常**：章节已经织出来了，
    丢掉这一章比没存进库糟糕得多，所以降级成只留存档引用。
    """
    state = _load(save)
    if not card.id:
        # id 既是存档引用又是世界库键，所以要真的不冲突，不能只按章号
        card.id = _fresh_card_id(world_key, state.completed + 1)
    if target_cr:
        card.target_cr = target_cr
    if xp_budget:
        card.xp_budget = xp_budget
    ensure_fail_forward(card, where=(card.locations[0] if card.locations else "这里"))

    if write_to_world and world_key:
        try:
            save_card(world_key, card, chapter_no=state.completed + 1)
            # 沉淀只登记「本章卡上写着、但圣经里还没有」的专有名词；
            # 校验已经拦过不在世界里的地名，所以这里不会凭空造出世界没有的东西
            deposit_entities(world_key, card, chapter_no=state.completed + 1)
        except Exception:
            _log.warning(f"章节卡入库失败（已降级为只存档引用）：{card.id}")

    state.card = snapshot_card(card)
    state.checked = []
    state.branches = []
    state.pending = ChapterCard()
    for name in {*card.locations, *card.npcs}:
        if name not in state.used_elements:
            state.used_elements.append(name)
    # 每章都记，重复也要记：去重窗口看的是「最近三章打过什么」，
    # 若相同骨架不重复记录，窗口会失真成「最近三种不同骨架」，
    # 于是同一骨架隔一章就能连打两次。
    if card.arc:
        state.recent_arcs.append(card.arc)
    # 窗口有界，否则跑久了存档会一直涨；留住最近 8 章足够去重
    if len(state.recent_arcs) > 8:
        del state.recent_arcs[:-8]
    _store(save, state)
    return card


def objective_progress(save: Any) -> tuple[int, int, list[Objective]]:
    """当前章的完成条件进度：``(已勾数, 总数, 目标列表)``。"""
    state = _load(save)
    objs = state.card.objectives
    return len(state.checked), len(objs), objs


def check_objective(save: Any, index: int, *, done: bool = True) -> int:
    """勾/取消一条完成条件，返回已勾数。"""
    state = _load(save)
    if 0 <= index < len(state.card.objectives):
        if done and index not in state.checked:
            state.checked.append(index)
            state.checked.sort()
        elif not done and index in state.checked:
            state.checked.remove(index)
    _store(save, state)
    return len(state.checked)


def note_clue(save: Any, clue: str) -> None:
    """记一条线索（同时供客观完成条件自动判定）。"""
    state = _load(save)
    clue = str(clue or "").strip()
    if clue and clue not in state.clues:
        state.clues.append(clue)
    _store(save, state)


def _auto_cleared(card: ChapterCard, state: ChapterState) -> list[int]:
    """自动判定客观条件，返回满足的目标序号。"""
    got = set(state.clues)
    hit: list[int] = []
    for index, obj in enumerate(card.objectives):
        if not obj.auto:
            continue
        if (obj.kind == "clue" and (not obj.ref or obj.ref in got)) or (obj.kind == "item" and obj.ref and obj.ref in got):
            hit.append(index)
    return hit


def auto_sync_objectives(save: Any) -> tuple[int, int]:
    """把客观条件自动勾上，返回 ``(已勾, 总数)``。

    客观条件程序能验就直接验，描述性目标仍然要 DM 用工具报——
    自动推进不该在 DM 不想结束的时候结束，所以只覆盖 ``clue`` / ``item``。
    """
    state = _load(save)
    if not state.card.id:
        return (0, 0)
    for index in _auto_cleared(state.card, state):
        if index not in state.checked:
            state.checked.append(index)
    state.checked.sort()
    _store(save, state)
    return (len(state.checked), len(state.card.objectives))


def complete_chapter(save: Any, *, factor: float = 0.0, note: str = "") -> ChapterCard | None:
    """结束当前章，返回被结束的卡（没有当前章则 ``None``）。

    :param factor: 本章的表现系数，写进状态供下一章算难度。
    :param note: 一句话摘要，进 ``history`` 与手册。
    """
    state = _load(save)
    if not state.card.id:
        return None
    finished = state.card
    state.completed += 1
    state.last_factor = float(factor)
    # 这一章的完成条件进度要留下来：纪元收束判定要看「整个纪元勾了多少」，
    # 只看最后一章等于把前面三章的成果抹掉。
    state.progresses.append([len(state.checked), len(finished.objectives)])
    state.history.append(
        str(note).strip()
        or finished.title
        or finished.goal
        or f"第 {state.completed} 章"
    )
    state.card = ChapterCard()
    state.checked = []
    #刻意**不**把刚结束的骨架移出 recent_arcs：去重窗口要记的是「最近打过哪几种骨架」，
    # 移掉等于窗口永远追不上刚结束的章，同一骨架会立刻被抽到第二次。
    _store(save, state)
    return finished


def advance_chapter(
    save: Any,
    card: ChapterCard,
    *,
    factor: float = 0.0,
    world_key: str = "",
    target_cr: str = "",
    xp_budget: int = 0,
    note: str = "",
) -> ChapterCard:
    """结束当前章并把新卡接上（连续推进时用，避免中间态丢在存档里）。

    :param note: 一句话摘要，进 ``history`` 与手册；留空则用章节标题。
    """
    complete_chapter(save, factor=factor, note=note)
    state = _load(save)
    state.last_factor = float(factor)
    _store(save, state)
    return start_chapter(
        save, card, target_cr=target_cr, xp_budget=xp_budget, world_key=world_key
    )


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #


def chapter_summary(save: Any, *, for_player: bool = True) -> str:
    """当前章节的摘要文本。

    :param for_player: 玩家侧只给「目标 / 已知线索 / 完成条件 / 已完成章节」，
        不泄露难度数值与威胁层级——那是DM 侧的东西。
    """
    state = _load(save)
    if not state.card.id and not state.history:
        return ""
    lines: list[str] = []
    if state.card.id:
        card = state.card
        lines.append(f"【第 {state.completed + 1} 章】{card.title}".rstrip("】"))
        if card.goal:
            lines.append(f"目标：{card.goal}")
        if for_player:
            if card.entry:
                lines.append(f"缘由：{card.entry}")
            if card.locations:
                lines.append(f"去处：{'、'.join(card.locations)}")
        done, total, objs = len(state.checked), len(card.objectives), card.objectives
        if objs:
            lines.append(f"完成条件（{done}/{total}）：")
            for index, obj in enumerate(objs):
                mark = "x" if index in state.checked else " "
                lines.append(f"  [{mark}] {obj.text}")
        if not for_player:
            if card.target_cr:
                lines.append(f"难度：目标 CR {card.target_cr}，本章预算 {card.xp_budget} XP")
            if card.fail_forward:
                lines.append(f"没做完：{card.fail_forward}")
        if state.clues:
            lines.append(f"已知线索：{'、'.join(state.clues)}")
    if state.history:
        lines.append("已完成：" + "、".join(state.history))
    return "\n".join(lines)
