"""《城主指南 (2024)》第八章「据点」：设施台账、据点回合与据点事件（纯函数层）。

## 这里是干什么的

角色 5 级起获得一个据点。据点里有两类设施：**基础设施**（卧室 / 厨房，纯装饰）
与**特色设施**（铁匠铺 / 图书馆 / 兵营，能下指令产生收益）。游戏内每 7 天一次
**据点回合**：玩家给特色设施下达指令，或对整个据点下达维护指令——**只有维护
指令**才会触发据点事件（1d100）。

## 数值的唯一真相

- 29 条特色设施的等级 / 空间 / 雇员 / 指令、11 段据点事件、宝藏与客人等子表、
  尺寸与工期 —— ``res/dnd/bastion.yaml``（``load_mapping`` 分段表）。
- 掷魔法物品复用 :func:`..dm_tables.roll_treasure`（第七章 20 张 1d100 表），
  掷艺术品复用 :func:`..dm_tables.roll_art`。这里不再抄一份。

## 边界：哪些是程序算、哪些交 DM

程序算的是**能掷骰、能记账**的部分：设施等级与数量上限、空间与雇员数、
指令是否合法、忙碌状态、据点卫兵 / 副官台账、事件里的骰池与金钱、
军械库把 6d6 改 1d8、副官减骰、请求援助的和值、赌场收益表。

交 DM 的是**叙事与裁定**：设施的具体增益（秘学护咒、临时生命、豁免优势）、
研究出什么情报、制造出哪件物品、事件怎么发生、先决条件是否满足
（卡片上没有「法器熟练」这类字段，程序只把先决原文显示出来）。

不碰磁盘、不 import agents；序列化 ``to_dict / from_dict`` 手写两边对齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..trpg.dice import roll
from .datafiles import load_mapping

__all__ = [
    "BASIC_FACILITIES",
    "EVENT_MIN",
    "FACILITIES",
    "ORIENT_WORDS",
    "SIZES",
    "Bastion",
    "BastionFacility",
    "EventResult",
    "TurnResult",
    "add_basic",
    "add_special",
    "bastion_help",
    "bastion_rules",
    "constant",
    "expand_special",
    "facility_names",
    "find_event",
    "format_bastion",
    "format_bastion_query",
    "format_event_table",
    "format_facility",
    "format_facility_list",
    "get_facility",
    "is_lost",
    "new_bastion",
    "next_turn",
    "order_options",
    "remove_special",
    "resolve_event",
    "roll_event",
    "set_order",
    "slots_for",
    "space_row",
]

_TABLES = load_mapping("bastion")

#: 尺寸：``key -> 行``（cramped / roomy / vast）
SIZES: dict[str, dict[str, Any]] = {
    str(row["key"]): row for row in _TABLES.get("size", ())
}

#: 设施名 -> 元数据行
FACILITIES: dict[str, dict[str, Any]] = {
    str(row["cn"]): row for row in _TABLES.get("facility", ())
}

#: 基础设施名（纯装饰，没有指令）
BASIC_FACILITIES: tuple[str, ...] = tuple(
    str(row["cn"]) for row in _TABLES.get("basic", ())
)

#: 特色设施数量表：``(等级, 数量)``，按等级升序
_SLOTS: tuple[tuple[int, int], ...] = tuple(
    (int(row["level"]), int(row["count"])) for row in _TABLES.get("slot", ())
)

_EVENTS: tuple[dict[str, Any], ...] = tuple(_TABLES.get("event", ()))
_CONSTANT: dict[str, Any] = {
    str(row["key"]): row for row in _TABLES.get("constant", ())
}

#: 据点事件表的下界（原文 1d100）
EVENT_MIN = 1

#: 指令：空间里可下的七种（维护是全局的，不进设施）
ORDERS = ("制造", "增强", "收获", "研究", "招募", "贸易")
MAINTAIN = "维护"

#: 需要跨回合完成的指令（本回合下达，下回合才能领）
_LONG_ORDERS = ("制造", "收获")

#: 「在据点 / 不在据点」的写法
ORIENT_WORDS = {
    "在": True,
    "据点内": True,
    "在据点": True,
    "守": True,
    "不在": False,
    "外出": False,
    "离开": False,
    "路上": False,
    "旅行": False,
}


# --------------------------------------------------------------------------- #
# 数据表查询
# --------------------------------------------------------------------------- #


def constant(key: str, default: int = 0) -> int:
    """取 ``constant`` 段里的一个数（解锁等级、回合天数、扩建价…）。"""
    row = _CONSTANT.get(key)
    if row is None:
        return default
    try:
        return int(row.get("value") or default)
    except (TypeError, ValueError):
        return default


def space_row(key: str) -> dict[str, Any] | None:
    return SIZES.get(str(key or "").strip())


def space_key(name: str) -> str:
    """把「狭窄 / 宽敞 / 庞大 / roomy」归一成 key；认不出返回空串。"""
    text = str(name or "").strip().lower()
    for key, row in SIZES.items():
        if text in (key, str(row.get("cn") or "").lower()):
            return key
    return ""


def space_cn(key: str) -> str:
    row = SIZES.get(str(key or ""))
    return str(row.get("cn") or key) if row else str(key or "")


def get_facility(name: str) -> dict[str, Any] | None:
    """按中文名 / 英文名查设施；认不出返回 ``None``（不猜）。"""
    text = str(name or "").strip()
    if not text:
        return None
    if text in FACILITIES:
        return FACILITIES[text]
    low = text.lower()
    for row in FACILITIES.values():
        if str(row.get("en") or "").lower() == low:
            return row
    return None


def slots_for(level: int) -> int:
    """该等级能拥有的特色设施数量（5→2 / 9→4 / 13→5 / 17→6，低于 5 级为 0）。"""
    count = 0
    for need, got in _SLOTS:
        if int(level) >= need:
            count = got
    return count


def facility_names(level: int = 20) -> tuple[str, ...]:
    """该等级可选的特色设施名（只按等级过滤；先决由 DM 判）。"""
    return tuple(
        str(row["cn"])
        for row in FACILITIES.values()
        if int(row.get("level") or 0) <= int(level)
    )


def order_options(name: str) -> tuple[str, ...]:
    row = get_facility(name)
    if row is None:
        return ()
    return tuple(str(o) for o in (row.get("orders") or ()))


def _order_key(word: str) -> str:
    """把指令写法归一（「制造工具」→ 制造）。认不出返回原样。"""
    text = str(word or "").strip()
    for order in (*ORDERS, MAINTAIN):
        if text.startswith(order):
            return order
    return text


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #


@dataclass
class BastionFacility:
    """据点里的一座特色设施。"""

    key: str
    #: cramped / roomy / vast
    space: str = "roomy"
    #: 本据点回合下过的指令，空 = 没下
    order: str = ""
    #: 还要多少回合才完成（制造 / 收获这类跨回合的指令）
    busy_turns: int = 0
    #: 正在做什么（「制造：铁匠工具」），完成时回显
    busy_note: str = ""
    #: 是否已扩建为庞大
    expanded: bool = False
    #: 雇员是否齐全（事件可能夺走雇员，下个回合恢复）
    staffed: bool = True
    #: 增益到期回合（增强指令 / 护咒），0 = 无
    buff_until: int = 0
    #: 增益说明（「训练场·武器专家」），给 DM 回看
    buff_note: str = ""

    @property
    def cn(self) -> str:
        return self.key

    def row(self) -> dict[str, Any]:
        return FACILITIES.get(self.key, {})

    def staff(self) -> int:
        table = self.row().get("staff") or {}
        try:
            return int(table.get(self.space) or 0)
        except (TypeError, ValueError):
            return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "space": self.space,
            "order": self.order,
            "busy_turns": self.busy_turns,
            "busy_note": self.busy_note,
            "expanded": self.expanded,
            "staffed": self.staffed,
            "buff_until": self.buff_until,
            "buff_note": self.buff_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BastionFacility:
        return cls(
            key=str(data.get("key") or ""),
            space=str(data.get("space") or "roomy"),
            order=str(data.get("order") or ""),
            busy_turns=int(data.get("busy_turns") or 0),
            busy_note=str(data.get("busy_note") or ""),
            expanded=bool(data.get("expanded")),
            staffed=bool(data.get("staffed", True)),
            buff_until=int(data.get("buff_until") or 0),
            buff_note=str(data.get("buff_note") or ""),
        )


@dataclass
class Bastion:
    """一个角色的据点。"""

    #: 主人 user_id
    owner: str = ""
    name: str = "据点"
    #: 基础设施：``{"key": 名字, "space": 尺寸}``
    basic: list[dict[str, str]] = field(default_factory=list)
    facilities: list[BastionFacility] = field(default_factory=list)
    #: 据点卫兵数（兵营招募 / 事件扣减）
    defenders: int = 0
    #: 副官数（作战指挥室；使攻击事件骰数 −1）
    officers: int = 0
    #: 已执行的据点回合数
    turn: int = 0
    #: 连续没下任何指令的回合数（疏于照顾的计数器；判定交 DM）
    idle_turns: int = 0
    #: 军械库是否已补货（补货后攻击事件改投 1d8，事件后失效）
    armory: bool = False
    #: 本回合是否已下维护指令
    maintained: bool = False
    #: 冥想室增强：下次掷事件掷两次取一
    reroll_event: bool = False
    #: 据点的记事
    log: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- 查询

    def find(self, name: str) -> BastionFacility | None:
        text = str(name or "").strip()
        if not text:
            return None
        return next((f for f in self.facilities if f.key == text), None)

    def has(self, name: str) -> bool:
        return self.find(name) is not None

    def slots(self, level: int) -> tuple[int, int]:
        """``(已用, 上限)``。"""
        return len(self.facilities), slots_for(level)

    def defender_cap(self) -> int | None:
        """据点卫兵容量 = 各兵营容量之和；没有兵营返回 ``None``（不限制）。"""
        total = 0
        found = False
        for fac in self.facilities:
            if fac.key != "兵营":
                continue
            found = True
            total += constant(
                "barrack_vast" if fac.space == "vast" else "barrack_roomy", 12
            )
        return total if found else None

    def gold_armory_cost(self) -> int:
        """军械库补货花费：100 + 每名卫兵 100；据点有铁匠铺则减半。"""
        base = 100 + 100 * max(0, self.defenders)
        return base // 2 if self.has("铁匠铺") else base

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "basic": [dict(row) for row in self.basic],
            "facilities": [f.to_dict() for f in self.facilities],
            "defenders": self.defenders,
            "officers": self.officers,
            "turn": self.turn,
            "idle_turns": self.idle_turns,
            "armory": self.armory,
            "maintained": self.maintained,
            "reroll_event": self.reroll_event,
            "log": list(self.log),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Bastion:
        return cls(
            owner=str(data.get("owner") or ""),
            name=str(data.get("name") or "据点"),
            basic=[
                {"key": str(r.get("key") or ""), "space": str(r.get("space") or "roomy")}
                for r in (data.get("basic") or ())
                if isinstance(r, dict)
            ],
            facilities=[
                BastionFacility.from_dict(r)
                for r in (data.get("facilities") or ())
                if isinstance(r, dict)
            ],
            defenders=int(data.get("defenders") or 0),
            officers=int(data.get("officers") or 0),
            turn=int(data.get("turn") or 0),
            idle_turns=int(data.get("idle_turns") or 0),
            armory=bool(data.get("armory")),
            maintained=bool(data.get("maintained")),
            reroll_event=bool(data.get("reroll_event")),
            log=[str(x) for x in (data.get("log") or ())],
        )


def new_bastion(owner: str = "", name: str = "据点") -> Bastion:
    return Bastion(owner=str(owner or ""), name=str(name or "据点"))


# --------------------------------------------------------------------------- #
# 设施增删
# --------------------------------------------------------------------------- #


def add_special(bastion: Bastion, name: str, space: str, level: int) -> str:
    """加入一座特色设施：校验等级、先决提示、数量上限与空间。"""
    row = get_facility(name)
    if row is None:
        return (
            f"没有「{name}」这个特色设施。可用：\n"
            + "、".join(facility_names(level))
        )
    key = str(row["cn"])
    if bastion.has(key):
        return f"据点里已经有{key}了（每个特色设施只能选一次，除非描述另有说明）"
    need = int(row.get("level") or 0)
    if int(level) < need:
        return f"{key}要到 {need} 级才能获取（当前 {level} 级）"
    used, cap = bastion.slots(level)
    if used >= cap:
        return (
            f"特色设施已满（{used}/{cap}）。数量按等级给："
            + " / ".join(f"{lv} 级 {n} 座" for lv, n in _SLOTS)
            + "；升级时才能再加，或把一座换成另一座"
        )
    allowed = [str(s) for s in (row.get("spaces") or ())]
    wanted = space_key(space) or (allowed[0] if allowed else "roomy")
    if wanted not in allowed:
        return (
            f"{key}的空间只能是"
            + " / ".join(space_cn(s) for s in allowed)
            + f"，不能是{space_cn(wanted)}"
        )
    bastion.facilities.append(BastionFacility(key=key, space=wanted))
    pre = str(row.get("pre") or "无")
    text = f"已加入特色设施：{key}（{space_cn(wanted)}，雇员 {_staff_text(row, wanted)}）"
    if pre and pre != "无":
        text += f"\n先决条件（原文）：{pre} —— 由你判定这张卡是否满足，系统不校验。"
    text += f"\n特色设施 {used + 1}/{cap}。"
    return text


def remove_special(bastion: Bastion, name: str) -> str:
    """换掉一座特色设施（升级时可以换；这里只做台账动作）。"""
    fac = bastion.find(name)
    if fac is None:
        return f"据点里没有「{name}」"
    bastion.facilities.remove(fac)
    bastion.log.append(f"移除设施：{fac.key}")
    return f"已移除{fac.key}。空出 1 个特色设施位，可以再选一座（仍要满足等级与先决）。"


def add_basic(bastion: Bastion, name: str, space: str) -> str:
    """增添基础设施：基础设施**要花钱和时间**（与特色设施不同）。"""
    text = str(name or "").strip()
    if text not in BASIC_FACILITIES:
        return f"基础设施只有：{'、'.join(BASIC_FACILITIES)}"
    key = space_key(space) or "cramped"
    row = SIZES.get(key) or {}
    gp = int(row.get("build_gp") or 0)
    days = int(row.get("build_days") or 0)
    bastion.basic.append({"key": text, "space": key})
    bastion.log.append(f"增添基础设施：{text}（{space_cn(key)}）")
    return (
        f"已增添基础设施：{text}（{space_cn(key)}，{gp} GP，{days} 天）。"
        f"\n⚠️ 钱要用 update_character 的「钱」字段扣（-{gp} 金）；"
        f"工期 {days} 天由你按世界时间推进，系统不自动跳时间。"
    )


def expand_special(bastion: Bastion, name: str) -> str:
    """把特色设施扩建为庞大：固定 2000 GP，原文没给工期。"""
    fac = bastion.find(name)
    if fac is None:
        return f"据点里没有「{name}」"
    row = fac.row()
    allowed = [str(s) for s in (row.get("spaces") or ())]
    if "vast" not in allowed:
        return f"{fac.key}不是可扩建的设施（它的描述里没写可以扩大为庞大）"
    if fac.space == "vast":
        return f"{fac.key}已经是庞大了"
    gp = constant("special_expand_gp", 2000)
    fac.space = "vast"
    fac.expanded = True
    bastion.log.append(f"扩建：{fac.key} → 庞大")
    return (
        f"{fac.key}已扩建为庞大（雇员 {fac.staff()} 名）。"
        f"\n⚠️ 记着用 update_character 扣 {gp} 金；原文没给工期，视为立即完成。"
    )


def _staff_text(row: dict[str, Any], space: str) -> str:
    table = row.get("staff") or {}
    try:
        return str(int(table.get(space) or 0))
    except (TypeError, ValueError):
        return "0"


# --------------------------------------------------------------------------- #
# 下达指令
# --------------------------------------------------------------------------- #


def set_order(bastion: Bastion, name: str, order: str, *, note: str = "") -> str:
    """给一座特色设施下达指令（校验该设施是否支持这条指令）。"""
    fac = bastion.find(name)
    if fac is None:
        have = "、".join(f.key for f in bastion.facilities) or "（还没有设施）"
        return f"据点里没有「{name}」。现有：{have}"
    key = _order_key(order)
    allowed = order_options(fac.key)
    if key == MAINTAIN:
        return "维护指令是给整个据点的，用「据点 维护」"
    if key not in allowed:
        return (
            f"{fac.key}不支持「{order}」指令，它只能：{' / '.join(allowed) or '无'}"
            "（特色设施表里一设施只给一条指令）"
            + f"\n{format_facility(fac.key)}"
        )
    if not fac.staffed:
        return f"{fac.key} 目前没有雇员（事件导致的），下一个据点回合才能用"
    if fac.busy_turns > 0:
        return (
            f"{fac.key} 还在忙（{fac.busy_note or '进行中'}，还要 {fac.busy_turns} 个回合），"
            "不能同时接第二条指令"
        )
    fac.order = key
    if note:
        fac.busy_note = note
    return (
        f"{fac.key}：本回合指令「{key}」。"
        + (f"（{note}）" if note else "")
        + "\n（指令在执行据点回合时结算：写「据点 回合」。）"
    )


# --------------------------------------------------------------------------- #
# 据点回合
# --------------------------------------------------------------------------- #


@dataclass
class TurnResult:
    """一次据点回合的结算结果。"""

    text: str
    #: 本回合进出钱包的 GP（正数收入 / 负数支出），由编排层落账
    gold: int = 0
    #: 是否触发了据点事件
    event: str = ""
    #: 需要 DM 注意的提醒
    notes: list[str] = field(default_factory=list)


def find_event(name: str) -> dict[str, Any] | None:
    """按中文名 / 键名找一条据点事件；认不出返回 ``None``。"""
    text = str(name or "").strip()
    if not text:
        return None
    return next(
        (e for e in _EVENTS if str(e.get("cn")) == text or str(e.get("key")) == text),
        None,
    )


def roll_event(rng: Any = None, *, twice: bool = False) -> dict[str, Any]:
    """掷据点事件（1d100）；``twice`` 时掷两次取一（冥想室）。"""
    first = roll("1d100", rng=rng).total
    second = roll("1d100", rng=rng).total if twice else first
    number = min(first, second) if twice else first
    row = next(
        (e for e in _EVENTS if int(e["min"]) <= number <= int(e["max"])), None
    )
    if row is None:  # 表必须覆盖 1–100；这里兜底不该发生
        row = {"key": "fine", "cn": "一切顺利", "dice": "1d8", "note": ""}
    return {
        "roll": number,
        "rolls": (first, second) if twice else (first,),
        "key": str(row["key"]),
        "cn": str(row["cn"]),
        "dice": str(row.get("dice") or ""),
        "note": str(row.get("note") or ""),
        "row": row,
    }


def next_turn(
    bastion: Bastion,
    *,
    level: int = 1,
    present: bool = True,
    rng: Any = None,
) -> TurnResult:
    """执行一个据点回合。

    :param present: 角色本人是否在据点内。**不在据点内 = 自动维护指令**
        （原文：不能用短讯术之类的魔法沟通时，据点如同被下达了维护指令）。
        在据点内且没给任何设施下指令 → 也不掷事件，只累计疏于照顾计数。

    结算顺序：先结算上回合下达的指令（含跨回合的制造 / 收获），
    再清空指令、回合数 +1；维护指令则在**本回合**掷据点事件。
    """
    lines: list[str] = [f"🏰 据点「{bastion.name}」第 {bastion.turn + 1} 回合"]
    gold = 0
    notes: list[str] = []
    event_text = ""
    event_key = ""

    # ① 先把上回合的跨回合工作推进一格
    for fac in bastion.facilities:
        if fac.busy_turns > 0:
            fac.busy_turns -= 1
            if fac.busy_turns == 0:
                lines.append(
                    f"· {fac.key}：{fac.busy_note or '上一轮的工作'} 已完成，可以领取"
                    "（具体得到什么由你按第七章 / 设施描述给）"
                )
        if fac.buff_until and fac.buff_until <= bastion.turn:
            lines.append(f"· {fac.key}：增益「{fac.buff_note}」到期")
            fac.buff_until = 0
            fac.buff_note = ""

    # ② 本回合的指令
    orders = [(fac, fac.order) for fac in bastion.facilities if fac.order]
    auto_maintain = not present
    if auto_maintain:
        lines.append("（主人不在据点内 → 视为下达了维护指令）")

    if orders and auto_maintain:
        lines.append("⚠️ 人不在据点，这些指令本轮不生效：" + "、".join(
            f.key for f, _ in orders
        ))
        orders = []

    for fac, order in orders:
        text, coins, extra = _run_order(bastion, fac, order, level=level, rng=rng)
        lines.append(f"· {fac.key}（{order}）：{text}")
        gold += coins
        notes.extend(extra)

    # ③ 维护指令 → 掷据点事件（只有它才触发事件）
    if auto_maintain or bastion.maintained:
        info = roll_event(rng, twice=bastion.reroll_event)
        bastion.reroll_event = False
        event_key = str(info["key"])
        result = resolve_event(bastion, info, level=level, rng=rng)
        event_text = result.text
        gold += result.gold
        notes.extend(result.notes)
        lines.append("")
        lines.append(f"🎲 据点事件（1d100={info['roll']}）：{info['cn']}")
        lines.append(event_text)
        # 军械库的补货只保一次事件
        if bastion.armory:
            bastion.armory = False
            lines.append("（军械库的装备已被这次事件消耗，需要重新补货）")

    # ④ 收尾：清指令、推进回合、记疏于照顾
    idle = not orders and not (auto_maintain or bastion.maintained)
    for fac in bastion.facilities:
        fac.order = ""
        if not fac.staffed:
            fac.staffed = True  # 新雇员接手，下个回合恢复
    bastion.maintained = False
    bastion.turn += 1
    bastion.idle_turns = bastion.idle_turns + 1 if idle else 0
    if idle:
        need = max(1, int(level))
        if bastion.idle_turns >= need:
            notes.append(
                f"⚠️ 连续 {bastion.idle_turns} 个回合没下任何指令"
                f"（疏于照顾阈值 = 角色等级 {need}）："
                "按第八章该由雇员弃守、据点被洗劫了。要不要判失去据点由你定。"
            )
        elif bastion.idle_turns >= max(1, need - 1):
            notes.append(
                f"（这已经是连续第 {bastion.idle_turns} 个没下指令的回合，"
                f"到 {need} 就该判疏于照顾了。）"
            )

    if not bastion.facilities:
        lines.append("（据点里还没有特色设施：5 级起该选 2 座，写「据点 增 铁匠铺:宽敞」）")

    text = "\n".join(lines)
    if notes:
        text += "\n\n" + "\n".join(notes)
    return TurnResult(text=text, gold=gold, event=event_key, notes=notes)


def _run_order(
    bastion: Bastion,
    fac: BastionFacility,
    order: str,
    *,
    level: int,
    rng: Any = None,
) -> tuple[str, int, list[str]]:
    """结算一条设施指令，返回 ``(说明, 金币增量, 给 DM 的提醒)``。"""
    notes: list[str] = []
    gold = 0
    row = fac.row()
    summary = str(row.get("summary") or "")

    if order in _LONG_ORDERS:
        days = 7
        fac.busy_turns = 1
        fac.busy_note = f"{order}（{summary.split('；')[0][:40]}）"
        text = f"已开工，需要 {days} 天——下个据点回合就能领。"
        if fac.key == "铁匠铺":
            notes.append("铁匠铺：制造项由玩家从设施描述里选（工具制品 / 9 级起武具表魔法物品）。")
        return text, gold, notes

    if order == "招募":
        return _run_recruit(bastion, fac, level=level, rng=rng)

    if order == "贸易":
        return _run_trade(bastion, fac, level=level, rng=rng)

    if order == "研究":
        if fac.key == "陈列室":
            number = roll("1d6", rng=rng).total
            if number % 2 == 0:
                from .dm_tables import roll_treasure

                picked = roll_treasure(rarity="普通", rng=rng)
                return (
                    f"掷骰 {number}（偶数）→ 找到一件魔法物品：{picked['item']}",
                    0,
                    [picked["text"]],
                )
            return f"掷骰 {number}（奇数）→ 雇员什么也没找到。", 0, []
        if fac.key == "酒馆":
            return (
                "间谍交来情报（7 天内据点 10 里内的大事、50 里内已知生物的位置）。"
                "**内容由你写**，系统只提供这条渠道。",
                0,
                ["酒馆情报：必须把具体内容显式写进叙述，玩家才看得到。"],
            )
        if fac.key == "档案馆":
            return (
                "雇员如同施展了通晓传奇，把知识带回来了（7 天）。内容由你定。",
                0,
                [],
            )
        return (
            f"研究 7 天：{summary}。**三条情报的具体内容由你写**，系统不生成。",
            0,
            [],
        )

    if order == "增强":
        days = 7
        if fac.key == "剧院":
            days = 21
            notes.append(
                "剧院：排练 14 天 + 演出 7 天。参与者做 DC 15 魅力（表演），"
                f"过半成功者各得剧院骰（{_theater_dice(level)}）。"
            )
        if fac.key == "冥想室":
            bastion.reroll_event = True
            notes.append("冥想室：下次掷据点事件可以掷两次取一（已记下）。")
        if fac.key == "训练场":
            notes.append(
                "训练场：从 coach 表选一名教练，把具体增益告诉玩家（模板见 query_data bastion 教练）。"
            )
        fac.buff_until = bastion.turn + max(1, days // 7)
        fac.buff_note = f"{fac.key}·{order}"
        return (
            f"增益已开始，持续 {days} 天（效果：{summary}）。具体数值由你落。",
            0,
            notes,
        )

    return f"指令「{order}」已记录（{summary}）。", 0, notes


def _theater_dice(level: int) -> str:
    if level >= 17:
        return "d10"
    if level >= 13:
        return "d8"
    return "d6"


def _run_recruit(
    bastion: Bastion, fac: BastionFacility, *, level: int, rng: Any = None
) -> tuple[str, int, list[str]]:
    notes: list[str] = []
    if fac.key == "兵营":
        cap = constant("barrack_vast" if fac.space == "vast" else "barrack_roomy", 12)
        total_cap = bastion.defender_cap() or cap
        add = min(constant("barrack_recruit", 4), max(0, total_cap - bastion.defenders))
        if add <= 0:
            return (
                f"兵营已满（营地总容量 {total_cap} 名，现有 {bastion.defenders} 名）："
                "先把卫兵派出去，或扩建 / 再建一座兵营。",
                0,
                [],
            )
        bastion.defenders += add
        return (
            f"招募到 {add} 名据点卫兵（据点共 {bastion.defenders} 名，"
            f"容量 {total_cap} 名）。",
            0,
            notes,
        )
    if fac.key == "动物园":
        notes.append(
            "动物园：从 menagerie 表选一只生物并收费（或按 cr_price 表定价），"
            "加进栏内；栏内生物算据点卫兵（容 4 只大型）。"
        )
        return "招募进行中（7 天，按表付费）。", 0, notes
    if fac.key == "传送法阵":
        number = roll("1d6", rng=rng).total
        if number % 2 == 0:
            return f"掷骰 {number}（偶数）→ 那位施法者拒绝了邀请，本轮没有收获。", 0, []
        limit = "8 环" if level >= 17 else "4 环"
        return (
            f"掷骰 {number}（奇数）→ 一位友好施法者来了，会在据点待 14 天"
            f"（可请他施 {limit} 以下法师法术一次；他不参与防御）。",
            0,
            [],
        )
    if fac.key == "作战指挥室":
        if bastion.officers >= 10:
            return "副官已经满 10 名了。", 0, []
        bastion.officers += 1
        return (
            f"新增 1 名副官（共 {bastion.officers} 名）。"
            "每位副官使「攻击」事件的骰数 −1。",
            0,
            ["也可以改为集结军队：每人至多 100 名守卫（骑兵 20），每天每名 1 GP 粮草。"],
        )
    if fac.key == "公会大厅":
        notes.append("公会大厅：按 guild 表挑一条任务，产出与工期见表。")
        return "已派公会会员执行任务（具体见表）。", 0, notes
    return "招募指令已记录。", 0, notes


def _run_trade(
    bastion: Bastion, fac: BastionFacility, *, level: int, rng: Any = None
) -> tuple[str, int, list[str]]:
    gold = 0
    notes: list[str] = []
    if fac.key == "游戏厅":
        table = next(
            (t for t in _TABLES.get("harvest", ()) if t.get("key") == "gamehall"),
            None,
        )
        number = roll("1d100", rng=rng).total
        row = None
        if table:
            row = next(
                (r for r in table["rows"] if int(r["min"]) <= number <= int(r["max"])),
                None,
            )
        expr = str((row or {}).get("pay") or "1d6*10")
        amount = _roll_expr(expr, rng)
        gold += amount
        return (
            f"赌场 7 天结账：1d100={number} → {(row or {}).get('text') or ''!s}"
            f" = **{amount} GP**（已记进这回合的收入）",
            gold,
            [],
        )
    if fac.key == "军械库":
        cost = bastion.gold_armory_cost()
        bastion.armory = True
        return (
            f"已补货，花费 {cost} GP；下次「攻击」事件卫兵损失改投 1d8，事件后失效。",
            -cost,
            [],
        )
    if fac.key == "仓库":
        cap = 500 if level < 9 else (2000 if level < 13 else 5000)
        bonus = _sell_bonus(level, 10, 20, 50, 100)
        return (
            f"7 天内买进 / 卖出不超过 {cap} GP 的货物；卖出时买家多付 {bonus}%（按标准价）。",
            0,
            ["仓库买卖的金额要用 trade 工具或 update_character 的「钱」字段落账。"],
        )
    if fac.key == "马厩":
        bonus = _sell_bonus(level, 20, 20, 50, 100)
        return (
            f"7 天内按标准价买卖坐骑；卖出时买家多付 {bonus}%。",
            0,
            ["马厩：卖出加价按等级 5 级 20% / 13 级 50% / 17 级 100%。"],
        )
    return "贸易指令已记录。", 0, notes


def _sell_bonus(level: int, lv5: int, lv9: int, lv13: int, lv17: int) -> int:
    if level >= 17:
        return lv17
    if level >= 13:
        return lv13
    if level >= 9:
        return lv9
    return lv5


def _roll_expr(expr: str, rng: Any = None) -> int:
    """掷 ``1d6*10`` 这种式子（只认 ``NdM[*K]``）。"""
    text = str(expr or "").strip().lower().replace("×", "*").replace(" ", "")
    if not text:
        return 0
    mult = 1
    if "*" in text:
        head, _, tail = text.partition("*")
        text = head
        try:
            mult = int(tail)
        except ValueError:
            mult = 1
    try:
        return roll(text, rng=rng).total * mult
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# 据点事件
# --------------------------------------------------------------------------- #


@dataclass
class EventResult:
    """一个据点事件的结算结果。"""

    text: str
    gold: int = 0
    notes: list[str] = field(default_factory=list)


def resolve_event(
    bastion: Bastion,
    info: dict[str, Any],
    *,
    level: int = 1,
    detail: str = "",
    rng: Any = None,
) -> EventResult:
    """把一个据点事件结算到机械部分（叙事仍交 DM）。

    :param detail: 事件需要的玩家输入。目前只有「请求援助」用它——
        写派出几名据点卫兵（``2`` 或 ``派 2 人``）。
    """
    key = str(info.get("key") or "")
    notes: list[str] = []

    if key == "fine":
        number = roll("1d8", rng=rng).total
        row = next(
            (r for r in _TABLES.get("fine_detail", ()) if int(r["roll"]) == number),
            None,
        )
        return EventResult(
            f"一切顺利（1d8={number}）：{(row or {}).get('text') or ''!s}"
        )

    if key == "attack":
        # 原文：投 6d6，每个 1 死一名据点卫兵。军械库补货后「投 1d8 而非 d6」
        # —— 改的是**面数**不是骰数；副官则每人使骰数 −1。
        faces = 8 if bastion.armory else 6
        count = max(0, 6 - max(0, bastion.officers))
        if count <= 0:
            return EventResult(
                "副官们把袭击者打退了，一个骰子都不用投（副官数 ≥ 骰数）。"
            )
        rolls = [roll(f"1d{faces}", rng=rng).total for _ in range(count)]
        lost = sum(1 for n in rolls if n == 1)
        head = (
            f"掷 {count}d{faces} = "
            + "+".join(str(n) for n in rolls)
            + (
                f"（有军械库，改投 d8；副官 −{bastion.officers} 骰）"
                if bastion.armory and bastion.officers
                else (
                    "（有军械库，改投 d8）"
                    if bastion.armory
                    else (f"（副官 −{bastion.officers} 骰）" if bastion.officers else "")
                )
            )
        )
        if bastion.defenders <= 0:
            target = _random_facility(bastion, rng)
            if target is None:
                return EventResult(head + "\n据点没有据点卫兵，也没有特色设施可砸。")
            target.staffed = False
            return EventResult(
                head
                + f"\n没有据点卫兵 → 随机一座特色设施受损：**{target.key}**，"
                "下个据点回合无法使用（之后自动修复，不花钱）。"
            )
        real = min(lost, bastion.defenders)
        bastion.defenders -= real
        body = f"\n掷出 {lost} 个 1 → 损失 {real} 名据点卫兵（剩 {bastion.defenders} 名）"
        if real < lost:
            body += f"；另有 {lost - real} 次本该死人，但卫兵已经不够了"
        return EventResult(head + body + "。袭击被打退了。")

    if key == "criminal":
        amount = _roll_expr("1d6*100", rng)
        return EventResult(
            f"可以花 **{amount} GP** 保下这位雇员；不保则他被逮捕。"
            "若因此有设施无人值守，该设施下个回合无法使用（之后自动补人，不花钱）。",
            0,
            ["罪犯雇员：保不保由玩家决定；花钱用 update_character 的「钱」字段。"],
        )

    if key == "opportunity":
        gp = constant("opportunity_gp", 500)
        return EventResult(
            f"花 **{gp} GP** 承办这场机会，回报是**重掷一次据点事件**"
            "（再掷出这个机会则重投）；不花则无事发生。",
            0,
            ["机不可失：玩家答应后再掷一次 roll_event，并把结果用 resolve_event 结算。"],
        )

    if key == "visitor":
        amount = _roll_expr("1d6*100", rng)
        return EventResult(
            f"来访者借用一座特色设施，付 **{amount} GP**（不影响该设施执行你的指令）。",
            amount,
        )

    if key == "guest":
        number = roll("1d4", rng=rng).total
        row = next(
            (r for r in _TABLES.get("guest", ()) if int(r["roll"]) == number), None
        )
        text = f"来客（1d4={number}）：{(row or {}).get('text') or ''!s}"
        if number in (2,):
            gift = _roll_expr("1d6*100", rng)
            text += f"\n（走的时候给的礼物：{gift} GP，7 天后才给，别现在加钱）"
        if number == 3:
            bastion.defenders += 1
            text += f"\n（据点卫兵 +1，现 {bastion.defenders} 名）"
        return EventResult(text)

    if key == "lost_hireling":
        target = _random_facility(bastion, rng)
        if target is None:
            return EventResult("据点里没有特色设施，无人可失。")
        target.staffed = False
        return EventResult(
            f"随机一座特色设施失去全部雇员：**{target.key}**"
            "（下个据点回合无法使用；之后新雇员接手，不花钱）。"
        )

    if key == "magic_find":
        return EventResult(
            "雇员找到 / 造出一件**非普通魔法物品**，必须是药剂或卷轴，由玩家选，不花钱。",
            0,
            [
                "魔法发现：用 query_data(bastion, 魔法发现) 可以看候选，"
                "也可以直接按第七章非普通表给。"
            ],
        )

    if key == "refugees":
        count = roll("2d4", rng=rng).total
        pay = _roll_expr("1d6*100", rng)
        return EventResult(
            f"{count} 名难民求避（据点没有足够基础设施就扎营在外），"
            f"他们付 **{pay} GP**。会一直待着，直至你安置他们或据点遭袭。",
            pay,
        )

    if key == "aid":
        squads = _parse_count(detail)
        if squads <= 0:
            return EventResult(
                "请求援助：先决定派几名据点卫兵（写「据点 援助 2」），"
                "每派一人投 1d6，和 ≥10 才算顺利。",
                0,
                ["请求援助要 DM 先问清玩家派几个人，再带 detail 结算。"],
            )
        squads = min(squads, bastion.defenders)
        rolls = [roll("1d6", rng=rng).total for _ in range(squads)]
        total = sum(rolls)
        pay = _roll_expr("1d6*100", rng)
        if total >= 10:
            return EventResult(
                f"派 {squads} 人，投 {'+'.join(str(n) for n in rolls)}={total} ≥ 10 → "
                f"问题解决，报酬 **{pay} GP**。",
                pay,
            )
        if bastion.defenders > 0:
            bastion.defenders -= 1
        return EventResult(
            f"派 {squads} 人，投 {'+'.join(str(n) for n in rolls)}={total} < 10 → "
            f"问题仍然解决，但报酬减半为 **{pay // 2} GP**，且其中一名卫兵阵亡"
            f"（剩 {bastion.defenders} 名）。",
            pay // 2,
        )

    if key == "treasure":
        number = roll("1d100", rng=rng).total
        row = next(
            (
                r
                for r in _TABLES.get("treasure", ())
                if int(r["min"]) <= number <= int(r["max"])
            ),
            None,
        )
        if row is None:
            return EventResult(f"宝藏（1d100={number}）：表里没有这一段。")
        if str(row.get("kind")) == "art":
            from .dm_tables import roll_art

            value = int(row.get("value") or 0)
            art = roll_art(value)
            return EventResult(
                f"宝藏（1d100={number}）→ {row.get('cn')}：{art}"
            )
        rarity = str(row.get("rarity") or "非普通")
        notes.append(
            f"宝藏是魔法物品（{rarity}）：主题由玩家选（奥秘 / 武备 / 器具 / 圣物），"
            f"写「据点 宝藏 {rarity} 奥秘」一次掷完。"
        )
        return EventResult(
            f"宝藏（1d100={number}）→ {row.get('cn')}。物品种类从第七章对应表掷出。",
            0,
            notes,
        )

    return EventResult(str(info.get("note") or ""), 0, notes)


def _parse_count(text: str) -> int:
    import re

    found = re.findall(r"\d+", str(text or ""))
    return int(found[0]) if found else 0


def _random_facility(
    bastion: Bastion, rng: Any = None
) -> BastionFacility | None:
    pool = [f for f in bastion.facilities if f.staffed]
    if not pool:
        pool = list(bastion.facilities)
    if not pool:
        return None
    number = roll(f"1d{len(pool)}", rng=rng).total
    return pool[max(0, min(len(pool) - 1, number - 1))]


# --------------------------------------------------------------------------- #
# 失去据点
# --------------------------------------------------------------------------- #


def is_lost(bastion: Bastion, level: int) -> bool:
    """疏于照顾的硬判定：连续「角色等级」次据点回合没下任何指令。"""
    return bastion.idle_turns >= max(1, int(level))


# --------------------------------------------------------------------------- #
# 文本
# --------------------------------------------------------------------------- #


def format_bastion(bastion: Bastion, level: int = 0) -> str:
    """据点跟进卡（只有 DM 看得到；玩家须知要显式写进叙述）。"""
    lines = [f"🏰 据点「{bastion.name}」"]
    meta = [f"回合 {bastion.turn}", f"据点卫兵 {bastion.defenders}"]
    if bastion.officers:
        meta.append(f"副官 {bastion.officers}")
    if bastion.armory:
        meta.append("军械库 已补货")
    if level:
        used, cap = bastion.slots(level)
        meta.append(f"特色设施 {used}/{cap}")
    lines.append(" · ".join(meta))

    if bastion.basic:
        lines.append(
            "基础设施："
            + "、".join(
                f"{r.get('key')}（{space_cn(str(r.get('space') or ''))}）"
                for r in bastion.basic
            )
        )
    if bastion.facilities:
        lines.append("特色设施：")
        for fac in bastion.facilities:
            bits = [space_cn(fac.space), f"雇员 {fac.staff()}"]
            if fac.order:
                bits.append(f"指令 {fac.order}")
            if fac.busy_turns:
                bits.append(f"忙 {fac.busy_note or '进行中'}（{fac.busy_turns} 回合）")
            if not fac.staffed:
                bits.append("无人值守")
            if fac.buff_until:
                bits.append(f"增益至第 {fac.buff_until} 回合")
            lines.append(f"  - {fac.key}（{'，'.join(bits)}）")
    else:
        lines.append("特色设施：（还没有）")

    if bastion.idle_turns:
        lines.append(f"⚠️ 连续 {bastion.idle_turns} 个回合没下任何指令")
    lines.append(
        "⚠️ 这份据点卡只有 DM 看得到：设施状态、卫兵数、事件结果要写进叙述，玩家才知道。"
    )
    return "\n".join(lines)


def format_facility(name: str) -> str:
    """单座设施的元数据（等级 / 空间 / 雇员 / 指令 / 先决 / 机制摘要）。"""
    row = get_facility(name)
    if row is None:
        return f"没有「{name}」这个设施。可用：{'、'.join(FACILITIES)}"
    spaces = " / ".join(space_cn(str(s)) for s in (row.get("spaces") or ()))
    staff = "，".join(
        f"{space_cn(str(k))} {v}"
        for k, v in (row.get("staff") or {}).items()
    )
    lines = [
        f"【特色设施】{row['cn']}（{row.get('en')}）",
        f"- 等级：{row.get('level')}",
        f"- 空间：{spaces}",
        f"- 雇员：{staff or '无'}",
        f"- 指令：{' / '.join(str(o) for o in (row.get('orders') or ())) or '无'}",
        f"- 先决：{row.get('pre') or '无'}",
        f"- 机制：{row.get('summary') or ''}",
    ]
    if "vast" in [str(s) for s in (row.get("spaces") or ())]:
        lines.append(f"- 扩建：{constant('special_expand_gp', 2000)} GP 扩为庞大（原文没给工期）")
    return "\n".join(lines)


def format_facility_list(level: int = 20) -> str:
    """按等级分组的特色设施清单（含先决与指令）。"""
    rows = sorted(
        (r for r in FACILITIES.values() if int(r.get("level") or 0) <= int(level)),
        key=lambda r: (int(r.get("level") or 0), str(r.get("cn"))),
    )
    if not rows:
        return f"{level} 级还没有可获取的特色设施（5 级起才有）"
    lines = [f"可用特色设施（{level} 级，共 {len(rows)} 座）："]
    current = 0
    for row in rows:
        lv = int(row.get("level") or 0)
        if lv != current:
            current = lv
            lines.append(f"\n{lv} 级：")
        pre = str(row.get("pre") or "无")
        orders = "/".join(str(o) for o in (row.get("orders") or ()))
        lines.append(
            f"- {row['cn']}（{orders}，{space_cn(str((row.get('spaces') or ['roomy'])[0]))}）"
            + (f" 先决：{pre}" if pre and pre != "无" else "")
        )
    lines.append("\n（先决条件由你判定——卡片上没有「法器熟练」这类字段，系统不校验。）")
    return "\n".join(lines)


def format_event_table() -> str:
    """据点事件表（1d100）与各段的机械要点。"""
    lines = ["🎲 据点事件（1d100，只有下达维护指令后才掷）"]
    for row in _EVENTS:
        dice = f"（{row['dice']}）" if row.get("dice") else ""
        lines.append(f"- {int(row['min']):02d}–{int(row['max']):02d} {row['cn']}{dice}")
    lines.append(
        "\n各事件怎么结算：写「据点 事件 <事件名>」"
        "或直接看 query_data(bastion, 事件名)。"
    )
    return "\n".join(lines)


_SUB_TABLE_WORDS: dict[str, str] = {
    "教练": "coach",
    "训练场教练": "coach",
    "公会": "guild",
    "范例公会": "guild",
    "客人": "guest",
    "来客": "guest",
    "特饮": "drink",
    "酒馆特饮": "drink",
    "参考书": "book",
    "种植园": "garden",
    "兽栏": "menagerie",
    "动物园生物": "menagerie",
    "按挑战等级": "cr_price",
    "挑战等级价格": "cr_price",
    "宝藏": "treasure",
    "一切顺利": "fine_detail",
    "细节": "fine_detail",
    "事件": "event",
    "事件表": "event",
    "尺寸": "size",
    "空间": "size",
    "数量": "slot",
    "基础设施": "basic",
}


def format_bastion_query(name: str) -> str:
    """``query_data(bastion, …)`` 的文本结果：设施 / 事件 / 各类子表 / 规则。

    认不出返回空串（调用方回落到「未知」提示）。
    """
    text = str(name or "").strip()
    if not text:
        return format_event_table() + "\n\n" + format_facility_list(20)
    if text in ("规则", "口径", "帮助", "用法"):
        return bastion_rules()
    row = get_facility(text)
    if row is not None:
        return format_facility(str(row["cn"]))
    event = find_event(text)
    if event is not None:
        dice = f"（{event['dice']}）" if event.get("dice") else ""
        return (
            f"🎲 据点事件：{event['cn']}{dice}\n"
            f"区间：{int(event['min']):02d}–{int(event['max']):02d}\n"
            f"结算：{event.get('note') or ''}"
        )
    key = _SUB_TABLE_WORDS.get(text)
    if key:
        return _format_sub_table(key)
    return ""


def _format_sub_table(key: str) -> str:
    rows = _TABLES.get(key, ())
    if not rows:
        return f"（没有 {key} 这张表）"
    if key == "size":
        lines = ["空间 | 方格 | 建造 | 工期 | 扩建 | 扩建工期"]
        for r in rows:
            lines.append(
                f"{r['cn']} | {r['squares']} | {r['build_gp']} GP | {r['build_days']} 天 | "
                f"{r['expand_gp'] or '—'} | {r['expand_days'] or '—'}"
            )
        lines.append("（建造 / 扩建只针对**基础设施**；特色设施靠升级获取。）")
        return "\n".join(lines)
    if key == "slot":
        return "等级 | 特色设施数\n" + "\n".join(
            f"{r['level']} | {r['count']}" for r in rows
        )
    if key == "basic":
        return "基础设施（纯装饰，没有指令）：\n" + "、".join(
            str(r["cn"]) for r in rows
        )
    if key == "coach":
        return "教练 | 增益（持续 7 天）\n" + "\n".join(
            f"{r['cn']} | {r['effect']}" for r in rows
        )
    if key == "guild":
        return "公会 | 任务\n" + "\n".join(
            f"{r['cn']} | {r['task']}" for r in rows
        )
    if key == "drink":
        return "特饮 | 效应（24 小时，倒出后 24 小时不喝失效）\n" + "\n".join(
            f"{r['cn']} | {r['effect']}" for r in rows
        )
    if key == "book":
        return "参考书 | 技能 | 效果\n" + "\n".join(
            f"{r['cn']} | {r['skill']} | {r['text']}" for r in rows
        )
    if key == "garden":
        return "类型 | 描述 | 一次收获\n" + "\n".join(
            f"{r['cn']} | {r['desc']} | {r['yield']}" for r in rows
        )
    if key == "menagerie":
        return "生物 | 体型 | 花费\n" + "\n".join(
            f"{r['cn']} | {r['size']} | {r['gp']} GP" for r in rows
        )
    if key == "cr_price":
        return "挑战等级 | 花费\n" + "\n".join(
            f"{r['cr']} | {r['gp']} GP" for r in rows
        )
    if key == "guest":
        return "1d4 | 来的是谁\n" + "\n".join(
            f"{r['roll']} | {r['text']}" for r in rows
        )
    if key == "fine_detail":
        return "1d8 | 细节\n" + "\n".join(
            f"{r['roll']} | {r['text']}" for r in rows
        )
    if key == "treasure":
        lines = ["1d100 | 宝藏"]
        for r in rows:
            lines.append(f"{int(r['min']):02d}–{int(r['max']):02d} | {r['cn']}")
        lines.append("（艺术品按 gp 档掷表；魔法物品按主题 + 稀有度掷第七章表）")
        return "\n".join(lines)
    if key == "event":
        return format_event_table()
    return f"（{key} 没有对应的文本格式）"


def bastion_help() -> str:
    """``tactic(action=据点)`` 不带子命令时的用法。"""
    return (
        "🏰 据点（DMG 2024 第八章）：\n"
        "· 建：据点 建 名字 —— 5 级起，先选 2 个基础设施（一窄一宽）与 2 座特色设施\n"
        "· 设施：据点 设施 —— 按当前等级列出可选的特色设施\n"
        "· 增：据点 增 铁匠铺:宽敞 —— 加一座特色设施（校验等级与数量上限）\n"
        "· 扩建：据点 扩建 兵营 —— 花 2000 GP 扩为庞大\n"
        "· 指令：据点 指令 铁匠铺:制造 —— 支持 制造 / 增强 / 收获 / 研究 / 招募 / 贸易\n"
        "· 维护：据点 维护 —— 本轮只维护，**会掷据点事件**\n"
        "· 回合：据点 回合 [在|不在] —— 结算一个据点回合（7 天）；「不在」视为自动维护\n"
        "· 卫兵：据点 卫兵 +4 / -2 —— 手工调整据点卫兵数\n"
        "· 状态：据点 状态 —— 据点跟进卡（只有 DM 看得到）\n"
        "· 查：据点 查 铁匠铺 —— 设施元数据；据点 规则 —— 口径与边界\n"
        "· 失去：据点 失去 疏于照顾 / 放弃 / 毁灭\n"
        "注：维护指令才会触发据点事件；在据点内给设施下指令不会。"
    )


def bastion_rules() -> str:
    """口径与实现边界（写进 DM 的说明文本）。"""
    return (
        "🏰 据点口径（DMG 2024 第八章）\n"
        f"· 解锁：角色 {constant('unlock_level', 5)} 级获得据点；"
        f"特色设施数 {slots_for(5)}/{slots_for(9)}/{slots_for(13)}/{slots_for(17)}"
        "（5/9/13/17 级）。\n"
        f"· 回合：游戏内每 {constant('turn_days', 7)} 天一次，频率由 DM 定（可放慢）。\n"
        "· 指令七种：制造 / 增强 / 收获 / 研究 / 招募 / 贸易 / 维护。"
        "维护给整个据点，下了它这轮就不能给设施下指令。\n"
        "· **只有维护指令才掷据点事件**。在据点内正常下令不掷。\n"
        "· 雇员：每座特色设施自带雇员，薪水由设施收入覆盖（不记账）。\n"
        "\n系统算什么：设施等级 / 空间 / 雇员 / 指令合法性 / 卫兵与副官台账 /"
        "事件骰池与金钱 / 军械库把 6d6 改 1d8 / 副官减骰 / 请求援助和值 /"
        "赌场与宝藏表。\n"
        "系统不算什么：设施增益的具体效果（护咒、临时生命、豁免优势）、研究出什么情报、"
        "先决条件是否满足、事件怎么发生——都归 DM。\n"
        "\n失去据点三种：放弃所有权 / 疏于照顾（连续等于角色等级的回合不下指令）/ "
        "彻底毁灭（万象无常牌抽到废墟）。"
    )
