"""《城主指南 (2024)》第二章「旅行」：11 种地形表与旅程阶段（纯函数层）。

**和 ``travel.py`` 不是一回事**：``travel.py`` 是本项目自创的「省际跨越难度
1–5 → 天数」，回答「从这个省到那个省要几天」；这里回答的是 DMG 明文那一套
「这一阶段走什么地形、最快能多快、遭遇在多远、觅食/导航/搜索 DC 各是多少、
四个步骤怎么走」。两套口径并行，别互相覆盖。

## 数值的唯一真相

- 11 种地形的五列、天气、声音传播、能见度、觅食 / 导航 / 追踪常量、
  阶段四步与挑战 —— ``res/dnd/journey.yaml``（``load_mapping`` 分段表）。
- **步调本身的英里数 / 强行军 DC 不在这张表里**：复用
  :mod:`.environment` 的 ``TRAVEL_PACE`` 与 ``forced_march_exhaustion``。
  复制一份会造成两处真相，改了一边另一边悄悄算错。

## 边界

- 检定（觅食 / 导航 / 搜索）由 DM 用 ``check`` 工具对着角色卡做；这里只给
  **DC** 与检定成功后的**产出 / 延时掷骰**。账本不记 HP / AC / 口粮存量。
- 天气、遭遇距离、觅食产出、迷路延时由系统掷骰；「该不该有遭遇」「障碍
  造成什么后果」是 DM 的裁定，不自动化。
- 不碰磁盘、不 import agents；序列化 ``to_dict/from_dict`` 手写两边对齐。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..trpg.dice import roll
from .datafiles import load_mapping
from .environment import forced_march_exhaustion, travel_pace, travel_pace_text

__all__ = [
    "JourneyLeg",
    "JourneyState",
    "complete_leg",
    "forage_dc",
    "forage_yield",
    "forced_march",
    "format_journey",
    "format_journey_query",
    "format_journey_rules",
    "format_terrain_table",
    "get_terrain",
    "journey_help",
    "leg_hours",
    "navigation_dc",
    "navigation_delay",
    "normalize_pace",
    "resolve_pace",
    "roll_encounter_distance",
    "roll_weather",
    "search_dc",
    "set_pace",
    "start_journey",
    "terrain_names",
]

#: 一天按 8 小时旅行算（超过 8 小时是「延长旅行」，见 environment）
TRAVEL_HOURS_PER_DAY = 8

_PACE_ORDER = ("slow", "normal", "fast")
_PACE_WORDS = {
    "缓速": "slow",
    "慢速": "slow",
    "慢": "slow",
    "slow": "slow",
    "中速": "normal",
    "普通": "normal",
    "正常": "normal",
    "normal": "normal",
    "快速": "fast",
    "快": "fast",
    "fast": "fast",
}
_PACE_CN = {"slow": "慢速", "normal": "普通", "fast": "快速"}
_PARENS = re.compile(r"（.*?）|\(.*?\)")


def _rows(section: str) -> tuple[dict[str, Any], ...]:
    return load_mapping("journey").get(section, ())


def terrain_names() -> tuple[str, ...]:
    return tuple(str(row["cn"]) for row in _rows("terrain"))


def _bare(name: str) -> str:
    return _PARENS.sub("", str(name or "").strip()).strip()


def get_terrain(name: str) -> dict[str, Any] | None:
    """按中文名 / 别名找地形行；认不出返回 ``None``（**不猜**）。"""
    key = _bare(name).lower()
    if not key:
        return None
    rows = _rows("terrain")
    for row in rows:
        if str(row["cn"]).lower() == key:
            return row
    for row in rows:
        aliases = (str(a).lower() for a in (row.get("aliases") or ()))
        if key in aliases:
            return row
    return None


def normalize_pace(word: str) -> str | None:
    """把「缓速 / 慢速 / 中速 / 普通 / 快速」等写法归一到 slow/normal/fast。

    原文用词不统一（地形表写「缓速」，步调小节写「慢速」），必须归一后再和
    ``environment.TRAVEL_PACE`` 对齐。「特殊」不是步调，返回 ``None``。
    """
    return _PACE_WORDS.get(_bare(word).lower())


def resolve_pace(
    terrain: str,
    wanted: str,
    *,
    good_road: bool = False,
    half_speed: bool = False,
    ignore_difficult: bool = False,
) -> tuple[str | None, str]:
    """按 DMG 的修正算出这一阶段**实际**能用的步调。

    :return: ``(slow/normal/fast 或 None, 说明)``；``None`` = 水路等靠载具、
        不选旅行步调的情形。
    """
    row = get_terrain(terrain)
    if row is None:
        return None, f"认不出地形「{terrain}」，没法定步调"
    if str(row["max_pace"]) == "特殊":
        return None, f"【{row['cn']}】{row.get('pace_note') or '按载具速度算，不选旅行步调'}"
    if ignore_difficult:
        return "fast", "飞行或能无视困难地形 → 不受地形限制，可用快速步调"

    cap = _PACE_ORDER.index(normalize_pace(str(row["max_pace"])) or "normal")
    reasons = [f"【{row['cn']}】最大步调 {row['max_pace']}"]
    if good_road:
        cap = min(cap + 1, len(_PACE_ORDER) - 1)
        reasons.append("路况良好 → 上限提高一节")
    if half_speed:
        cap = 0
        reasons.append("有成员速度减半或更低 → 全队强制慢速")

    key = normalize_pace(wanted)
    if key is None:
        key = "normal"
        reasons.append(f"没认出「{wanted}」，按普通算")
    actual = _PACE_ORDER[min(_PACE_ORDER.index(key), cap)]
    if actual != key:
        reasons.append(f"想要的{_PACE_CN[key]}被压到{_PACE_CN[actual]}")
    return actual, "；".join(reasons)


def leg_hours(miles: float, pace: str) -> float:
    """按步调算这一阶段要走几小时（不足一天也照实给）。"""
    per_day = float(travel_pace(pace)["mi_per_day"])
    if per_day <= 0 or miles <= 0:
        return 0.0
    return float(miles) / per_day * TRAVEL_HOURS_PER_DAY


def roll_encounter_distance(terrain: str, rng: Any = None) -> tuple[int, str]:
    """掷该地形的遭遇距离；返回 ``(尺数, 说明)``。"""
    row = get_terrain(terrain)
    if row is None:
        return 0, f"认不出地形「{terrain}」"
    result = roll(str(row["encounter_dice"]), rng=rng)
    feet = result.total * int(row["encounter_mult"])
    return feet, (
        f"【{row['cn']}】{row['encounter_display']}："
        f"{row['encounter_dice']}={result.total} → **{feet} 尺**"
    )


def _dc(terrain: str, field_name: str) -> int:
    row = get_terrain(terrain)
    if row is None:
        return 0
    return int(row.get(field_name) or 0)


def forage_dc(terrain: str) -> int:
    return _dc(terrain, "forage_dc")


def navigation_dc(terrain: str) -> int:
    return _dc(terrain, "navigation_dc")


def search_dc(terrain: str) -> int:
    return _dc(terrain, "search_dc")


def forage_yield(wis_mod: int, rng: Any = None) -> tuple[int, int, str]:
    """觅食成功后掷食物磅数与水加仑数（**各掷一次 1d6 + 感知调整值**）。

    负数产出没有意义（感知调整值可能是负的），按 0 兜底；这是工程兜底，
    不是规则改动。检定本身由 DM 用 ``check`` 做，这里只管成功后的产出。
    """
    row = _rows("forage")[0]
    food = max(0, roll(str(row["yield_dice"]), rng=rng).total + int(wis_mod))
    water = max(0, roll(str(row["yield_dice"]), rng=rng).total + int(wis_mod))
    return food, water, (
        f"觅食成功：食物 **{food} {row['food_unit']}**、水 **{water} {row['water_unit']}**"
        f"（两次 {row['yield_dice']} 各加感知调整值 {int(wis_mod):+d}）"
    )


def navigation_delay(rng: Any = None) -> tuple[int, int, str]:
    """导航检定失败后的延时；返回 ``(d6点数, 百分比, 说明)``。"""
    row = _rows("navigation")[0]
    n = roll(str(row["delay_dice"]), rng=rng).total
    percent = n * int(row["delay_percent"])
    return n, percent, (
        f"迷路：{row['delay_dice']}={n} → 当前旅程阶段长度延长 **{percent}%**"
        "（基准值，DM 可自行裁定表现形式）"
    )


def _weather_band(value: int) -> dict[str, Any]:
    for row in _rows("weather"):
        lo, _, hi = str(row["roll"]).partition("-")
        try:
            if int(lo) <= value <= int(hi or lo):
                return row
        except ValueError:
            continue
    return _rows("weather")[0]


def roll_weather(rng: Any = None) -> str:
    """每阶段掷 1d20 三次，分别定气温、风势、降水并落到一段可读文本。"""
    values = [roll("1d20", rng=rng).total for _ in range(3)]
    labels = ("气温", "风势", "降水")
    fields = ("temperature", "wind", "precipitation")
    bits = []
    for label, field_name, value in zip(labels, fields, values, strict=True):
        row = _weather_band(value)
        value_text = str(row.get(field_name) or "—")
        if "1d4" in value_text:
            swing = roll("1d4", rng=rng).total * 10
            value_text = f"变化 {swing} 华氏度（{'变冷' if '变冷' in value_text else '变热'}）"
        bits.append(f"{label} 1d20={value} → {value_text}")
    notes = " ".join(str(r.get("note") or "") for r in _rows("weather") if r.get("note"))
    return "本阶段天气（1d20 × 3）：\n" + "\n".join(bits) + (f"\n※ {notes.strip()}" if notes.strip() else "")


def forced_march(hours: int, con_mod: int, rng: Any = None) -> int:
    """「延长旅行」：一天超过 8 小时后累计获得的力竭层数。

    直接转 :func:`.environment.forced_march_exhaustion`——**唯一真相在 PHB 层**，
    这里只是把 DMG 第二章的同一条规则接到旅程阶段上，不复制 DC 算法。
    """
    return forced_march_exhaustion(hours, con_mod, rng=rng)


@dataclass
class JourneyLeg:
    """旅程的一个阶段（DMG 的「旅行规划卡」一行）。"""

    name: str = ""
    start: str = ""
    goal: str = ""
    miles: float = 0.0
    terrain: str = ""
    weather: str = ""
    pace: str = ""
    challenges: list[str] = field(default_factory=list)
    days_spent: float = 0.0
    done: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": self.start,
            "goal": self.goal,
            "miles": self.miles,
            "terrain": self.terrain,
            "weather": self.weather,
            "pace": self.pace,
            "challenges": list(self.challenges),
            "days_spent": self.days_spent,
            "done": self.done,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JourneyLeg:
        return cls(
            name=str(data.get("name") or ""),
            start=str(data.get("start") or ""),
            goal=str(data.get("goal") or ""),
            miles=float(data.get("miles") or 0),
            terrain=str(data.get("terrain") or ""),
            weather=str(data.get("weather") or ""),
            pace=str(data.get("pace") or ""),
            challenges=[str(x) for x in (data.get("challenges") or ())],
            days_spent=float(data.get("days_spent") or 0),
            done=bool(data.get("done")),
            note=str(data.get("note") or ""),
        )


@dataclass
class JourneyState:
    """一场旅程：若干阶段 + 当前阶段 + 累计用时。"""

    legs: list[JourneyLeg] = field(default_factory=list)
    index: int = 0
    elapsed_days: float = 0.0
    log: list[str] = field(default_factory=list)
    ended: bool = False

    def current(self) -> JourneyLeg | None:
        if self.ended or not (0 <= self.index < len(self.legs)):
            return None
        return self.legs[self.index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "legs": [leg.to_dict() for leg in self.legs],
            "index": self.index,
            "elapsed_days": self.elapsed_days,
            "log": list(self.log),
            "ended": self.ended,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JourneyState:
        return cls(
            legs=[
                JourneyLeg.from_dict(row)
                for row in (data.get("legs") or ())
                if isinstance(row, dict)
            ],
            index=int(data.get("index") or 0),
            elapsed_days=float(data.get("elapsed_days") or 0),
            log=[str(x) for x in (data.get("log") or ())],
            ended=bool(data.get("ended")),
        )


def start_journey(legs: list[JourneyLeg]) -> JourneyState:
    if not legs:
        raise ValueError("旅程至少要有一个阶段")
    return JourneyState(legs=list(legs))


def set_pace(
    state: JourneyState,
    wanted: str,
    *,
    good_road: bool = False,
    half_speed: bool = False,
    ignore_difficult: bool = False,
) -> str:
    """第一步：给当前阶段定步调，顺带算出预计耗时。"""
    leg = state.current()
    if leg is None:
        return "旅程已经结束，没有正在进行的阶段"
    pace, reason = resolve_pace(
        leg.terrain,
        wanted,
        good_road=good_road,
        half_speed=half_speed,
        ignore_difficult=ignore_difficult,
    )
    leg.pace = pace or ""
    if pace is None:
        text = f"{reason}；耗时按载具速度另算"
    else:
        hours = round(leg_hours(leg.miles, pace), 1)
        text = (
            f"{reason}；{travel_pace_text(pace)}。"
            f" 阶段 {leg.miles:g} 里 → 约 **{hours:g} 小时**"
        )
    state.log.append(text)
    return text


def complete_leg(state: JourneyState, *, delay_percent: float = 0) -> str:
    """第四步：跟踪进度 —— 结掉当前阶段，把时间记进账本。"""
    leg = state.current()
    if leg is None:
        return "旅程已经结束，没有正在进行的阶段"
    hours = round(leg_hours(leg.miles, leg.pace) if leg.pace else 0.0, 1)
    hours *= 1 + max(0.0, float(delay_percent)) / 100
    days = round(hours / TRAVEL_HOURS_PER_DAY, 2)
    leg.days_spent = days
    leg.done = True
    state.elapsed_days = round(state.elapsed_days + days, 2)
    state.index += 1
    text = (
        f"阶段「{leg.name or leg.goal or leg.terrain}」走完："
        f"约 {hours:g} 小时（{days:g} 天）"
        + (f"，含迷路延时 {delay_percent:g}%" if delay_percent else "")
        + f"；累计 {state.elapsed_days:g} 天。"
    )
    if state.index >= len(state.legs):
        state.ended = True
        text += " **全部阶段完成，旅程结束。**"
    else:
        nxt = state.current()
        text += f" 下一阶段：{nxt.name or nxt.goal or nxt.terrain}（{nxt.terrain} {nxt.miles:g} 里）"
    state.log.append(text)
    return text


def format_journey(state: JourneyState) -> str:
    """DM 账本卡（只有 DM 看得到）。"""
    lines = [
        f"【旅程规划卡】阶段 {min(state.index + 1, len(state.legs))}/{len(state.legs)}"
        f" | 累计 {state.elapsed_days:g} 天"
        + (" | 已结束" if state.ended else "")
    ]
    for i, leg in enumerate(state.legs, 1):
        mark = "✔" if leg.done else ("▶" if i == state.index + 1 else "·")
        pace = _PACE_CN.get(leg.pace, "载具/未定") if leg.pace else "载具/未定"
        row = get_terrain(leg.terrain)
        dcs = (
            f"觅食DC{row['forage_dc']} / 导航DC{row['navigation_dc']} / 搜索DC{row['search_dc']}"
            if row
            else "地形未识别"
        )
        lines.append(
            f"{mark} {i}. {leg.name or leg.goal or '（未命名）'}："
            f"{leg.start or '?'} → {leg.goal or '?'}，{leg.miles:g} 里 / {leg.terrain or '?'} / {pace}"
        )
        weather = (leg.weather or "天气未定").replace("\n", " ")[:60]
        lines.append(
            f"     {dcs}；{weather}"
            + (f"；挑战：{'、'.join(leg.challenges)}" if leg.challenges else "")
        )
    if state.log:
        lines.append("最近记录：" + state.log[-1])
    return "\n".join(lines)


def format_terrain_table(name: str = "") -> str:
    """地形表：留空列 11 种，给名字出单行详情与步调说明。"""
    text = _bare(name)
    if text:
        row = get_terrain(text)
        if row is None:
            return f"认不出地形「{name}」。可用：{'、'.join(terrain_names())}"
        pace, reason = resolve_pace(str(row["cn"]), "快速")
        lines = [
            f"【{row['cn']}】别名：{'、'.join(str(a) for a in row.get('aliases') or ()) or '—'}",
            f"最大步调：{row['max_pace']}"
            + (f"（{row['pace_note']}）" if row.get("pace_note") else ""),
            f"遭遇距离：{row['encounter_display']}",
            f"觅食DC {row['forage_dc']} / 导航DC {row['navigation_dc']} / 搜索DC {row['search_dc']}",
        ]
        if pace:
            lines.append(f"按最快步调走：{travel_pace_text(pace)}")
        else:
            lines.append(reason)
        return "\n".join(lines)

    lines = ["【旅行地形】（DMG 2024 L1320-1333）地形 | 最大步调 | 遭遇距离 | 觅食DC | 导航DC | 搜索DC"]
    for row in _rows("terrain"):
        lines.append(
            f"{row['cn']} | {row['max_pace']} | {row['encounter_display']} | "
            f"{row['forage_dc']} | {row['navigation_dc']} | {row['search_dc']}"
        )
    lines.append(
        "※ 极地保持快速需滑雪板等装备；水路按载具速度、不选步调。"
        "「缓速」= 慢速、「普通」= 中速（原文用词不统一）。"
    )
    lines.append("※ 步调英里数：" + "；".join(travel_pace_text(k) for k in _PACE_ORDER))
    return "\n".join(lines)


_SECTION_CN = {
    "pace_rules": "步调修正",
    "weather": "天气表（1d20 × 3）",
    "phase_notes": "旅程阶段说明",
    "visibility_notes": "户外 / 海上 / 水下能见度",
    "underwater_visibility": "水下遭遇距离",
    "sound_distance": "声音传播距离",
    "forage": "觅食",
    "navigation": "导航",
    "tracking": "追踪",
    "challenges": "旅程阶段挑战",
    "phase_steps": "运行阶段的四个步骤",
}


def format_journey_rules(key: str = "") -> str:
    """按主题给 DMG 原文段落；留空给总览。"""
    text = _bare(key)
    sections: dict[str, tuple[str, ...]] = {
        "步调": ("pace_rules",),
        "天气": ("weather", "phase_notes"),
        "能见度": ("visibility_notes", "underwater_visibility", "sound_distance"),
        "声音": ("sound_distance",),
        "水下": ("underwater_visibility",),
        "觅食": ("forage",),
        "导航": ("navigation",),
        "追踪": ("tracking",),
        "挑战": ("challenges",),
        "阶段": ("phase_steps", "phase_notes"),
    }
    picked = next((v for k, v in sections.items() if k in text), None)
    if picked is None:
        if text in ("", "规则", "全部", "所有", "总览", "旅行"):
            return (
                "旅行规则可查：地形（默认给 11 种全表）/ 步调 / 天气 / 能见度 / "
                "声音 / 水下 / 觅食 / 导航 / 追踪 / 挑战 / 阶段。"
                "\n" + "\n".join(
                    f"{i}. {r['title']}：{r['text']}" for i, r in enumerate(_rows("phase_steps"), 1)
                )
            )
        return ""

    lines: list[str] = []
    for section in picked:
        lines.append(f"【{_SECTION_CN.get(section, section)}】")
        for row in _rows(section):
            label = str(
                row.get("key") or row.get("level") or row.get("condition") or ""
            )
            if section == "phase_steps":
                label = f"{row.get('step')}. {row.get('title')}"
            body = str(row.get("text") or "")
            if section == "weather":
                body = (
                    f"1d20 {row['roll']}：气温 {row['temperature']} / "
                    f"风势 {row['wind']} / 降水 {row['precipitation']}"
                )
            elif section == "underwater_visibility":
                body = f"遭遇距离 {row['feet']} 尺"
            elif section == "sound_distance":
                body = f"{row['display']}"
            elif section in ("forage", "navigation", "tracking"):
                body = "；".join(
                    f"{k} {v}" for k, v in row.items() if k not in ("text",)
                ) + "。" + str(row.get("text") or "")
            if label and body:
                lines.append(f"· {label}：{body}")
            elif body:
                lines.append("· " + body)
            if row.get("note"):
                lines.append("  ※ " + str(row["note"]))
    return "\n".join(lines)


def format_journey_query(name: str = "") -> str:
    """``query_data(table=旅行/地形/旅程, name=…)`` 的入口。"""
    text = str(name or "").strip()
    if not text:
        return format_terrain_table()
    if get_terrain(text) is not None:
        return format_terrain_table(text)
    rules = format_journey_rules(text)
    if rules:
        return rules
    return format_terrain_table()


def journey_help() -> str:
    return (
        "【旅程】子命令（写在 tactic 的 action 里）：\n"
        "· 旅程 规划 <起点>:<终点>:<英里>:<地形>[:名称]，多段用逗号隔开（也可放 target）\n"
        "  例：旅程 规划 河湾:林缘:30:草地,林缘:遗址:20:森林\n"
        "· 旅程 状态 —— 看规划卡与累计天数\n"
        "· 旅程 步调 快速 [路况良好/速度减半/无视困难] —— 第一步：按地形压上限并算耗时\n"
        "· 旅程 天气 / 遭遇 / 觅食 / 迷路 —— 掷 1d20×3 / 遭遇距离 / 1d6+感知 / 1d6×10%\n"
        "· 旅程 挑战 <词> —— 记一个本阶段挑战；旅程 推进 —— 第四步：结掉当前阶段\n"
        "· 旅程 结束 —— 清掉规划卡\n"
        "· query_data(table=旅行, name=森林/步调/天气/觅食/导航/追踪/阶段) 查明文规则"
    )
