"""《城主指南 (2024)》NPC 态度 / 忠诚 / 声望（纯函数层 + 手写序列化状态）。

三节原文：态度 L1126-1130 + 初始态度 L6037-6047、忠诚 L3854-3868、
声望 L4331-4379。数值唯一真相是 ``res/dnd/relations.yaml``。

## 边界

- **都是可选规则**：引擎只记账与掷骰，不替 DM 判断「这件事该不该给声望」、
  「NPC 是否觉得被虐待」。是否触发事件由 DM 写子命令，系统算数值。
- 忠诚值**私下记**（玩家不该确定 NPC 忠不忠）；DM 工具回执只有 DM 看得到，
  态度变化与声望收益必须显式写进叙述。
- 不碰磁盘、不 import agents；``RelationsState`` 的 ``to_dict/from_dict``
  手写，两边字段必须对齐（存进去读出来不能丢）。

## 两处工程裁定（原文没写死）

- 起始忠诚 = 队伍最高魅力的一半，取整方式未写 → **向下取整** ``cha // 2``。
- 忠诚阈值 10 原文自相矛盾（「10 或更高冒死」与「1 到 10 之间薄弱」），
  取更靠前的明确句：**>=10 冒死、1~9 薄弱、0 背叛**。两条都写进了数据表注释。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from typing import Any

from ..trpg.dice import roll
from .datafiles import load_mapping

__all__ = [
    "AttitudeRoll",
    "NpcRelation",
    "RelationsState",
    "apply_loyalty",
    "apply_reputation",
    "attitude_names",
    "format_relations_query",
    "format_relations_rules",
    "format_relations_state",
    "initial_loyalty",
    "initial_variant",
    "loyalty_event",
    "loyalty_max",
    "loyalty_tier",
    "mutiny_check",
    "normalize_attitude",
    "reputation_benefits",
    "reputation_by_level",
    "reputation_gain",
    "roll_initial_attitude",
    "teamwork_days",
]


@cache
def _rows(section: str) -> tuple[dict[str, Any], ...]:
    return load_mapping("relations")[section]


def _rule(key: str) -> Any:
    return next(row["value"] for row in _rows("loyalty_rules") if row["key"] == key)


def _match(rows: tuple[dict[str, Any], ...], text: str) -> dict[str, Any] | None:
    key = str(text or "").strip().lower()
    if not key:
        return None
    for row in rows:
        names = [str(row.get("key", "")), str(row.get("cn", ""))]
        names += [str(a) for a in row.get("aliases") or ()]
        if key in {n.strip().lower() for n in names if n}:
            return row
    return None


# --------------------------------------------------------------------------- #
# 态度
# --------------------------------------------------------------------------- #


def attitude_names() -> tuple[str, ...]:
    return tuple(str(row["cn"]) for row in _rows("attitudes"))


def normalize_attitude(text: str) -> str | None:
    row = _match(_rows("attitudes"), text)
    return str(row["cn"]) if row else None


def initial_variant(text: str = "普通") -> dict[str, Any] | None:
    return _match(_rows("initial_variants"), text or "普通")


@dataclass(frozen=True)
class AttitudeRoll:
    attitude: str
    variant: str
    die_total: int
    modified: int
    note: str


def roll_initial_attitude(variant: str = "普通", rng: Any = None) -> AttitudeRoll | None:
    """掷初始态度：变体骰 + 修正 → 查 1d12 原表（原文 L6047 脚注）。"""
    row = initial_variant(variant)
    if row is None:
        return None
    die_total = roll(str(row["dice"]), rng=rng).total
    modified = die_total + int(row["modifier"])
    hit = next(
        (
            item
            for item in _rows("initial_attitude")
            if int(item["low"]) <= modified <= int(item["high"])
        ),
        None,
    )
    if hit is None:
        # 变体修正后的值应落在 1~12；越界说明表被改坏了，不猜一个态度
        raise ValueError(f"初始态度表没有覆盖骰值 {modified}")
    return AttitudeRoll(
        attitude=str(hit["attitude"]),
        variant=str(row["key"]),
        die_total=die_total,
        modified=modified,
        note=str(row["note"]),
    )


# --------------------------------------------------------------------------- #
# 忠诚
# --------------------------------------------------------------------------- #


def loyalty_max(max_charisma: int) -> int:
    """最高忠诚值 = 队伍最高魅力，但不超过 0~20 的硬上限。"""
    return max(0, min(int(_rule("hard_cap")), int(max_charisma)))


def initial_loyalty(max_charisma: int) -> int:
    """起始忠诚 = 最高魅力的一半；原文没写取整方式，工程取向下取整。"""
    return loyalty_max(max_charisma) // int(_rule("start_divisor"))


def apply_loyalty(current: int, delta: int, maximum: int) -> int:
    """0 ≤ 结果 ≤ min(20, 最高魅力)；原文说不能超过最大值、不能低于 0。"""
    cap = loyalty_max(maximum)
    return max(0, min(cap, int(current) + int(delta)))


def loyalty_event(key: str, rng: Any = None) -> tuple[int, str] | None:
    """按事件掷 1d4 / 2d4，返回 (带符号的变化量, 说明)。"""
    row = _match(_rows("loyalty_events"), key)
    if row is None:
        return None
    amount = roll(str(row["dice"]), rng=rng).total * int(row["sign"])
    return amount, str(row["text"])


def loyalty_tier(value: int) -> dict[str, Any]:
    for row in _rows("loyalty_tiers"):
        if int(row["min"]) <= int(value) <= int(row["max"]):
            return row
    raise ValueError(f"忠诚值 {value} 不在 0~20")


def mutiny_check(disloyal: int, crew: int, *, docked: bool = False) -> str:
    """半数船员忠诚归零 → 哗变；停泊时改为离船不归。"""
    if crew < 1:
        raise ValueError("船员总数至少 1")
    ratio = float(_rule("mutiny_ratio"))
    if disloyal < crew * ratio:
        return f"{disloyal}/{crew} 人忠诚归零，未达半数，暂不哗变。"
    if docked:
        return "船已停泊：不忠的船员离船且永不归来。"
    return "航行中至少半数船员忠诚归零：船员态度转为敌对并发动哗变。"


# --------------------------------------------------------------------------- #
# 声望
# --------------------------------------------------------------------------- #


def reputation_gain(key: str) -> int | None:
    """任务类声望增量；「重大任务」默认取下限 3，+4 由 DM 显式写。"""
    row = _match(_rows("reputation_gains"), key)
    if row is None:
        return None
    return int(row["value"])


def major_reputation_options() -> tuple[int, int]:
    row = next(r for r in _rows("reputation_gains") if r["key"] == "重大任务")
    return (int(row["value"]), int(row["max"]))


def teamwork_days(current: int) -> int | None:
    """声望 1+ 后，花「当前声望 × 10」天做团体任务换 +1；0 点不能建设。"""
    if current < 1:
        return None
    row = next(r for r in _rows("reputation_gains") if r["key"] == "团队建设")
    return int(current) * int(row["days_per_point"])


def apply_reputation(current: int, delta: int) -> int:
    """声望不会低于 0（原文 L4366），没有上限。"""
    return max(0, int(current) + int(delta))


def reputation_benefits(value: int) -> tuple[str, ...]:
    return tuple(
        f"{row['cn']}（{row['min']}+）：{row['text']}"
        for row in _rows("reputation_tiers")
        if int(value) >= int(row["min"])
    )


def reputation_by_level(level: int) -> int:
    """不想追踪声望时按等级估算：取「等级不超过该角色」的最高一档。"""
    result = 0
    for row in _rows("reputation_by_level"):
        if int(level) >= int(row["level"]):
            result = int(row["value"])
    return result


# --------------------------------------------------------------------------- #
# 状态（手写序列化）
# --------------------------------------------------------------------------- #


@dataclass
class NpcRelation:
    name: str
    attitude: str = "冷漠"
    loyalty: int = 0
    loyalty_max: int = 0
    goal: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "attitude": self.attitude,
            "loyalty": self.loyalty,
            "loyalty_max": self.loyalty_max,
            "goal": self.goal,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NpcRelation:
        return cls(
            name=str(data.get("name", "")),
            attitude=str(data.get("attitude", "冷漠")),
            loyalty=int(data.get("loyalty", 0)),
            loyalty_max=int(data.get("loyalty_max", 0)),
            goal=str(data.get("goal", "")),
            note=str(data.get("note", "")),
        )


@dataclass
class RelationsState:
    npcs: dict[str, NpcRelation] = field(default_factory=dict)
    reputation: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "npcs": {k: v.to_dict() for k, v in self.npcs.items()},
            "reputation": dict(self.reputation),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationsState:
        npcs = {
            str(k): NpcRelation.from_dict(v)
            for k, v in dict(data.get("npcs") or {}).items()
            if isinstance(v, dict)
        }
        reputation = {
            str(k): int(v)
            for k, v in dict(data.get("reputation") or {}).items()
        }
        return cls(npcs=npcs, reputation=reputation)


# --------------------------------------------------------------------------- #
# 文本
# --------------------------------------------------------------------------- #


def format_relations_rules(name: str = "") -> str:
    """按名字给一段原文规则；留空给三节全文。"""
    text = str(name or "").strip()
    if "忠诚" in text:
        lines = ["【忠诚】（DMG 2024 L3854-3868）"] + [
            f"· {row['text']}（{row['dice']}）" for row in _rows("loyalty_events")
        ]
        lines += [f"· {row['cn']}：{row['text']}" for row in _rows("loyalty_tiers")]
        lines += [f"· {row['title']}：{row['text']}" for row in _rows("loyalty_notes")]
        return "\n".join(lines)
    if "声望" in text:
        lines = ["【声望】（DMG 2024 L4331-4379）"] + [
            f"· {row['text']}" for row in _rows("reputation_gains")
        ]
        lines += [f"· {row['text']}" for row in _rows("reputation_tiers")]
        lines += [f"· {row['title']}：{row['text']}" for row in _rows("reputation_notes")]
        return "\n".join(lines)
    if "态度" in text or "交涉" in text:
        lines = ["【态度】（DMG 2024 L1126-1130、L6037-6047）"]
        lines += [f"· {row['cn']}：{row['text']}" for row in _rows("attitudes")]
        spans = " / ".join(
            f"{row['low']}-{row['high']} {row['attitude']}"
            for row in _rows("initial_attitude")
        )
        lines.append(f"· 初始态度 1d12：{spans}")
        lines += [f"· {row['key']}：{row['note']}" for row in _rows("initial_variants")]
        return "\n".join(lines)
    return "\n\n".join(
        [
            format_relations_rules("态度"),
            format_relations_rules("忠诚"),
            format_relations_rules("声望"),
        ]
    )


def format_relations_state(state: RelationsState) -> str:
    lines = ["【NPC 关系账本】"]
    if state.npcs:
        for rel in state.npcs.values():
            tier = loyalty_tier(rel.loyalty)
            lines.append(
                f"· {rel.name}：{rel.attitude}｜忠诚 {rel.loyalty}/{rel.loyalty_max}"
                f"（{tier['cn']}）"
                + (f"｜目标：{rel.goal}" if rel.goal else "")
                + (f"｜{rel.note}" if rel.note else "")
            )
    else:
        lines.append("· （还没有登记 NPC）")
    if state.reputation:
        for group, value in state.reputation.items():
            lines.append(f"· 声望【{group}】{value}")
            lines += [f"　- {item}" for item in reputation_benefits(value)]
    else:
        lines.append("· （还没有团体声望）")
    lines.append(
        "⚠️ 忠诚值要私下记；这份回执只有 DM 看得到，态度变化与声望收益"
        "必须显式写进叙述，玩家才会知道。"
    )
    return "\n".join(lines)


def format_relations_query(name: str = "") -> str:
    """``query_data(table=关系 / 忠诚 / 声望, name=…)`` 的入口。"""
    text = str(name or "").strip()
    if not text or "规则" in text or "全文" in text:
        return format_relations_rules()
    if text.startswith("初始"):
        variant = text[2:].strip() or "普通"
        result = roll_initial_attitude(variant)
        if result is None:
            return (
                f"没认出初始态度变体「{variant}」。可用："
                + "、".join(str(r["key"]) for r in _rows("initial_variants"))
            )
        return (
            f"【初始态度】{result.variant}：{result.die_total}"
            + (f" + {result.modified - result.die_total}" if result.modified != result.die_total else "")
            + f" = {result.modified} → **{result.attitude}**。{result.note}"
        )
    if text.startswith("等级"):
        digits = "".join(c for c in text if c.isdigit())
        if digits:
            return f"【按等级估声望】{digits} 级 ≈ {reputation_by_level(int(digits))} 点声望（原文 L4372-4379，前提是长期为该团体效力）。"
    if any(word in text for word in ("忠诚", "声望", "态度", "交涉")):
        return format_relations_rules(text)
    return format_relations_rules()
