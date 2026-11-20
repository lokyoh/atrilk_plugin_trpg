"""据点的存档 IO 与 ``tactic`` 子命令编排（agents 层）。

``core/dnd/bastion.py`` 是纯规则层，不碰磁盘；这里把 :class:`Bastion` 落到
**该存档目录下**的 ``bastion.json``（与 ``combat.json`` / ``journey.json`` 同级），
再把 DM 写进 ``tactic(action=据点 …)`` 的子命令翻成状态机调用。

一个存档里可以有**多个**据点（每人一个），所以文件结构是
``{"bastions": {"<user_id>": {...}}}``。

边界（与纯函数层一致）：
- 程序算：设施等级 / 空间 / 雇员 / 指令合法性 / 卫兵与副官台账 / 事件骰池与金钱 /
  军械库改 d8 / 副官减骰 / 请求援助和值 / 赌场与宝藏表。
- 交 DM：设施增益的具体效果、研究出什么情报、先决条件是否满足、事件怎么发生。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...dnd.bastion import (
    ORIENT_WORDS,
    Bastion,
    add_basic,
    add_special,
    bastion_help,
    bastion_rules,
    expand_special,
    facility_names,
    find_event,
    format_bastion,
    format_event_table,
    format_facility,
    format_facility_list,
    is_lost,
    new_bastion,
    next_turn,
    remove_special,
    resolve_event,
    roll_event,
    set_order,
    slots_for,
)
from ...dnd.coins import add_coins, can_afford, format_coins
from ...dnd.save_store import find_character, save_dir, set_character

__all__ = [
    "BASTION_FILENAME",
    "bastion_path",
    "clear_bastion",
    "do_bastion",
    "load_bastion",
    "save_bastion",
]

BASTION_FILENAME = "bastion.json"

#: 子命令按**长度降序**匹配，否则「基建」会被「建」吃掉
_COMMANDS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("基础设施", "basic"),
            ("基建", "basic"),
            ("建", "create"),
            ("建立", "create"),
            ("新建", "create"),
            ("设施", "list"),
            ("列表", "list"),
            ("增", "add"),
            ("添加", "add"),
            ("换", "swap"),
            ("替换", "swap"),
            ("扩建", "expand"),
            ("指令", "order"),
            ("命令", "order"),
            ("维护", "maintain"),
            ("回合", "turn"),
            ("事件", "event"),
            ("援助", "aid"),
            ("宝藏", "treasure"),
            ("卫兵", "defenders"),
            ("守卫", "defenders"),
            ("状态", "status"),
            ("跟进卡", "status"),
            ("规则", "rules"),
            ("查", "show"),
            ("失去", "lose"),
            ("帮助", "help"),
            ("说明", "help"),
        ),
        key=lambda pair: -len(pair[0]),
    )
)

_SPLIT = re.compile(r"[,，;；\s]+")
_COLON = re.compile(r"[:：]")

_VISIBILITY_NOTE = (
    "⚠️ 这份据点卡只有 DM 看得到：设施状态、卫兵数与事件结果要显式写进叙述，玩家才知道。"
)


# --------------------------------------------------------------------------- #
# 存档 IO
# --------------------------------------------------------------------------- #


def bastion_path(save) -> Path:
    return save_dir(save.save_id) / BASTION_FILENAME


def _read_all(save) -> dict[str, Any]:
    path = bastion_path(save)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    rows = data.get("bastions")
    return dict(rows) if isinstance(rows, dict) else {}


def _write_all(save, rows: dict[str, Any]) -> Path:
    path = bastion_path(save)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"bastions": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def load_bastion(save, owner: str) -> Bastion | None:
    """读某个角色的据点；没有返回 ``None``。"""
    raw = _read_all(save).get(str(owner or ""))
    if not isinstance(raw, dict):
        return None
    return Bastion.from_dict(raw)


def save_bastion(save, bastion: Bastion) -> Path:
    rows = _read_all(save)
    rows[bastion.owner] = bastion.to_dict()
    return _write_all(save, rows)


def clear_bastion(save, owner: str) -> bool:
    rows = _read_all(save)
    if str(owner or "") not in rows:
        return False
    rows.pop(str(owner), None)
    _write_all(save, rows)
    return True


# --------------------------------------------------------------------------- #
# 归属与落账
# --------------------------------------------------------------------------- #


def _owner_of(save, player: str) -> tuple[str, Any]:
    """把「谁在操作据点」归一成 ``(user_id, 角色卡)``。"""
    card = find_character(save, str(player or "").strip())
    if card is None:
        return "", None
    return str(card.user_id or ""), card


def _apply_gold(save, owner: str, delta: int) -> str:
    """把据点收支写进角色卡钱包（正数收入 / 负数支出）。

    ⚠️ **余额不足时不扣**：``add_coins`` 会把负数算成负债再被 `from_cp` 吞掉，
    看起来「扣过了」其实是免单。这里先查一次，不足就如实说没扣成功。
    """
    if not delta:
        return ""
    card = find_character(save, owner)
    if card is None:
        return f"（{delta:+d} GP 没能落账：找不到角色卡）"
    if delta < 0 and not can_afford(card.money, -delta):
        return (
            f"⚠️ **{delta} GP 没有扣成功**：钱包不足（现有：{format_coins(card.money)}）。"
            "设施状态已经按这笔支出记了账 —— 要让玩家先凑钱、还是当赊账，你定；"
            "不认这笔就把它撤销。"
        )
    card.money = add_coins(card.money, {"金": int(delta)})
    set_character(save, card)
    return f"钱包：{format_coins(card.money)}（{delta:+d} GP）"


def _level(save, owner: str) -> int:
    card = find_character(save, owner)
    return int(getattr(card, "level", 1) or 1) if card is not None else 1


# --------------------------------------------------------------------------- #
# 解析小工具
# --------------------------------------------------------------------------- #


def _split_pair(text: str) -> tuple[str, str]:
    """``铁匠铺:宽敞`` / ``铁匠铺 宽敞`` → ``(铁匠铺, 宽敞)``。"""
    raw = str(text or "").strip()
    if not raw:
        return "", ""
    parts = [p for p in _COLON.split(raw) if p.strip()]
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    words = raw.split()
    if len(words) >= 2:
        return words[0].strip(), " ".join(words[1:]).strip()
    return raw, ""


def _need(save, owner: str) -> tuple[Bastion | None, str]:
    bastion = load_bastion(save, owner)
    if bastion is None:
        return None, (
            "这一局还没有据点。角色 5 级起才能有，建的话写「据点 建 名字」，"
            "再选 2 个基础设施与 2 座特色设施（「据点 基建 厨房:狭窄」「据点 增 铁匠铺:宽敞」）。"
        )
    return bastion, ""


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def do_bastion(
    save,
    action_text: str,
    *,
    player: str = "",
    target: str = "",
    trigger: str = "",
    rng: Any = None,
) -> str:
    """把 ``tactic(action=据点 …)`` 翻成状态机调用。"""
    text = str(action_text or "").strip()
    if text.startswith("据点"):
        text = text[2:].strip()
    if not text:
        return bastion_help()

    cmd_key = ""
    rest = text
    for word, key in _COMMANDS:
        if text.startswith(word):
            cmd_key = key
            rest = text[len(word) :].strip()
            break
    if not cmd_key:
        return f"没认出「{text}」。\n{bastion_help()}"

    ref = str(player or "").strip() or str(target or "").strip()
    if not ref:
        # 单人局不必点名字：据点归属具体角色，只有一个人时就取他
        ref = next(iter(getattr(save, "player_ids", ()) or ()), "")
    owner, card = _owner_of(save, ref)
    if not owner:
        return (
            "没找到操作据点的角色卡：用 player 参数写角色名。"
            "（据点归属具体角色，多人各有一处。）"
        )
    level = _level(save, owner)
    who = getattr(card, "name", "") or owner

    if cmd_key == "help":
        return bastion_help()
    if cmd_key == "rules":
        return bastion_rules()
    if cmd_key == "list":
        return format_facility_list(level)
    if cmd_key == "show":
        name = rest or str(target or "").strip()
        if not name:
            bastion = load_bastion(save, owner)
            return (
                format_bastion(bastion, level)
                if bastion is not None
                else format_facility_list(level)
            )
        return format_facility(name)
    if cmd_key == "event" and not rest:
        # 只查表，不需要据点
        return format_event_table()

    if cmd_key == "create":
        if level < 5:
            return f"{who} 才 {level} 级：据点要到 5 级才有（DMG 2024 第八章）。"
        existing = load_bastion(save, owner)
        if existing is not None:
            return (
                f"{who} 已经有据点了：「{existing.name}」（第 {existing.turn} 回合）。"
                "要换个地方先写「据点 失去 放弃所有权」。"
            )
        name = rest or "据点"
        bastion = new_bastion(owner, name)
        save_bastion(save, bastion)
        return (
            f"🏰 {who} 的据点「{name}」建立完成（{level} 级，可拥有 {_slots_text(level)}）。\n"
            "接下来按第八章选：\n"
            "· 2 个基础设施（一窄一宽，免费）：据点 基建 厨房:狭窄、据点 基建 卧室:宽敞\n"
            "· 2 座特色设施（要满足先决，免费）：据点 增 铁匠铺:宽敞\n"
            "（可用的特色设施见「据点 设施」。）"
        )

    bastion, error = _need(save, owner)
    if bastion is None:
        return error

    if cmd_key == "add":
        name, space = _split_pair(rest or str(target or "").strip())
        if not name:
            return "写法：「据点 增 铁匠铺:宽敞」（空间可省，取该设施默认值）"
        out = add_special(bastion, name, space, level)
        if "已加入" in out:
            save_bastion(save, bastion)
        return out

    if cmd_key == "basic":
        name, space = _split_pair(rest)
        if not name:
            return "写法：「据点 基建 厨房:狭窄」（狭窄 / 宽敞 / 庞大）"
        out = add_basic(bastion, name, space)
        if "已增添" in out:
            save_bastion(save, bastion)
        return out

    if cmd_key == "swap":
        name, new = _split_pair(rest)
        if not name:
            return "写法：「据点 换 铁匠铺 图书馆」（先移除旧的，再加新的）"
        first = remove_special(bastion, name)
        if "没有" in first:
            return first
        if not new:
            save_bastion(save, bastion)
            return first
        second = add_special(bastion, new, "", level)
        save_bastion(save, bastion)
        return first + "\n" + second

    if cmd_key == "expand":
        name = rest or str(target or "").strip()
        if not name:
            return "写法：「据点 扩建 兵营」"
        out = expand_special(bastion, name)
        if "已扩建" in out:
            save_bastion(save, bastion)
        return out

    if cmd_key == "order":
        name, order = _split_pair(rest)
        if not name or not order:
            return "写法：「据点 指令 铁匠铺:制造」"
        out = set_order(bastion, name, order)
        if "本回合指令" in out:
            save_bastion(save, bastion)
        return out

    if cmd_key == "maintain":
        bastion.maintained = True
        save_bastion(save, bastion)
        return (
            "已下达维护指令：本轮所有雇员专注维护据点，**不能**再给设施下指令。\n"
            "执行「据点 回合」时会掷一次据点事件（1d100）。"
        )

    if cmd_key == "turn":
        present = True
        word = str(rest or "").strip()
        if word:
            for key, value in ORIENT_WORDS.items():
                if word.startswith(key):
                    present = value
                    break
            else:
                return "「据点 回合」后面只能写「在」或「不在」（不带就按「在据点」算）"
        result = next_turn(bastion, level=level, present=present, rng=rng)
        save_bastion(save, bastion)
        text_out = result.text
        if result.gold:
            paid = _apply_gold(save, owner, result.gold)
            mark = "💰" if result.gold > 0 else "💸"
            text_out += f"\n\n{mark} {paid}"
        if is_lost(bastion, level):
            text_out += (
                "\n\n⚠️ 疏于照顾已达阈值（连续 ≥ 角色等级 个回合没下指令）。"
                "按第八章该判据点了：写「据点 失去 疏于照顾」或让玩家重新建。"
            )
        return text_out + "\n\n" + _VISIBILITY_NOTE

    if cmd_key == "event":
        return _cmd_event(save, bastion, rest, level=level, rng=rng)

    if cmd_key == "aid":
        return _cmd_event(save, bastion, "请求援助 " + rest, level=level, rng=rng)

    if cmd_key == "treasure":
        return _cmd_treasure(rest, rng=rng)

    if cmd_key == "defenders":
        return _cmd_defenders(save, bastion, rest)

    if cmd_key == "status":
        return format_bastion(bastion, level) + "\n" + format_event_table()

    if cmd_key == "lose":
        reason = rest or "放弃所有权"
        clear_bastion(save, owner)
        return (
            f"{who} 失去了据点「{bastion.name}」（{reason}）。\n"
            "按第八章，可以再建一个新的：两个基础设施（一窄一宽）+ "
            f"{_slots_text(level)}特色设施。写「据点 建 名字」重来。"
        )

    return f"没认出「{text}」。\n{bastion_help()}"


def _slots_text(level: int) -> str:
    return f"{slots_for(level)} 座"


def _cmd_event(
    save,
    bastion: Bastion,
    text: str,
    *,
    level: int,
    rng: Any = None,
) -> str:
    """单独结算一个据点事件（不含掷表；用于「机不可失」重掷与请求援助）。"""
    raw = str(text or "").strip()
    if not raw:
        return format_event_table()
    words = _SPLIT.split(raw)
    name = words[0]
    detail = " ".join(words[1:])
    row = find_event(name)
    if row is None:
        # 允许「掷」：正常掷一次
        if name in ("掷", "随机", "roll"):
            info = roll_event(rng)
            result = resolve_event(bastion, info, level=level, detail=detail, rng=rng)
            save_bastion(save, bastion)
            return f"🎲 据点事件（1d100={info['roll']}）：{info['cn']}\n{result.text}"
        return f"没有叫「{name}」的据点事件。\n{format_event_table()}"
    info = {
        "roll": 0,
        "key": str(row["key"]),
        "cn": str(row["cn"]),
        "dice": str(row.get("dice") or ""),
        "note": str(row.get("note") or ""),
        "row": row,
    }
    result = resolve_event(bastion, info, level=level, detail=detail, rng=rng)
    save_bastion(save, bastion)
    text_out = f"🎲 据点事件：{info['cn']}\n{result.text}"
    if result.gold:
        text_out += f"\n\n💰 {_apply_gold(save, bastion.owner, result.gold)}"
    return text_out


def _cmd_treasure(text: str, rng: Any = None) -> str:
    """直接按第七章掷一件魔法物品（宝藏事件选主题时用）。"""
    words = _SPLIT.split(str(text or "").strip())
    rarity = words[0] if words else "非普通"
    theme = words[1] if len(words) > 1 else ""
    from ...dnd.dm_tables import roll_treasure

    try:
        picked = roll_treasure(theme=theme, rarity=rarity, rng=rng)
    except KeyError as e:
        return str(e)
    return picked["text"]


def _cmd_defenders(save, bastion: Bastion, text: str) -> str:
    """手工调整据点卫兵数（招募只走兵营；这里是 DM 的兜底）。"""
    raw = str(text or "").strip().replace(" ", "")
    if not raw:
        return f"据点现有 {bastion.defenders} 名据点卫兵、{bastion.officers} 名副官"
    sign = 1
    if raw.startswith(("+", "加")):
        raw = raw.lstrip("+").lstrip("加")
    elif raw.startswith(("-", "减")):
        sign = -1
        raw = raw.lstrip("-").lstrip("减")
    try:
        count = abs(int(raw))
    except ValueError:
        return "写法：「据点 卫兵 +4」/「据点 卫兵 -2」"
    cap = bastion.defender_cap()
    if sign > 0 and cap is not None:
        count = min(count, max(0, cap - bastion.defenders))
    bastion.defenders = max(0, bastion.defenders + sign * count)
    save_bastion(save, bastion)
    return (
        f"据点卫兵 {sign:+d}{count} → 现有 {bastion.defenders} 名"
        + (f"（容量 {cap} 名）" if cap is not None else "")
    )


# 供 query_data 复用：设施名对不上时列出全部可选
def _facility_hint(level: int) -> str:
    return "、".join(facility_names(level)) or "（5 级起才有）"


__all__ += ["_facility_hint"]
