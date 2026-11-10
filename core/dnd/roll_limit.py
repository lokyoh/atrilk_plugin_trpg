"""掷属性每日一次额度。

建卡 / 改卡的「roll 属性」给的是 :data:`~.creation.ABILITY_SET_COUNT` 组候选
（每项 4d6 去最低），玩家原本能反复「重掷」刷到满意为止。本模块把掷的机会
按 **玩家 + 自然日** 记账：

``data/plugins/跑团/daily_rolls/<user_id>.json`` -> ``{"date": "YYYY-MM-DD"}``

属性现在**只有 roll 一条路**（没有标准数组、也不能手填），所以账本里还会
顺手存下当天掷出的候选组与原始 4d6 点数：玩家取消建卡删了会话，也能把
今天那一组找回来接着挑，不至于被「每天一次」锁死一整天。

⚠️ 账本**独立于建卡会话**：「取消建卡」会删掉会话，删不掉这里；重新
``/跑团.建卡`` 也绕不过去，机器人重启同样保留。日期按 UTC+8（北京时间）
换日，与服务器本地时区无关，也不依赖 ``tzdata``。

:func:`can_roll` 是不碰磁盘的纯判据（方便测试跨天），读写只在
:func:`consume_daily_roll` / :func:`rolled_today`。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ATRI.exceptions import str_traceback
from ATRI.log import log

__all__ = [
    "DAILY_ROLLS_DIRNAME",
    "can_roll",
    "consume_daily_roll",
    "last_roll_date",
    "load_roll_result",
    "roll_path",
    "rolled_today",
    "rolls_dir",
    "save_roll_result",
    "today_key",
]

#: 账本目录名（插件私有目录下）
DAILY_ROLLS_DIRNAME = "daily_rolls"

#: 换日时区：北京时间 UTC+8
_DAY_TZ = timezone(timedelta(hours=8))


def today_key(now: datetime | None = None) -> str:
    """今日的日期键（``YYYY-MM-DD``，UTC+8）。

    传入的 ``now`` 带时区就换算成 UTC+8，不带时区就按 UTC+8 理解 ——
    测试能注入任意时刻模拟跨天，不必改系统时钟。
    """
    current = now or datetime.now(_DAY_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_DAY_TZ)
    return current.astimezone(_DAY_TZ).date().isoformat()


def can_roll(last_date: str, today: str) -> bool:
    """纯判据：上次掷的日期不是今天，就还有一次机会。"""
    return last_date != today


def rolls_dir() -> Path:
    """账本目录（不存在则创建）。"""
    from ... import plugin

    path = plugin.get_path() / DAILY_ROLLS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def roll_path(user_id: str) -> Path:
    """某玩家的账本文件（文件名与 cards / sessions 同一套安全规则）。"""
    safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_") or "unknown"
    return rolls_dir() / f"{safe}.json"


def last_roll_date(user_id: str) -> str:
    """上次掷属性的日期；没掷过 / 读不了都返回空串（不阻断玩家）。"""
    try:
        path = roll_path(user_id)
        if not path.is_file():
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("date") or "")
    except Exception as e:  # 损坏文件 / 目录不可读，都不该让整条建卡流程崩掉
        log.warning(f"读取掷属性记录失败：{user_id}\n{str_traceback(e)}")
        return ""


def rolled_today(user_id: str, *, today: str | None = None) -> bool:
    """今天是否已经用掉额度（只查询，不记账）。"""
    return not can_roll(last_roll_date(user_id), today or today_key())


def _write_record(user_id: str, data: dict) -> None:
    """原子写账本；写盘失败只记日志，不让玩家卡在建卡第一步。"""
    try:
        path = roll_path(user_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        log.warning(f"写入掷属性记录失败：{user_id}\n{str_traceback(e)}")


def consume_daily_roll(user_id: str, *, today: str | None = None) -> bool:
    """尝试消耗今日额度：还有 → 记下日期并返回 ``True``；已用过 → ``False``。

    记账用原子替换，避免写一半被读到；写盘失败时**不拦截玩家**（退化成
    不限次，总比把人卡在建卡第一步好）。
    """
    day = today or today_key()
    if not can_roll(last_roll_date(user_id), day):
        return False
    _write_record(user_id, {"date": day})
    return True


def save_roll_result(
    user_id: str,
    candidates: list[list[int]],
    set_groups: list[list[list[int]]],
    *,
    today: str | None = None,
) -> None:
    """把**当天掷出的候选**随额度一起存，取消建卡删了会话也能接着挑。

    这不是第二次掷：只是把已经掷出来的结果留个底，让「属性只能靠 roll +
    每天一次」不会因为玩家取消建卡就把当天锁死。
    """
    _write_record(
        user_id,
        {
            "date": today or today_key(),
            "candidates": [list(group) for group in candidates],
            "set_groups": [[list(g) for g in groups] for groups in set_groups],
        },
    )


def load_roll_result(
    user_id: str, *, today: str | None = None
) -> tuple[list[list[int]], list[list[list[int]]]] | None:
    """取当天掷出的 ``(候选组, 原始 4d6 点数)``；隔天 / 没掷过 / 坏了都返回 ``None``。"""
    try:
        path = roll_path(user_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("date") != (today or today_key()):
            return None
        raw = data.get("candidates")
        if not isinstance(raw, list) or not raw:
            return None
        candidates = [[int(v) for v in group] for group in raw]
        groups_raw = data.get("set_groups") or []
        set_groups = [[[int(v) for v in g] for g in groups] for groups in groups_raw]
        return candidates, set_groups
    except Exception as e:
        log.warning(f"读取当日掷属性结果失败：{user_id}\n{str_traceback(e)}")
        return None
