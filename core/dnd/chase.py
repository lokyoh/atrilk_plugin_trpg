"""DMG 2024 第三章「追逐」状态机（纯规则层：不碰存档、不 import agents）。

原文见 ``参考/城主指南 (2024).md`` L1787-1884，数值在 ``res/dnd/chase.yaml``。

## 为什么单独开一层

战斗的先攻序列是「打」，追逐的先攻序列是「跑」。两者都按顺序轮转，但追逐
记的是**距离、疾走次数、力竭**，不是 HP / 动作 / 反应。硬塞进 ``CombatState``
会给每个 ``Combatant`` 加一串只有追逐才用的字段，还会让「战斗中能不能疾走」
变成同一个字段的两种含义。所以：追逐是**独立的一场**，状态自己存。

## 尺度上的两处简化（如实登记）

1. 原文的距离是「每对猎物 ↔ 猎手」分别跟踪，分头行动时各算各的。这里只记
   **一条群体距离**，头领 = 先攻序列里最靠前的在追逐者。要跑分组追逐就开
   第二场，别把两组塞进一个状态。
2. 复杂状况的 **1d12 由系统掷**，但状况里要求的豁免 / 伤害 / 状态由 DM 用
   ``check`` / ``attack`` / ``update_character`` 裁定：追逐状态里没有 HP / AC，
   不为了「自动化」再造一套战斗系统。原文规定状况影响**下一位**参与者，
   这里会点名是谁。

## 谁看得到

``format_chase`` 是 DM 侧的账本回执。项目硬规矩：DM 功能调用结果只有 DM
看得到，所以距离 / 力竭 / 谁逃脱了要由 DM 显式写进叙述，玩家才会知道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..trpg.check import roll_check
from ..trpg.dice import Advantage, roll
from .datafiles import load_mapping

__all__ = [
    "CHASE_TERRAINS",
    "ChaseParticipant",
    "ChaseState",
    "advance_turn",
    "chase_help",
    "complication_for",
    "dash_limit",
    "escape_advantage",
    "escape_phase",
    "format_chase",
    "format_chase_rules",
    "format_complication_table",
    "move_chase",
    "roll_complication",
    "start_chase",
    "take_dash",
]

_TABLES = load_mapping("chase")
_DASH = _TABLES["dash"][0]
DASH_BASE = int(_DASH["base"])
DASH_MINIMUM = int(_DASH["minimum"])
DASH_SAVE_DC = int(_DASH["extra_save_dc"])
EXHAUSTION_EXIT = int(_DASH["exhaustion_exit"])
ESCAPE_FACTORS = tuple(_TABLES["escape_factors"])
_RULES = tuple(_TABLES["rules"])
CHASE_TERRAINS = ("城市", "荒野")

PREY = "prey"
HUNTER = "hunter"
_SIDE_CN = {PREY: "被追捕者", HUNTER: "追击者"}


def dash_limit(con_mod: int) -> int:
    """可免费疾走的次数 = 3 + 体质调整值（至少 1）。"""
    return max(DASH_MINIMUM, DASH_BASE + int(con_mod or 0))


def escape_advantage(factors: Any) -> Advantage:
    """按《逃脱因素》表算优势 / 劣势；两边都有则互相抵消。

    ``factors`` 写表里 ``factor`` 列的词（也认「优势 / 劣势」本身，方便 DM 直接
    给个裁定）。认不出的词忽略——DM 可以按原文「其他因素由你决定」自行调，
    但别让一个错别字把检定姿态弄反。
    """
    named = {str(row["factor"]): str(row["effect"]) for row in ESCAPE_FACTORS}
    adv = dis = 0
    for raw in factors or ():
        word = str(raw).strip()
        effect = named.get(word, word)
        if effect == "优势":
            adv += 1
        elif effect == "劣势":
            dis += 1
    if adv and not dis:
        return Advantage.ADVANTAGE
    if dis and not adv:
        return Advantage.DISADVANTAGE
    return Advantage.NORMAL


def _complication_rows(terrain: str) -> tuple[dict[str, Any], ...]:
    key = "wilderness_complications" if terrain == "荒野" else "city_complications"
    return tuple(_TABLES[key])


def complication_for(terrain: str, value: int) -> dict[str, Any]:
    """按 1d12 点数取一条复杂状况；7–12 一律是「没有发生」。"""
    number = int(value)
    for row in _complication_rows(terrain):
        label = str(row["roll"])
        if label.isdigit():
            hit = number == int(label)
        else:
            low, _, high = label.partition("-")
            hit = int(low) <= number <= int(high or low)
        if hit:
            return row
    return {"roll": number, "text": "没有发生任何复杂状况。"}


def _describe_complication(row: dict[str, Any]) -> str:
    text = str(row.get("text") or "没有发生任何复杂状况。")
    tags = []
    if row.get("save"):
        tags.append(f"豁免 DC {row['dc']} {row['save']}")
    if row.get("damage"):
        tags.append(f"伤害 {row['damage']}")
    return text + ("【" + "；".join(tags) + "】" if tags else "")


@dataclass(slots=True)
class ChaseParticipant:
    """一名追逐参与者。

    ``passive_perception`` 只对追击者有意义；``stealth_mod`` / ``left_sight``
    只对被追捕者有意义。两者都存是为了 ``to_dict`` / ``from_dict`` 对称。
    """

    cid: str = ""
    name: str = ""
    side: str = PREY
    initiative: int = 10
    speed: int = 30
    con_mod: int = 0
    stealth_mod: int = 0
    passive_perception: int = 10
    dash_used: int = 0
    exhaustion: int = 0
    out: bool = False
    left_sight: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cid": self.cid,
            "name": self.name,
            "side": self.side,
            "initiative": self.initiative,
            "speed": self.speed,
            "con_mod": self.con_mod,
            "stealth_mod": self.stealth_mod,
            "passive_perception": self.passive_perception,
            "dash_used": self.dash_used,
            "exhaustion": self.exhaustion,
            "out": self.out,
            "left_sight": self.left_sight,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChaseParticipant:
        return cls(
            cid=str(data.get("cid") or ""),
            name=str(data.get("name") or ""),
            side=str(data.get("side") or PREY),
            initiative=int(data.get("initiative") or 0),
            speed=int(data.get("speed") or 30),
            con_mod=int(data.get("con_mod") or 0),
            stealth_mod=int(data.get("stealth_mod") or 0),
            passive_perception=int(data.get("passive_perception") or 10),
            dash_used=int(data.get("dash_used") or 0),
            exhaustion=int(data.get("exhaustion") or 0),
            out=bool(data.get("out")),
            left_sight=bool(data.get("left_sight")),
            note=str(data.get("note") or ""),
        )

    @property
    def side_cn(self) -> str:
        return _SIDE_CN.get(self.side, self.side)

    def status(self) -> str:
        tags = [self.side_cn]
        if self.exhaustion:
            tags.append(f"力竭 {self.exhaustion} 级")
        if self.out:
            tags.append("已退出")
        if self.note:
            tags.append(self.note)
        return " / ".join(tags)


@dataclass(slots=True)
class ChaseState:
    """一场追逐的状态。``turn`` 是 :attr:`order` 的下标，不是参与者编号。"""

    terrain: str = "城市"
    distance: int = 30
    round_no: int = 1
    turn: int = 0
    participants: list[ChaseParticipant] = field(default_factory=list)
    ended: bool = False
    result: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "terrain": self.terrain,
            "distance": self.distance,
            "round_no": self.round_no,
            "turn": self.turn,
            "participants": [p.to_dict() for p in self.participants],
            "ended": self.ended,
            "result": self.result,
            "notes": list(self.notes[-20:]),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChaseState:
        return cls(
            terrain=str(data.get("terrain") or "城市"),
            distance=int(data.get("distance") or 0),
            round_no=max(1, int(data.get("round_no") or 1)),
            turn=int(data.get("turn") or 0),
            participants=[
                ChaseParticipant.from_dict(p)
                for p in (data.get("participants") or [])
                if isinstance(p, dict)
            ],
            ended=bool(data.get("ended")),
            result=str(data.get("result") or ""),
            notes=list(data.get("notes") or []),
        )

    @property
    def order(self) -> list[ChaseParticipant]:
        """先攻序列（降序；已退出者不占顺序）。"""
        active = [p for p in self.participants if not p.out]
        return sorted(active, key=lambda p: (-p.initiative, p.name))

    def current(self) -> ChaseParticipant | None:
        order = self.order
        if not order:
            return None
        return order[self.turn % len(order)]

    def find(self, ref: str) -> ChaseParticipant | None:
        key = str(ref or "").strip()
        for p in self.participants:
            if key and (key == p.cid or key == p.name):
                return p
        return None

    def active(self, side: str) -> list[ChaseParticipant]:
        return [p for p in self.participants if p.side == side and not p.out]

    @property
    def leader(self) -> ChaseParticipant | None:
        """领头追击者 = 先攻序列里最靠前的在追逐者（群体距离的简化口径）。"""
        for p in self.order:
            if p.side == HUNTER:
                return p
        return None

    def log(self, text: str) -> None:
        self.notes.append(text)
        del self.notes[:-20]


def start_chase(
    terrain: str,
    distance: int,
    participants: list[ChaseParticipant],
) -> ChaseState:
    """开一场追逐；至少各有一名被追捕者与追击者，否则抛 ``ValueError``。"""
    kind = "荒野" if str(terrain).strip() == "荒野" else "城市"
    if not any(p.side == PREY for p in participants) or not any(
        p.side == HUNTER for p in participants
    ):
        raise ValueError("一场追逐需要至少一个被追捕者 + 至少一个追击者")
    return ChaseState(
        terrain=kind,
        distance=max(0, int(distance or 0)),
        participants=list(participants),
    )


def take_dash(
    state: ChaseState, ref: str, *, rng: Any = None, extra_distance: int | None = None
) -> str:
    """执行一次疾走：按速度拉开 / 缩短距离，并记免费次数与额外疾走的力竭。

    原文「参与者都会强烈地希望在每一轮里执行疾走动作」，所以这里把**移动**
    和**记账**合成一步；不疾走的移动用 :func:`move_chase`。超过免费次数时，
    在自己回合结束时过 DC 10 体质豁免，失败 +1 力竭，力竭 5 级被迫退出。
    """
    actor = state.find(ref)
    if actor is None:
        return f"追逐里没有「{ref}」这个人"
    if actor.out:
        return f"{actor.name} 已经退出追逐"
    if state.ended:
        return f"追逐已经结束（{state.result}）"
    if actor.exhaustion >= EXHAUSTION_EXIT:
        # 原文：「若某名参与者的力竭等级为 5，其被迫退出追逐」——
        # 不要求这级力竭一定来自本次疾走（DM 也可能用 check / update_character 记）。
        actor.out = True
        actor.note = f"力竭 {actor.exhaustion} 级，被迫退出"
        _check_all_prey_gone(state)
        return f"【{actor.name}】力竭 {actor.exhaustion} 级，**被迫退出追逐**。"

    feet = actor.speed if extra_distance is None else max(0, int(extra_distance))
    actor.dash_used += 1
    limit = dash_limit(actor.con_mod)
    text = f"【{actor.name}】疾走 {feet} 尺（第 {actor.dash_used}/{limit} 次免费疾走）。"
    text += _apply_distance(state, actor, feet)

    if actor.dash_used > limit:
        check = roll_check(
            actor.con_mod, DASH_SAVE_DC, kind="额外疾走（体质）", rng=rng
        )
        if check.success:
            text += f" 超出免费次数，DC {DASH_SAVE_DC} 体质豁免 {check.describe()} → 撑住了。"
        else:
            actor.exhaustion += 1
            text += (
                f" 超出免费次数，DC {DASH_SAVE_DC} 体质豁免 {check.describe()}"
                f" → 力竭 {actor.exhaustion} 级。"
            )
            if actor.exhaustion >= EXHAUSTION_EXIT:
                actor.out = True
                actor.note = f"力竭 {actor.exhaustion} 级，被迫退出"
                text += f" 力竭 {EXHAUSTION_EXIT} 级，**被迫退出追逐**。"
                if not state.ended:
                    _check_all_prey_gone(state)
    state.log(text)
    return text


def _apply_distance(state: ChaseState, actor: ChaseParticipant, feet: int) -> str:
    """把一段移动折进群体距离；追上（0 尺）即捕获。"""
    if actor.side == HUNTER:
        state.distance = max(0, state.distance - feet)
        direction = "拉近"
    else:
        state.distance += feet
        direction = "拉开"
    if state.distance == 0:
        state.ended = True
        prey_names = "、".join(p.name for p in state.active(PREY)) or "目标"
        state.result = f"追击者追至 0 尺，捕获了 {prey_names}"
        return f" 距离{direction}到 0 尺 → **捕获，追逐结束**（{state.result}）。"
    return f" 距离{direction}到 {state.distance} 尺。"


def move_chase(state: ChaseState, ref: str, feet: int) -> str:
    """不疾走的移动（逃跑者停下施法、追击者绕路等），只改距离不记疾走。"""
    actor = state.find(ref)
    if actor is None:
        return f"追逐里没有「{ref}」这个人"
    if actor.out:
        return f"{actor.name} 已经退出追逐"
    if state.ended:
        return f"追逐已经结束（{state.result}）"
    distance = max(0, int(feet or 0))
    text = f"【{actor.name}】移动 {distance} 尺。" + _apply_distance(
        state, actor, distance
    )
    state.log(text)
    return text


def roll_complication(state: ChaseState, *, rng: Any = None) -> str:
    """回合末掷 1d12：状况影响**下一位**参与者，不是掷骰的这位。"""
    if state.ended:
        return f"追逐已经结束（{state.result}）"
    number = roll("1d12", rng=rng).total
    row = complication_for(state.terrain, number)
    order = state.order
    current = state.current()
    if not order or current is None:
        return "追逐里没有还在跑的人了"
    index = state.turn % len(order)
    nxt = order[(index + 1) % len(order)]
    caster = order[index].name
    if str(row.get("text") or "").startswith("没有发生"):
        text = f"{caster} 回合末 1d12={number}：没有发生任何复杂状况。"
    else:
        text = (
            f"{caster} 回合末 1d12={number}（{state.terrain}）→"
            f" 影响下一位 **{nxt.name}**：" + _describe_complication(row)
        )
    state.log(text)
    return text


def escape_phase(
    state: ChaseState,
    *,
    rng: Any = None,
    factors: Any = (),
    round_no: int | None = None,
) -> str:
    """一轮结束（先攻归 0 且全员行动过）后的逃脱检定。

    每位被追捕者各掷一次敏捷（隐匿）：从未离开领头追击者视线 → 自动失败；
    否则与追击者中**最高的被动察觉**比较，检定总和**大于**它即逃脱。
    ``roll_check`` 的语义是 ``total >= dc``，所以这里把 DC 设成
    ``最高被动察觉 + 1``，等价于原文的「大于」。
    """
    hunters = state.active(HUNTER)
    preys = state.active(PREY)
    if state.ended:
        return f"追逐已经结束（{state.result}），不再做逃脱检定"
    if not preys:
        return "没有还在跑的被追捕者了"
    if not hunters:
        state.ended = True
        state.result = "追击者全部退出，被追捕者逃脱"
        return f"**{state.result}**"
    best_pp = max(h.passive_perception for h in hunters)
    advantage = escape_advantage(factors)
    finished_round = state.round_no if round_no is None else round_no
    lines = [
        f"第 {finished_round} 轮结束：逃脱检定（对抗最高被动察觉 {best_pp}；"
        f"原文是「大于」，判定 DC 记作 {best_pp + 1}）"
    ]
    for prey in preys:
        if not prey.left_sight:
            lines.append(
                f"【{prey.name}】从未离开领头追击者 {state.leader.name if state.leader else ''} 的视线 → 自动失败，追逐继续。"
            )
            continue
        check = roll_check(
            prey.stealth_mod,
            best_pp + 1,
            kind="逃脱（敏捷·隐匿）",
            advantage=advantage,
            rng=rng,
        )
        if check.success:
            prey.out = True
            prey.note = "逃脱成功"
            lines.append(f"【{prey.name}】{check.describe()} > {best_pp} → **逃脱成功**（退出追逐）。")
        else:
            lines.append(f"【{prey.name}】{check.describe()} → 没甩掉，追逐继续。")
    if not state.active(PREY):
        state.ended = True
        state.result = "所有被追捕者都已逃离"
        lines.append(f"**{state.result}，追逐结束。**")
    text = "\n".join(lines)
    state.log(text)
    return text


def _check_all_prey_gone(state: ChaseState) -> None:
    """力竭退场的是被追捕者时，追逐同样结束。"""
    if not state.active(PREY):
        state.ended = True
        state.result = "被追捕者力竭退出，追逐结束"


def advance_turn(state: ChaseState, *, rng: Any = None, factors: Any = ()) -> str:
    """推进一回合；走完整轮时接一次逃脱检定（原文：先攻归 0 且全员行动过）。"""
    if state.ended:
        return f"追逐已经结束（{state.result}）"
    order = state.order
    if not order:
        return "追逐里没有还在跑的人了"
    state.turn += 1
    if state.turn < len(order):
        return f"轮到 {order[state.turn].name}（第 {state.round_no} 轮）。"
    state.turn = 0
    finished_round = state.round_no
    state.round_no += 1
    escape = escape_phase(state, rng=rng, factors=factors, round_no=finished_round)
    return f"第 {finished_round} 轮全员行动完毕。\n{escape}"


def format_chase(state: ChaseState) -> str:
    """DM 侧账本卡（玩家须知请显式写进叙述）。"""
    lines = [
        f"【追逐】{state.terrain} · 第 {state.round_no} 轮 · 距离 {state.distance} 尺"
        + (f" · **已结束：{state.result}**" if state.ended else ""),
        f"领头追击者：{state.leader.name if state.leader else '无'}",
    ]
    order = state.order
    current = state.current()
    for p in order:
        marker = "→ " if current is not None and p.cid == current.cid else "   "
        lines.append(
            f"{marker}{p.name}（先攻 {p.initiative}）：{p.status()}，"
            f"疾走 {p.dash_used}/{dash_limit(p.con_mod)}，速度 {p.speed} 尺"
        )
    for p in state.participants:
        if p.out:
            lines.append(f"   {p.name}：{p.status()}")
    if current is not None and not state.ended:
        lines.append(f"当前回合：{current.name}；回合末掷 1d12 意外（影响下一位）。")
    return "\n".join(lines)


def format_chase_rules(name: str = "") -> str:
    """规则原文（供 ``query_data(table=hazard, name=追逐)`` 用，不进提示词）。"""
    key = str(name or "").strip()
    if key in ("城市", "荒野", "城市意外", "荒野意外"):
        return format_complication_table("荒野" if key.startswith("荒野") else "城市")
    if key and key not in ("追逐", "全部", "所有"):
        for row in _RULES:
            if row["key"] == key:
                return f"【追逐·{row['key']}】{row['text']}"
        rows = [r for r in ESCAPE_FACTORS if key in str(r["factor"])]
        if rows:
            return "【逃脱因素】" + "；".join(
                f"{r['factor']} → {r['effect']}" for r in rows
            )
        return ""
    lines = ["《城主指南 (2024)》第三章·追逐（L1787-1884）"]
    lines.extend(f"- {row['key']}：{row['text']}" for row in _RULES)
    lines.append(
        "- 逃脱因素：" + "；".join(
            f"{r['factor']} → {r['effect']}" for r in ESCAPE_FACTORS
        )
    )
    lines.append(
        "- 复杂状况：城市 / 荒野各一张 1d12 表（1–6 有状况，7–12 没有）；"
        "写「追逐 意外」掷，写「追逐 规则 城市」可看完整表。"
    )
    return "\n".join(lines)


def chase_help() -> str:
    """子命令帮助（只在 DM 写「追逐」不带参数时返回，不占提示词预算）。"""
    return "\n".join(
        [
            "【追逐】子命令（写在 tactic 的 action 里）：",
            "  追逐 开始 城市|荒野 距离 —— 开一场；参与者用 target 写「名字:猎物,名字:追击者」",
            "  追逐 状态 —— 看距离 / 先攻 / 疾走 / 力竭（DM 账本，玩家须知要写进叙述）",
            "  追逐 疾走 [名字] —— 记一次疾走并按速度改距离；超出免费次数自动掷 DC10 体质",
            "  追逐 移动 尺数 [名字] —— 不疾走的移动，只改距离",
            "  追逐 意外 —— 回合末掷 1d12，点名影响下一位参与者",
            "  追逐 下一回合 —— 推进先攻；走完整轮自动做逃脱检定",
            "  追逐 视线 名字 —— 标记该被追捕者已离开领头追击者视线（否则逃脱自动失败）",
            "  追逐 逃脱 [因素] —— 立刻做一轮逃脱检定（因素如「许多可以躲藏的东西」）",
            "  追逐 加入 名字:猎物|追击者 —— 中途加入（角色反转 / 分头行动）",
            "  追逐 规则 [开始|疾走|攻击|结束|逃脱|意外|城市|荒野] —— 读原文",
            "  追逐 结束 [原因] —— 收掉这场",
            "⚠️ 项目硬规矩：这些回执只有 DM 看得到，距离 / 力竭 / 逃脱要显式写进叙述。",
        ]
    )


def format_complication_table(terrain: str) -> str:
    """完整的 1d12 表（DM 查表用）。"""
    kind = "荒野" if terrain == "荒野" else "城市"
    lines = [f"【{kind}追逐复杂状况】1d12"]
    for row in _complication_rows(kind):
        lines.append(f"  {row['roll']}：{row['text']}")
    return "\n".join(lines)
