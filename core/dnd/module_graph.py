"""模组图：网状剧情结构的纯函数层。

**一张图 = 一个故事 = 一章。**

## 三层，各管各的

| 层 | 是什么 | 谁负责 |
|---|---|---|
| **章** | **一个故事**：一个核心冲突 + 一张节点图 + 一组收束 | 这一层 |
| **场所** | 一处地方：修道院、洞穴、某艘船 | 世界库的 ``place`` 文档（``scene.meta.site`` 指向它） |
| **场景** | 场所里的**一块地方**：可以是「城里的某间酒馆」这种区域，也可以是「船上的船长室」这种房间 | 世界库的 ``scene`` 文档 |

玩家在**场景之间**移动，所以 ``Node.at`` 填的是**场景名**——
不是 ``city`` / ``dungeon`` 这类地点名（地点是场所的容器，不是玩家站着的地方）。

**场景可以是区域，也可以是房间**（2026-10-22 改）。原先的定义是
「场景 = 一个区域，房间由 agent 临场描绘」，理由是「不要给场景预写房间：
一旦写死，DM 就只能在房间里念稿子」。但那假设了**所有模组都是自己写的**。
官方模组（风骸岛之龙）一艘船有主甲板、船长室、储藏室、九个编号舱房，
**每一间都有作者写死的遭遇、机关与宝藏清单**——把它们压进「一个场景」就全丢了。

所以现在两种都允许：

- **场景 = 区域**：没有作者写死的房间时用这个，房间由 agent 临场描绘，
  玩家的即兴有落脚处；
- **场景 = 房间**：官方模组那种「一间房一份文档」时用这个，内容零丢失。

两种靠**场所组**区分：同一个 ``place`` 底下的几个场景是同一处地方的房间，
它们之间移动**不扣时间、不判路上有没有事**（见
:func:`~...agents.dnd.event_flow.step_move`）。

**章不是升级单位。** 沙盒的玩家进来时不知道自己几级，也不该为了升一级去打完一章。
难度按队伍实际水平算（``encounter_budget`` 的三轨），所以一章跑完可能一级都不升，
也可能连升两级。

## 为什么不是事件卡

事件卡的形状是「一个目标 + 几条完成条件」，它只能描述一条线：勾完就是过，
过完去下一章。一条线写六遍，必然是六份同款——一个疑点、两个地点、一张清单、
一个不肯说的人。所以「内容太浅」的根因不在文笔，在形状。

模组的形状是**图**，而图的边不是顺序，是**事实**：

- ``node`` 是局面（在某地、要哪些事实、给出哪些事实）；
- ``fact`` 是钥匙（拿到它，别的局面才对你打开）；
- ``convergence`` 是收束（哪几套事实同时成立时，进哪个结局）；
- ``clock`` 是时钟（不管玩家做什么，到点就发生）。

节点自己不知道「第几章」，顺序由玩家手里的钥匙决定。这就是沙盒：
**同一个模组，两次游玩应该走出不同的节点序列。**

## 「深」靠四个结构件，不靠形容词

1. **代价（``Node.cost``）**：每个局面都要付东西——暴露自己、欠人情、错过另一处。
   没有代价的线索等于白送，玩家不需要判断。
2. **互斥（``Fact.excludes``）**：拿到 A 就永久失去 B。**必须放弃一些真相**，
   这才是「玩」而不是「收集」。
3. **分层（``Node.depth``）**：同一个地点可以挂多个节点。第二次来才问得深，
   因为深的那层要求第一次拿到的事实。
4. **时钟（``Clock``）**：不处理它，世界自己变坏。

## 「玩家不干了」是正常结局，不是异常分支

沙盒里玩家**完全可以觉得没意思就扔下这一章**。所以这一层必须能回答
「他们放弃了什么、世界因此怎样」——那是 :func:`abandon_summary`，
不是「没通关」的错误处理。

三条推论：

1. **任何节点都可以是退出点**。不存在「必须先打碎水晶才能离开」这种硬门槛。
2. **放弃也要有结算**：哪条收束还差哪几个事实、哪些时钟因此到点。世界不会等他们
   回来，但也不惩罚他们——只是继续往前走。
3. **收束不是「必须达成」**。收束回答的是「这段故事最后成了什么样」，
   「没成」也是一种答案。:data:`DEFAULT_EPOCH_ENDING` 就是那个兜底。

## 校验是这一层的存在理由

「网状」如果不能被程序验证，就只是形容词。:func:`validate` 拒绝这几种图：

- 某个事实没有任何节点能产出（拿不到的事实 = 死锁）；
- 某个节点从入口不可达（永远不活）；
- 收束只有一套条件且没有冗余产出（**线性，不是网**）；
- 只有一个收束（那不是多结局，是「一条线一个出口」）。

关键判据是 :func:`has_alternative_route`：一个收束要么有**两套不同的成立条件**，
要么它某个必需事实**能在两处拿到**。两者都没有，玩家就永远只有一条路——
那叫走廊，不叫沙盒。

## 落在哪里

- 纯逻辑，不碰存档、不调模型（与 ``event_no.py`` / ``evolution.py`` 同一层）；
- 「读存档、调模型造图、校验后接进图」在 ``core/agents/dnd/module_flow.py``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "BATTLE_MIN",
    "CONVERGENCE_KINDS",
    "DEFAULT_EPOCH_ENDING",
    "FACT_KINDS",
    "GROW_KINDS",
    "GROW_MARKER",
    "MAX_NODES",
    "NODE_KINDS",
    "OPENING_HOOK_HINTS",
    "AbandonResult",
    "Clock",
    "Convergence",
    "Fact",
    "GrowResult",
    "ModuleGraph",
    "Node",
    "abandon_summary",
    "abandon_text",
    "advice",
    "fire_node",
    "graph_text",
    "grow_node",
    "open_nodes",
    "opening_hook_tips",
    "reachable",
    "reached_convergences",
    "text_to_node_spec",
    "tick_clocks",
    "validate",
    "verdict_text",
]

#: 事实的种类。**这是「玩家手里有什么」的分类**，不是剧情的分类。
FACT_KINDS: dict[str, str] = {
    "secret": "秘密",  # 有人知道、但不肯说的事
    "proof": "证据",  # 能拿去对质的实物
    "debt": "人情",  # 欠下的、或别人欠你的
    "access": "通行",  # 能进某个地方、能动某个东西
}

#: 节点的种类。刻意只有六种：多一种就会有人拿它当「第几章」用。
NODE_KINDS: dict[str, str] = {
    "clue": "可查",  # 一处能翻、能问、能看的东西
    "encounter": "遭遇",  # 一场见面，可能谈成也可能谈崩
    "battle": "战斗",  # 真打。**DND 的主轴**，与「遭遇」分开：
    #   遭遇可能是纯交涉，战斗必然有先攻、回合、伤害
    "decision": "抉择",  # 只能选一边，两边互斥
    "clock": "时钟",  # 不由玩家触发，到点自己发生
    "showdown": "收束前哨",  # 走向结局的那一步
}

#: 每个规模档**至少**要有几场战斗。
#:
#: 战斗不是「偶尔发生」，是 DND 的主轴。一个没有战斗的事件在这个系统里会退化成
#: 「一段叙述」——而叙述是 DM 最不缺的东西。所以这条是硬校验，不是建议。
BATTLE_MIN: dict[str, int] = {
    "light": 1,
    "medium": 2,
    "heavy": 3,
}

#: 开局硬钩子的四类**标记词**（提示词硬规则 12 的程序一侧）。
#:
#: 开局那一刻玩家在这世界里**没有历史**——没有债、没有名声、没有仇人、
#: 没有已建立的关系——所以入口不能依赖「有人请你去看看」这类可以转身走开的事。
#: 它只能是四类之一：被扣下 / 过不去 / 撞见 / 非要不可。
#:
#: **这是探针，不是判定**：程序读不懂「这件事是否作用在玩家身上」，
#: 只能从作者写下的字里找四类钩子的痕迹。所以取词刻意保守——
#: 一律两字以上（「扣」会命中纽扣、「封」会命中封蜡），一类都没命中才报问题。
#: 宁可放过一个写得含蓄的硬钩子，也不要错杀：错杀的代价是开局装不上事件。
#:
#: **只对开局生效**（见 :func:`validate` 的 ``opening`` 参数）：等玩家有了历史，
#: 「受雇」「听说」就是正当入口——那时玩家有名声，别人会来找他。
OPENING_HOOK_HINTS: dict[str, tuple[str, ...]] = {
    "被扣下": (
        "扣下",
        "扣住",
        "扣了",
        "嫌疑人",
        "认错",
        "认出",
        "栽赃",
        "搜出",
        "没收",
        "押在",
        "押走",
        "不准走",
        "不让你走",
        "走不掉",
        "脱不了身",
        "拦下",
    ),
    "过不去": (
        "过不去",
        "过不了",
        "进不去",
        "出不去",
        "不开门",
        "船不发",
        "断路",
        "禁行",
        "船期",
        "赶不上",
        "关了",
        "关着",
        "锁了",
        "紧闭",
        "封路",
        "封了",
        "挡在",
        "挡了",
        "不许走",
        "不许进",
        "不让走",
    ),
    "撞见": (
        "倒在",
        "死在",
        "眼前",
        "脚边",
        "当面",
        "眼皮",
        "砸在",
        "就在你面前",
        "撞上",
    ),
    "非要不可": (
        "只有这",
        "非得",
        "非它不可",
        "非去不可",
        "修不好",
        "等的人",
        "要找的人",
        "缺了它",
        "唯一",
    ),
}

#: 收束的种类。
CONVERGENCE_KINDS: dict[str, str] = {
    "ending": "结局",  # 本局到此为止
    "turn": "转折",  # 不结束，纪元换挡（压力升级）
}

#: 一张图的节点数上限。超过它的图既读不完也跑不动，
#: 而且几乎一定意味着有人把「事件」又塞回来了。
MAX_NODES = 40

#: 没有任何收束成立时的兜底结局 key（连续跑不崩的保险）
DEFAULT_EPOCH_ENDING = "unresolved"

#: DM 现场造节点时用的标记（工具参数以它开头才算「造节点」，不当线索记）
GROW_MARKER = "新局面"

#: 造节点时程序分配的 id 前缀。图里其余 id 由生成器 / 手写给定，
#: 所以两套编号不会互相挤（与 ``events.new_event_id`` 同一思路）。
GROW_NODE_PREFIX = "gn"
GROW_FACT_PREFIX = "gf"

#: 生长允许的节点种类。**刻意不含 ``clock``**：时钟到点自己发生
#: （见 :data:`Clock` 与 :func:`tick_clocks`），而生长出来的节点只能由
#: DM 报局面名结算——挂个「时钟」标签却要人手动推，是自相矛盾。
GROW_KINDS: tuple[str, ...] = ("clue", "encounter", "battle", "decision", "showdown")


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Fact:
    """一条事实——图里的边。

    ``text`` 是**玩家会知道的说法**，不是系统给的名字：它要能直接进提示词，
    让 DM 不必猜「这个 id 是什么意思」。
    """

    id: str
    text: str
    kind: str = "secret"
    #: 拿到它之后**永久失去**的事实（抉择的代价）。
    #: 互斥是单向声明的：A 排除 B 时，只在拿到 A 的那一刻删掉 B。
    excludes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "excludes": list(self.excludes),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Fact:
        d = data if isinstance(data, dict) else {}
        return cls(
            id=str(d.get("id") or "").strip(),
            text=str(d.get("text") or "").strip(),
            kind=str(d.get("kind") or "secret").strip(),
            excludes=[str(x) for x in (d.get("excludes") or [])],
        )


@dataclass(slots=True)
class Node:
    """一个局面。

    **它没有「第几章」这个字段，这是刻意的。** 节点的可见性完全由
    ``requires``（手里要有哪些事实）决定，所以同一个图里，
    不同的玩家会走出不同的节点序列——那才是沙盒。
    """

    id: str
    title: str
    #: 所在**场景**（世界库里的 ``scene`` 名）。留空表示「哪儿都可能」。
    #:
    #: 场景**可以是区域，也可以是房间**（见模块说明）：填 ``scene`` 而不是
    #: ``city`` / ``dungeon``，因为地点是场所的容器，不是玩家站着的地方。
    #: 同一 ``place`` 底下的几个场景算「同一个场所里的房间」，
    #: 它们之间移动不扣时间（场所组，见 ``event_flow._same_site``）。
    at: str = ""
    kind: str = "clue"
    #: 全部满足才活（「与」）。空 = 开局就活，也就是入口。
    requires: list[str] = field(default_factory=list)
    #: 顺利时给出的事实。
    grants: list[str] = field(default_factory=list)
    #: **谈崩 / 失败时给出的事实**。这是沙盒的关键：
    #: 失败不是「跳过」，而是把玩家推到另一个局面去。
    on_fail: list[str] = field(default_factory=list)
    #: 做这件事要付的代价（一句话，给 DM 叙述用）。空 = 白送，慎用。
    cost: str = ""
    #: 第几层。1 = 表层（进门就能问），2 = 拿到别的钥匙后再来才看得见。
    #: 逻辑上不参与判定（分层靠 ``requires`` 实现），只用于展示与检查。
    depth: int = 1
    #: 给 DM 的一句话：这里会发生什么。**不写台词**——台词是 DM 的事。
    hint: str = ""

    @property
    def gives(self) -> list[str]:
        """无论成败都会给的事实（顺利 + 失败前进）。"""
        return list(dict.fromkeys([*self.grants, *self.on_fail]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "at": self.at,
            "kind": self.kind,
            "requires": list(self.requires),
            "grants": list(self.grants),
            "on_fail": list(self.on_fail),
            "cost": self.cost,
            "depth": self.depth,
            "hint": self.hint,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Node:
        d = data if isinstance(data, dict) else {}
        try:
            depth = int(d.get("depth") or 1)
        except (TypeError, ValueError):
            depth = 1
        return cls(
            id=str(d.get("id") or "").strip(),
            title=str(d.get("title") or "").strip(),
            at=str(d.get("at") or "").strip(),
            kind=str(d.get("kind") or "clue").strip(),
            requires=[str(x) for x in (d.get("requires") or [])],
            grants=[str(x) for x in (d.get("grants") or [])],
            on_fail=[str(x) for x in (d.get("on_fail") or [])],
            cost=str(d.get("cost") or "").strip(),
            depth=max(1, depth),
            hint=str(d.get("hint") or "").strip(),
        )


@dataclass(slots=True)
class Convergence:
    """收束：**任意一组**事实全部成立即达成（析取范式）。

    ``requires`` 是「组的列表」，不是「事实的列表」。例如::

        requires=[["ledger", "tunnel"], ["ledger", "seal", "abbot"]]

    表示「两套账 + 窄道」或「两套账 + 封蜡 + 落款」都能收。
    多组并存正是沙盒要的：**不同的路通向同一个结局，且各自握着不同的筹码**。
    """

    id: str
    title: str
    requires: list[list[str]] = field(default_factory=list)
    kind: str = "ending"
    #: 多个收束同时成立时，``rank`` 小的优先。用于「更具体的结局先判」。
    rank: int = 0
    #: 达成后世界怎么变（一句话，给 DM 叙述用）。
    outcome: str = ""

    def tiers(self) -> list[list[str]]:
        """规范化成非空的条件组（去重、去空组）。"""
        out: list[list[str]] = []
        for group in self.requires:
            tier = [str(x) for x in (group or []) if str(x).strip()]
            if tier and tier not in out:
                out.append(tier)
        return out

    @property
    def fact_count(self) -> int:
        """最省的那一组要几个事实（用于判断这是不是一条捷径）。"""
        tiers = self.tiers()
        return min((len(t) for t in tiers), default=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "requires": [list(g) for g in self.requires],
            "kind": self.kind,
            "rank": self.rank,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Convergence:
        d = data if isinstance(data, dict) else {}
        groups: list[list[str]] = []
        for group in d.get("requires") or []:
            if isinstance(group, str):
                groups.append([group])
            elif isinstance(group, list | tuple):
                groups.append([str(x) for x in group])
        try:
            rank = int(d.get("rank") or 0)
        except (TypeError, ValueError):
            rank = 0
        return cls(
            id=str(d.get("id") or "").strip(),
            title=str(d.get("title") or "").strip(),
            requires=groups,
            kind=str(d.get("kind") or "ending").strip(),
            rank=rank,
            outcome=str(d.get("outcome") or "").strip(),
        )


@dataclass(slots=True)
class Clock:
    """时钟：每 ``every`` 章推进一次，到点触发 ``fires`` 指向的节点。

    时钟是沙盒的必需品。没有它，世界就在原地等玩家——
    玩家不动，世界也不动，那只是「开放式地图」，不是沙盒。
    """

    name: str
    every: int = 4
    fires: str = ""
    #: 已经推进到第几格（运行时状态，图本身不需要，但存一起省一次序列化）
    ticks: int = 0

    @property
    def ready(self) -> bool:
        return self.every > 0 and self.ticks >= self.every

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "every": self.every,
            "fires": self.fires,
            "ticks": self.ticks,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Clock:
        d = data if isinstance(data, dict) else {}
        try:
            every = int(d.get("every") or 4)
        except (TypeError, ValueError):
            every = 4
        try:
            ticks = int(d.get("ticks") or 0)
        except (TypeError, ValueError):
            ticks = 0
        return cls(
            name=str(d.get("name") or "").strip(),
            every=max(1, every),
            fires=str(d.get("fires") or "").strip(),
            ticks=max(0, ticks),
        )


@dataclass(slots=True)
class AbandonResult:
    """玩家扔下这一章之后的结算。

    **不是失败。** 失败是「这件事你做砸了」（见 :attr:`Node.on_fail`），
    放弃是「这件事你们不想做了」——失败换一条路继续，放弃是这段故事到此为止。
    所以这里没有惩罚，只有账目：做了什么、还差什么、世界因此到了哪一步。
    """

    #: 其实已经成立的收束（有它就不叫放弃，叫收尾）
    settled: list[Convergence] = field(default_factory=list)
    #: 没做成的：每条未达成的收束，以及它**最接近的那一组**还差哪几个事实
    unmet: list[tuple[Convergence, list[str]]] = field(default_factory=list)
    #: 因为这一章过去了而到点的时钟
    due_clocks: list[Clock] = field(default_factory=list)
    #: 做过的事（节点标题，供 DM 复盘；不是流水账）
    done: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """有收束成立就算「收尾」，不算「放弃」。"""
        return bool(self.settled)


@dataclass
class ModuleGraph:
    """一张模组图。"""

    #: 一句话的轴：谁要什么、谁挡着谁。每条边都该能回到这句。
    conflict: str = ""
    #: 规模档：``light`` / ``medium`` / ``heavy``。**它替代「推荐等级」**——
    #: 玩家是任意等级进来的，这一档只说明「这件事本身多大」，难度由程序按队伍水平缩放。
    scale: str = "medium"
    facts: dict[str, Fact] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    convergences: list[Convergence] = field(default_factory=list)
    clocks: list[Clock] = field(default_factory=list)

    # ---------------------------------------------------------------- 基本属性

    @property
    def entries(self) -> list[str]:
        """入口节点：没有前置事实的那些。"""
        return [n.id for n in self.nodes.values() if not n.requires]

    def node(self, node_id: str) -> Node | None:
        return self.nodes.get(str(node_id or ""))

    def fact_text(self, fact_id: str) -> str:
        fact = self.facts.get(str(fact_id or ""))
        return fact.text if fact else ""

    def producers(self, fact_id: str) -> list[str]:
        """能产出某个事实的节点 id（顺利或失败前进都算）。"""
        return [n.id for n in self.nodes.values() if fact_id in n.gives]

    # ---------------------------------------------------------------- 序列化

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict": self.conflict,
            "scale": self.scale,
            "facts": {k: v.to_dict() for k, v in self.facts.items()},
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "convergences": [c.to_dict() for c in self.convergences],
            "clocks": [c.to_dict() for c in self.clocks],
        }

    @classmethod
    def from_dict(cls, data: Any) -> ModuleGraph:
        d = data if isinstance(data, dict) else {}
        facts = {
            str(k): Fact.from_dict({**v, "id": k})
            for k, v in (d.get("facts") or {}).items()
            if isinstance(v, dict)
        }
        nodes = {
            str(k): Node.from_dict({**v, "id": k})
            for k, v in (d.get("nodes") or {}).items()
            if isinstance(v, dict)
        }
        return cls(
            conflict=str(d.get("conflict") or "").strip(),
            scale=str(d.get("scale") or "medium").strip() or "medium",
            facts=facts,
            nodes=nodes,
            convergences=[
                Convergence.from_dict(c)
                for c in (d.get("convergences") or [])
                if isinstance(c, dict)
            ],
            clocks=[
                Clock.from_dict(c)
                for c in (d.get("clocks") or [])
                if isinstance(c, dict)
            ],
        )

    @property
    def empty(self) -> bool:
        return not self.nodes and not self.convergences

    # ---------------------------------------------------------------- 运行时

    def add_node(self, node: Node) -> None:
        """接一个新节点（DM 现场造的那种）。**调用方必须先跑 :func:`validate`**。"""
        self.nodes[node.id] = node

    def remove_node(self, node_id: str) -> Node | None:
        return self.nodes.pop(str(node_id or ""), None)


# --------------------------------------------------------------------------- #
# 可达性
# --------------------------------------------------------------------------- #


def reachable(
    graph: ModuleGraph,
    *,
    skip: frozenset[str] | set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """从入口出发能拿到哪些事实、会经过哪些节点（不跳过任何节点）。

    :param skip: 假装这些节点不存在（用来找「必经节点」）。
    :return: ``(事实集合, 节点集合)``

    单调假设：事实一旦拿到就不会失去。``excludes`` 只在真正触发时生效，
    这里不算——可达性问的是「能不能拿到」，不是「拿到之后还剩什么」。
    """
    blocked = {str(x) for x in (skip or ())}
    have: set[str] = set()
    fired: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in graph.nodes.values():
            if node.id in blocked or node.id in fired:
                continue
            if all(f in have for f in node.requires):
                fired.add(node.id)
                for fact_id in node.gives:
                    if fact_id and fact_id not in have:
                        have.add(fact_id)
                        changed = True
    return have, fired


def open_nodes(
    graph: ModuleGraph,
    facts: set[str] | list[str],
    visited: set[str] | list[str] | None = None,
) -> list[Node]:
    """当前「活着」的节点：前置事实齐了、且还没做过。

    按 ``depth`` 再按 ``at`` 排序——同一地点的浅层排在前面，
    DM 拿到这份清单就知道「玩家现在在哪儿能做什么」。
    """
    have = {str(x) for x in (facts or ())}
    done = {str(x) for x in (visited or ())}
    alive = [
        n
        for n in graph.nodes.values()
        if n.id not in done and all(f in have for f in n.requires)
    ]
    alive.sort(key=lambda n: (n.depth, n.at, n.id))
    return alive


def fire_node(
    graph: ModuleGraph,
    node_id: str,
    *,
    facts: set[str] | list[str],
    ok: bool = True,
) -> tuple[list[str], list[str]]:
    """触发一个节点，返回 ``(新增事实, 因互斥失去的事实)``。

    ``ok=False`` 走 ``on_fail``——这是**失败前进**：不成功也有东西拿，
    只是拿到的是另一条路上的东西。沙盒里「谈崩」不该是空手而归。
    """
    node = graph.node(node_id)
    if node is None:
        return [], []
    have = {str(x) for x in (facts or ())}
    gained = [f for f in (node.grants if ok else node.on_fail) if f and f not in have]

    # 互斥：拿到某条事实时，删掉它排除的那些（已经失去的不会回来）。
    # 只看**这次新拿到的**——已有事实的互斥在拿到它的那一刻就已经生效过，
    # 重复触发不该再删一遍（否则同一个节点做两次会越删越多）。
    lost: list[str] = []
    for fact_id in gained:
        fact = graph.facts.get(fact_id)
        if fact is None:
            continue
        for excluded in fact.excludes:
            if excluded in have and excluded not in lost:
                lost.append(excluded)
    return gained, lost


def _tier_ok(tier: list[str], have: set[str]) -> bool:
    return bool(tier) and all(f in have for f in tier)


def reached_convergences(
    graph: ModuleGraph,
    facts: set[str] | list[str],
) -> list[Convergence]:
    """当前成立的收束，按 ``rank`` 再按「更具体优先」排序。

    多个同时成立时，**事实要求更多的那条排前面**——越具体的结局越该先判，
    这和纪元结算里「清偿优先于凯旋」是同一条道理。
    """
    have = {str(x) for x in (facts or ())}
    hit = [c for c in graph.convergences if any(_tier_ok(t, have) for t in c.tiers())]
    hit.sort(key=lambda c: (c.rank, -max((len(t) for t in c.tiers()), default=0)))
    return hit


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


def tick_clocks(graph: ModuleGraph, *, by: int = 1) -> list[Clock]:
    """一章过去了，所有时钟往前走 ``by`` 格；返回这一下到点的那些。

    时钟推进**与玩家做成没做成无关**——这就是沙盒：世界不会因为他们不干了就停下。
    """
    step = max(0, int(by))
    for clock in graph.clocks:
        if clock.every > 0:
            clock.ticks += step
    return [c for c in graph.clocks if c.ready]


def abandon_summary(
    graph: ModuleGraph,
    *,
    facts: set[str] | list[str],
    visited: set[str] | list[str] | None = None,
) -> AbandonResult:
    """玩家扔下这一章时的结算。

    「还差哪几个事实」只报**最接近的那一组**：一条收束要三个、另一条要两个，
    已有两个——报两个那组才有意义，全列出来 DM 会读出一堆无关的缺项。

    这一步**不回滚** ``facts`` / ``visited``。已经拿到的线索还在玩家手里，
    那是他们真的做过的事；放弃只意味着这一章的故事停在这儿。
    """
    have = {str(x) for x in (facts or ())}
    settled = reached_convergences(graph, have)
    settled_ids = {c.id for c in settled}

    unmet: list[tuple[Convergence, list[str]]] = []
    for conv in graph.convergences:
        if conv.id in settled_ids:
            continue
        tiers = conv.tiers()
        if not tiers:
            continue
        best = min(tiers, key=lambda t: sum(1 for f in t if f not in have))
        missing = [f for f in best if f not in have]
        if missing:
            unmet.append((conv, missing))

    done = [
        graph.nodes[node_id].title or node_id
        for node_id in (visited or ())
        if node_id in graph.nodes
    ]
    return AbandonResult(
        settled=settled,
        unmet=unmet,
        due_clocks=[c for c in graph.clocks if c.ready],
        done=done,
    )


def _dangling_ids(graph: ModuleGraph) -> list[str]:
    """引用了不存在的 fact / node。"""
    problems: list[str] = []
    for node in graph.nodes.values():
        for fact_id in [*node.requires, *node.grants, *node.on_fail]:
            if fact_id not in graph.facts:
                problems.append(
                    f"节点「{node.title or node.id}」引用了不存在的事实：{fact_id}"
                )
    for conv in graph.convergences:
        for tier in conv.tiers():
            for fact_id in tier:
                if fact_id not in graph.facts:
                    problems.append(
                        f"收束「{conv.title or conv.id}」引用了不存在的事实：{fact_id}"
                    )
    for clock in graph.clocks:
        if clock.fires and clock.fires not in graph.nodes:
            problems.append(f"时钟「{clock.name}」指向不存在的节点：{clock.fires}")
    for fact in graph.facts.values():
        for other in fact.excludes:
            if other not in graph.facts:
                problems.append(f"事实「{fact.id}」排除了不存在的事实：{other}")
    return problems


def has_alternative_route(graph: ModuleGraph, conv: Convergence) -> bool:
    """这个收束**是不是有两条路**。

    两种可能算「有两条路」：

    1. 它有两套以上**不同的**成立条件（析取范式多组）；
    2. 它某个必需事实**能在两个以上节点拿到**（冗余产出）。

    两者都没有，玩家就只有一条路能到这儿。那不是网，是走廊。
    """
    tiers = conv.tiers()
    if len({frozenset(t) for t in tiers}) >= 2:
        return True
    for tier in tiers:
        for fact_id in tier:
            if len(graph.producers(fact_id)) >= 2:
                return True
    return False


def opening_hook_tips(graph: ModuleGraph) -> list[str]:
    """开局入口该是**硬钩子**（提示词硬规则 12）——**只提示，不拒绝**。

    **只对开局调用**（:func:`advice` 的 ``opening`` 参数）：开局时玩家
    在这世界里没有历史，入口必须是他躲不开的四类之一；等玩家有了历史，
    「有人请你去看看」就是正当入口——那时别人会来找一个有名声的人。

    判定靠 :data:`OPENING_HOOK_HINTS` 的标记词，**宁可漏报不可错杀**。
    曾经做成过硬校验，第二天就被自己的预写事件打脸：``ev_灰烬台`` 的入口
    「关了三日的门」是标准的「过不去」，只因为标题太简练没命中任何标记词，
    差一点让这一省开局装不上事件。所以它只配当提示——
    真正管住它的是 :mod:`tests.test_dnd_preset_seed` 里那条「预写事件
    不许有开局提示」的测试，那才是能执行的标准。
    """
    if not graph.entries:
        return []
    hits: list[str] = []
    for node_id in graph.entries:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        text = " ".join((node.title or "", node.hint or "", node.cost or ""))
        for kind, words in OPENING_HOOK_HINTS.items():
            if kind not in hits and any(w in text for w in words):
                hits.append(kind)
    if hits:
        return []
    entry = graph.nodes.get(graph.entries[0])
    name = (entry.title or entry.id) if entry is not None else graph.entries[0]
    return [
        f"开局入口「{name}」不像硬钩子：开局那一瞬玩家没有历史，入口该是他"
        "躲不开的四类之一——被扣下 / 过不去 / 撞见 / 非要不可。"
        "「听说某地不太平」「有人请你去看看」这类可以转身走开的，玩家开局就会走开"
    ]


def validate(graph: ModuleGraph) -> list[str]:
    """校验一张模组图，返回**硬问题**列表（空 = 可以接进世界）。

    这一层是拒绝，不是修补：静默补全一张断掉的图，等于教模型「写坏也没关系」。
    """
    problems: list[str] = []
    problems.extend(_dangling_ids(graph))

    if not graph.nodes:
        return ["图里没有任何节点"]
    if len(graph.nodes) > MAX_NODES:
        problems.append(
            f"节点太多（{len(graph.nodes)} > {MAX_NODES}）：这不像一个模组，更像一本书"
        )
    if not graph.conflict.strip():
        problems.append("缺核心冲突：一句话说清谁要什么、谁挡着谁")

    if not graph.entries:
        problems.append("没有入口节点：所有节点都有前置事实，玩家一步也走不了")

    # 每个节点都要有产出，否则触发了什么也没发生（死胡同）
    for node in graph.nodes.values():
        if not node.gives:
            problems.append(f"节点「{node.title or node.id}」不产出任何事实（死胡同）")
        if node.kind not in NODE_KINDS:
            problems.append(f"节点「{node.title or node.id}」种类未知：{node.kind}")
        if not node.at.strip():
            problems.append(f"节点「{node.title or node.id}」没写所在地点")
        # 同**一边**（都算顺利、或都算失败）里既拿到 A 又拿到 B，而 A 排除 B：
        # 自相矛盾的声明。跨边不算——``on_fail`` 是另一条路上的东西，
        # ``decision`` 的「只能选一边，两边互斥」正是 grants / on_fail 各给一边，
        # ``fire_node`` 每次也只走一边。拿同一边的互斥才是真写错。
        for side in (node.grants, node.on_fail):
            gives = set(side)
            for fact_id in sorted(gives):
                fact = graph.facts.get(fact_id)
                if fact is None:
                    continue
                clash = sorted(x for x in fact.excludes if x in gives)
                if clash:
                    problems.append(
                        f"节点「{node.title or node.id}」同时给出互斥的事实："
                        f"{fact_id} 排除 {clash}"
                    )

    # 每个事实都要能拿到（拿不到的事实 = 永久的死锁）
    if graph.entries:
        have, fired = reachable(graph)
        for fact in graph.facts.values():
            if fact.id not in have:
                problems.append(
                    f"事实「{fact.text or fact.id}」从入口拿不到：会永久卡住"
                )
        for node in graph.nodes.values():
            if node.id not in fired:
                problems.append(
                    f"节点「{node.title or node.id}」从入口不可达：永远不会活"
                )
        # 每个事实要么能产出、要么被某个收束需要（否则没人关心它）
        needed = {f for c in graph.convergences for t in c.tiers() for f in t}
        for fact in graph.facts.values():
            if not graph.producers(fact.id) and fact.id not in needed:
                problems.append(f"事实「{fact.text or fact.id}」既拿不到、也没人需要")

    # 战斗是硬要求：DND 的主轴，不是「偶尔发生」
    battles = [n for n in graph.nodes.values() if n.kind == "battle"]
    need = BATTLE_MIN.get(str(graph.scale or "").strip(), BATTLE_MIN["medium"])
    if len(battles) < need:
        problems.append(
            f"战斗节点不足：{graph.scale} 档至少要 {need} 场，现在 {len(battles)} 场"
        )

    # 收束
    if len(graph.convergences) < 2:
        problems.append(
            f"只有 {len(graph.convergences)} 个收束：多结局至少要两个，"
            "否则就是「一条线一个出口」"
        )
    for conv in graph.convergences:
        tiers = conv.tiers()
        if not tiers:
            problems.append(f"收束「{conv.title or conv.id}」没有成立条件")
            continue
        if conv.kind not in CONVERGENCE_KINDS:
            problems.append(f"收束「{conv.title or conv.id}」种类未知：{conv.kind}")
        have, _fired = reachable(graph)
        if not any(_tier_ok(t, have) for t in tiers):
            problems.append(f"收束「{conv.title or conv.id}」从入口不可达")
            continue

    # 「撤退路」与「网的存在性」这两条从硬拒绝降成了软建议（见 :func:`advice`）：
    # 官方模组（风骸岛之龙）每一章都是线性探索、战斗常常是必经，
    # 硬拒绝等于说官方模组不能用。线性是**风格**，不是写错。
    return problems


def advice(graph: ModuleGraph, *, opening: bool = False) -> list[str]:
    """软建议（不拒绝，只在生成时回给模型让它自己改好）。

    与 :func:`validate` 分开：硬问题拒绝落库，软建议只提示。
    两者混在一起会让「必须改」和「最好改」变得一样重。

    :param opening: 这一件是**开局事件**吗？是的话额外提示入口该是硬钩子
        （:func:`opening_hook_tips`）。**只对开局生效**——等玩家有了历史，
        「受雇」「听说」就是正当入口，不该拿开局的标准去品评它。
    """
    tips: list[str] = []
    if len(graph.convergences) < 3:
        tips.append("收束只有两三个：沙盒里通常至少三个（一个正、一个偏、一个坏）")
    for conv in graph.convergences:
        if not has_alternative_route(graph, conv):
            tips.append(
                f"收束「{conv.title or conv.id}」是单条路：可以考虑给它第二套成立条件"
            )
    # 撤退路：玩家是**任意等级**进来的，可能撞上明显打不过的事件。
    # 留一条不依赖战斗事实的收束（「撤出去、把消息带回来」），他才有得选。
    battles = [n for n in graph.nodes.values() if n.kind == "battle"]
    if battles and graph.convergences:
        battle_facts = {f for n in battles for f in n.gives}
        has_escape = any(
            any(not (set(tier) & battle_facts) for tier in conv.tiers())
            for conv in graph.convergences
        )
        if not has_escape:
            tips.append(
                "所有收束都依赖战斗产出的事实：打不过的玩家没有任何退路"
                "（建议留一个「撤出去、把消息带回来」这类收束）"
            )
    for node in graph.nodes.values():
        if not node.cost.strip() and node.kind in ("encounter", "decision"):
            tips.append(
                f"节点「{node.title or node.id}」没有代价：白送的东西玩家不需要判断"
            )
    if not any(f.excludes for f in graph.facts.values()):
        tips.append("没有任何互斥事实：玩家不会被迫放弃什么，抉择就是假的")
    if not graph.clocks:
        tips.append("没有时钟：玩家不做，世界也不会变坏，那就只是开放式地图")
    battles = [n for n in graph.nodes.values() if n.kind == "battle"]
    # light 档**只要 1 场**（见 :data:`BATTLE_MIN`），必然挤在同一个场景——
    # 对它说「换个地方也该有一场」是让作者去违反规模档，不是改进。
    if battles and len(battles) >= 2 and len({n.at for n in battles if n.at}) < 2:
        tips.append(
            "战斗都挤在同一个场景：换个地方也该有一场，不然「这片地方危险」是空的"
        )
    deep = [n for n in graph.nodes.values() if n.depth >= 2]
    if not deep:
        tips.append("没有第二层节点：同一地点只有一次可挖，玩家来第二趟就没东西了")
    if opening:
        tips.extend(opening_hook_tips(graph))
    return tips


# --------------------------------------------------------------------------- #
# 生长（图可长，但生长要过审）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class GrowResult:
    """一次「现场造节点」的结果。

    **允许生长，不允许长坏**：``node is None`` 时图**一个字节都没改**
    （新事实也会回滚），``problems`` 里是不过审的理由。
    """

    #: 接进图的节点；被拒时为空
    node: Node | None = None
    #: 不过审的理由（空 = 接上了）
    problems: list[str] = field(default_factory=list)
    #: 这次**新建**的事实（玩家会知道的说法，也就是新发的钥匙）
    new_facts: list[Fact] = field(default_factory=list)
    #: 复用已有事实的说法（写对了原文 → 同一把钥匙，不新建）
    reused: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.node is not None


def text_to_node_spec(text: str) -> dict[str, Any] | None:
    """从「新局面 {…}」里解析出节点声明。

    :return: 声明字典；``None`` 表示这句话**不是造节点**（调用方按线索老路走）；
        空字典表示「是造节点但写砸了」——两种都要区分，因为前者不该报错。

    只认**开头**就是标记的写法：「渡口出现了新局面」这种句子里的「新局面」
    不算——那是叙述，不是指令。宁可漏认，不可错认。
    """
    import json
    import re

    raw = str(text or "").strip()
    if not raw.startswith(GROW_MARKER):
        return None
    body = raw[len(GROW_MARKER) :].strip().lstrip(":：").strip()
    if not body:
        return {}
    match = re.search(r"\{.*\}", body, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _spec_texts(value: Any) -> list[str]:
    """把声明里的一个字段读成「说法列表」（容错：单个字符串也收）。"""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list | tuple):
        items = list(value)
    else:
        items = []
    out: list[str] = []
    for item in items:
        body = str(item or "").strip()
        if body and body not in out:
            out.append(body)
    return out


def _resolve_fact(graph: ModuleGraph, ref: str) -> Fact | None:
    """按 id 或**原文**找一条已有事实。

    原文也能认，是因为 DM 手里只有玩家会知道的说法（``Fact.text``），
    没有系统 id——让他先查 id 再写，等于逼他多绕一圈。
    """
    body = str(ref or "").strip()
    if not body:
        return None
    by_id = graph.facts.get(body)
    if by_id is not None:
        return by_id
    for fact in graph.facts.values():
        if fact.text and fact.text == body:
            return fact
    return None


def _next_free(prefix: str, taken: dict[str, Any]) -> str:
    index = 1
    while f"{prefix}{index}" in taken:
        index += 1
    return f"{prefix}{index}"


def grow_node(
    graph: ModuleGraph,
    spec: dict[str, Any],
    *,
    scenes: list[str] | tuple[str, ...] = (),
    at_default: str = "",
) -> GrowResult:
    """把 DM 现场写的一个局面接进图。**过审才接，不过审原样退回。**

    过审的是两件事：

    1. **局部检查**（有针对性的报错，DM 才知道怎么改）：有名字、种类合法、
       有产出、需要的事实都存在且从入口拿得到、所在地在场景候选里；
    2. **整张图再跑一遍** :func:`validate`——生长不该把图长坏
       （节点数上限、悬空引用都在这一道兜住）。不过就**回滚**：
       新建的事实也一并撤掉，不留半成品。

    ``requires`` 只能引用**已经存在**的事实：让生长顺手造一把「前置钥匙」，
    图就会长出永远没人认领的分支。要新钥匙，让这个节点自己 ``grants``。
    """
    problems: list[str] = []
    spec = spec if isinstance(spec, dict) else {}

    # ---- 名字与种类
    title = str(spec.get("title") or "").strip()
    if not title:
        problems.append("缺 title：一个新局面得有名字（DM 与玩家都靠它称呼这件事）")
    elif any(n.title == title for n in graph.nodes.values()):
        problems.append(
            f"已经有一个叫「{title}」的局面了：同名会让 note_event 认不准，换个说法"
        )
    kind = str(spec.get("kind") or "").strip() or "clue"
    if kind not in GROW_KINDS:
        problems.append(
            f"kind「{kind}」不能现场造：可用 "
            + "、".join(GROW_KINDS)
            + "（时钟到点自己发生，不走这一套）"
        )

    # ---- 给出的事实（有产出，否则是死胡同）
    grant_refs = _spec_texts(spec.get("grants"))
    if not grant_refs:
        problems.append("缺 grants：这个局面至少要给出一件事实，否则触发了什么也没发生")
    fail_refs = _spec_texts(spec.get("on_fail"))

    # ---- 需要的事实（必须已存在、且从入口拿得到）
    require_refs = _spec_texts(spec.get("requires"))
    require_ids: list[str] = []
    for ref in require_refs:
        fact = _resolve_fact(graph, ref)
        if fact is None:
            problems.append(
                f"需要的事实「{ref}」不存在：requires 只能写已有事实"
                "（原文或 id 都认），要新钥匙让这个节点自己 grants"
            )
            continue
        if fact.id not in require_ids:
            require_ids.append(fact.id)
    if require_ids:
        have, _fired = reachable(graph)
        missing = [graph.fact_text(f) or f for f in require_ids if f not in have]
        if missing:
            problems.append(
                "前置事实从入口拿不到（这个局面永远不活）：" + "、".join(missing)
            )

    # ---- 所在地
    at = str(spec.get("at") or "").strip() or str(at_default or "").strip()
    if not at:
        problems.append("没写所在地（at），也看不出玩家眼下在哪个场景")
    elif scenes and at not in set(scenes):
        problems.append(f"所在地「{at}」不在场景候选里：" + "、".join(scenes[:12]))

    # ---- 分层（只用于展示与检查，不参与判定）
    try:
        depth = max(1, int(spec.get("depth") or 1))
    except (TypeError, ValueError):
        depth = 1

    if problems:
        return GrowResult(problems=problems)

    # ---- 建事实：写对原文的复用，写新说法的才新建
    new_facts: list[Fact] = []
    grant_ids: list[str] = []
    reused: list[str] = []
    for ref in [*grant_refs, *fail_refs]:
        fact = _resolve_fact(graph, ref)
        if fact is not None:
            if ref not in reused:
                reused.append(ref)
            if fact.id not in grant_ids:
                grant_ids.append(fact.id)
            continue
        fact = Fact(
            id=_next_free(GROW_FACT_PREFIX, graph.facts),
            text=ref,
            kind=str(spec.get("fact_kind") or "").strip() or "secret",
        )
        new_facts.append(fact)
        grant_ids.append(fact.id)

    fail_ids = [f.id for f in new_facts if f.text in set(fail_refs)]
    node = Node(
        id=_next_free(GROW_NODE_PREFIX, graph.nodes),
        title=title,
        at=at,
        kind=kind,
        requires=require_ids,
        # 新说法用新事实，写对原文的复用原钥匙（ref 可能是**原文**，不是 id）
        grants=[f.id for f in new_facts if f.text in set(grant_refs)]
        + [
            fact.id for r in grant_refs if (fact := _resolve_fact(graph, r)) is not None
        ],
        on_fail=fail_ids,
        cost=str(spec.get("cost") or "").strip(),
        depth=depth,
        hint=str(spec.get("hint") or "").strip(),
    )

    # ---- 接上，再整图过审；不过就回滚（不留半成品）
    for fact in new_facts:
        graph.facts[fact.id] = fact
    graph.nodes[node.id] = node
    graph_problems = validate(graph)
    if graph_problems:
        graph.nodes.pop(node.id, None)
        for fact in new_facts:
            graph.facts.pop(fact.id, None)
        return GrowResult(
            problems=["接上之后整张图出了问题（已回滚，没有接进图）：", *graph_problems]
        )
    return GrowResult(node=node, new_facts=new_facts, reused=reused)


# --------------------------------------------------------------------------- #
# 文本（给 DM / 玩家）
# --------------------------------------------------------------------------- #


def _fact_line(graph: ModuleGraph, fact_id: str) -> str:
    fact = graph.facts.get(fact_id)
    if fact is None:
        return fact_id
    label = FACT_KINDS.get(fact.kind, fact.kind)
    return f"[{label}] {fact.text or fact.id}"


def graph_text(
    graph: ModuleGraph,
    *,
    facts: set[str] | list[str] | None = None,
    visited: set[str] | list[str] | None = None,
    for_player: bool = False,
    max_open: int = 6,
) -> str:
    """把「当前局面」拼成一段短文本。

    **只列现在还活着的节点**，不是整张图——整张图每轮重发给模型会吃掉
    几百上千字符，而其中九成这一步用不上。

    :param for_player: 玩家侧只说他**已经知道什么**，不列还没解锁的节点
        （「还有什么没做」是 DM 的事，剧透给玩家等于把谜底写在封面上）。
    """
    have = {str(x) for x in (facts or ())}
    done = {str(x) for x in (visited or ())}
    lines: list[str] = []

    if graph.conflict.strip():
        lines.append("冲突：" + graph.conflict.strip())

    if have and not for_player:
        known = [_fact_line(graph, f) for f in graph.facts if f in have]
        if known:
            lines.append("已握有的：" + "；".join(known[:8]))
    if have and for_player:
        known = [_fact_line(graph, f) for f in graph.facts if f in have]
        if known:
            lines.append("你们已经知道：" + "；".join(known[:8]))

    if for_player:
        return "\n".join(lines).strip()

    alive = open_nodes(graph, have, done)
    if alive:
        lines.append("现在能做的：")
        for node in alive[:max_open]:
            need = ""
            if node.requires:
                need = (
                    "（要 "
                    + "、".join(graph.fact_text(f) or f for f in node.requires)
                    + "）"
                )
            where = f"@{node.at}" if node.at else ""
            label = NODE_KINDS.get(node.kind, node.kind)
            lines.append(f"· [{label}] {node.title}{where}{need}")
            if node.hint:
                lines.append(f"  {node.hint}")
            if node.cost:
                lines.append(f"  代价：{node.cost}")
    else:
        lines.append("现在没有可推进的局面。")

    if graph.clocks:
        pending = [c for c in graph.clocks if c.ready]
        if pending:
            lines.append("已经到点的：")
            for clock in pending:
                target = graph.node(clock.fires)
                title = target.title if target else clock.fires
                lines.append(f"· {clock.name} 到点了 → 「{title}」该发生了")

    conv = reached_convergences(graph, have)
    if conv:
        lines.append("已经成立的结果：" + "、".join(c.title or c.id for c in conv[:3]))
    return "\n".join(lines).strip()


def abandon_text(graph: ModuleGraph, result: AbandonResult) -> str:
    """把放弃结算拼成给 DM 的一段话。

    给的是**账目**不是判决：做过什么、还差什么、世界走到哪一步。
    「没走完」不等于「失败」，所以这里一句评价性的话都不写。
    """
    lines: list[str] = ["【这一章没走完】"]
    if result.done:
        lines.append("做过的事：" + "、".join(result.done[-6:]))
    else:
        # 「什么都没做」是一个有意义的状态，值得单独说一句：
        # 否则下面列一堆缺口，读起来像「你们做了一堆但都差一点」，不准确。
        lines.append("什么都没做成——你们没往里走。")
    if result.settled:
        lines.append(
            "已经成立的结果：" + "、".join(c.title or c.id for c in result.settled)
        )
    for conv, missing in result.unmet[:3]:
        gap = "、".join(graph.fact_text(f) or f for f in missing)
        lines.append(f"「{conv.title or conv.id}」还差：{gap}")
    for clock in result.due_clocks[:3]:
        target = graph.node(clock.fires)
        name = target.title if target else clock.fires
        lines.append(f"而「{clock.name}」到点了 → 「{name}」该发生了")
    return "\n".join(lines)


def verdict_text(graph: ModuleGraph, conv: Convergence) -> str:
    """一个收束的 DM 侧描述。"""
    tiers = conv.tiers()
    cond = " 或 ".join(
        "＋".join(graph.fact_text(f) or f for f in tier) for tier in tiers
    )
    kind = CONVERGENCE_KINDS.get(conv.kind, conv.kind)
    lines = [f"【{kind}】{conv.title}", f"成立条件：{cond}"]
    if conv.outcome:
        lines.append("结果：" + conv.outcome)
    return "\n".join(lines)
