"""钱币（5e2014「币制」）——币种换算、加减、展示与掷钱。

⚠️ **建卡不发起始金币**：初始装备包不可放弃成金币（用户裁定），
所以这里的 :func:`roll_starting_gold` 只服务于将来的赏金 / 战利品，
不要接回建卡流程。

内部统一按 **铜币 (cp)** 计价，避免多币种互借与浮点误差。

兑换（参考「标准兑换率」）：

- 1 金 (gp) = 10 银 (sp) = 100 铜 (cp)
- 1 铂 (pp) = 10 金 = 1000 铜
- 1 银金 (ep) = 5 银 = 50 铜（少见，只在换算时认得）

角色卡钱包用中文键 ``{"铜": n, "银": n, "金": n, "铂": n}``（没有的币种不保存），
与 ``settlement`` 的战利品口径（银 / 金 / 铂）一致；展示格式也是「金 35 · 银 20」。
"""

from __future__ import annotations

import re
from typing import Any

from ..trpg.dice import roll

__all__ = [
    "COIN_CP",
    "add_coins",
    "can_afford",
    "format_coins",
    "from_cp",
    "merge_coins",
    "parse_coin_text",
    "parse_cost",
    "roll_starting_gold",
    "spend_coins",
    "to_cp",
]

#: 各币种折合铜币
COIN_CP: dict[str, int] = {
    "铜": 1,
    "银": 10,
    "银金": 50,
    "金": 100,
    "铂": 1000,
    "cp": 1,
    "sp": 10,
    "ep": 50,
    "gp": 100,
    "pp": 1000,
}

#: 展示 / 拆分顺序（大面额在前）
_ORDER: tuple[str, ...] = ("铂", "金", "银", "铜")

#: 货币条目里的写法 → 币种
_COIN_WORDS: dict[str, str] = {
    "金币": "金",
    "银币": "银",
    "铜币": "铜",
    "铂金币": "铂",
    "铂金": "铂",
    "白金": "铂",
    "gp": "金",
    "sp": "银",
    "cp": "铜",
    "pp": "铂",
}

#: 识别「15 金币」「10gp」这类纯货币条目（背景起始钱袋就长这样）
_COIN_RE = re.compile(
    r"^\s*(\d+)\s*(金币|银币|铜币|铂金币|铂金|白金|gp|sp|cp|pp)\s*$", re.IGNORECASE
)


def parse_coin_text(text: str) -> dict[str, int] | None:
    """把「15 金币」这类条目解析成钱包 ``{"金": 15}``；不是纯货币条目返回 ``None``。

    背景的起始钱袋（如罪犯的「15 金币」）用它从装备列表里拆出来放进钱包。
    """
    match = _COIN_RE.match(str(text or "").strip())
    if not match:
        return None
    word = match.group(2)
    name = _COIN_WORDS.get(word) or _COIN_WORDS.get(word.lower())
    count = int(match.group(1))
    if not name or count <= 0:
        return None
    return {name: count}


def _coin_cp(name: str) -> int:
    return COIN_CP.get(str(name).strip(), 0)


def to_cp(coins: dict[str, int] | None) -> int:
    """把钱包折成铜币总额。认不得的币种按 0 计。"""
    total = 0
    for name, count in (coins or {}).items():
        total += _coin_cp(name) * int(count or 0)
    return total


def from_cp(total: int) -> dict[str, int]:
    """把铜币总额拆成枚数最省的 wallet（铂 / 金 / 银 / 铜）。"""
    left = max(0, int(total or 0))
    out: dict[str, int] = {}
    for name in _ORDER:
        value = COIN_CP[name]
        count, left = divmod(left, value)
        if count:
            out[name] = count
    return out


def add_coins(
    coins: dict[str, int] | None, delta: dict[str, int] | None
) -> dict[str, int]:
    """钱包相加（``delta`` 可为负值表示扣钱；扣成负数则归零）。"""
    return from_cp(to_cp(coins) + to_cp(delta))


def merge_coins(
    coins: dict[str, int] | None, delta: dict[str, int] | None
) -> dict[str, int]:
    """按**原币种**逐项相加，不做大面额归并。

    背景钱袋「15 金币」并进钱包后仍是「金 15」，不会被写成「铂 1 · 金 5」。
    需要真正结算（买东西找零）时再用 :func:`add_coins` / :func:`spend_coins`。
    """
    merged = {str(k): int(v) for k, v in (coins or {}).items()}
    for name, count in (delta or {}).items():
        merged[str(name)] = merged.get(str(name), 0) + int(count or 0)
    return {name: count for name, count in merged.items() if count > 0}


def format_coins(coins: dict[str, int] | None) -> str:
    """钱包的可读文本，如 ``金 35 · 银 20``；空钱包返回「无」。

    **按钱包里的原币种展示**，不强制兑换成大面额——玩家记得「我有 35 金」，
    把它写成「铂 3 · 金 7」反而认不出。真正的归并只发生在 :func:`add_coins` /
    :func:`spend_coins` 结算之后。
    """
    wallet = {
        str(name): int(count)
        for name, count in (coins or {}).items()
        if int(count or 0) > 0
    }
    if not wallet:
        return "无"
    known = [name for name in _ORDER if name in wallet]
    extra = [name for name in wallet if name not in _ORDER]
    return " · ".join(f"{name} {wallet[name]}" for name in known + extra)


def can_afford(coins: dict[str, int] | None, cost: dict[str, int] | int | None) -> bool:
    """钱包是否付得起 ``cost``（``cost`` 可以是钱包字典或直接是铜币数）。"""
    need = cost if isinstance(cost, int) else to_cp(cost)
    return to_cp(coins) >= int(need or 0)


def spend_coins(
    coins: dict[str, int] | None, cost: dict[str, int] | int | None
) -> tuple[dict[str, int], bool]:
    """付钱：够就返回 ``(新钱包, True)``，不够返回 ``(原钱包, False)``。"""
    need = cost if isinstance(cost, int) else to_cp(cost)
    before = to_cp(coins)
    after = before - int(need or 0)
    if after < 0:
        return from_cp(before), False
    return from_cp(after), True


def _parse_gold_expr(expr: str) -> tuple[int, int, int]:
    """解析 ``5d4×10`` / ``5d4`` / ``2d4*10`` → ``(骰数, 面数, 倍数)``。"""
    text = str(expr or "").strip().lower()
    for sep in ("×", "x", " ", "*"):
        text = text.replace(sep, "*")
    multiplier = 1
    if "*" in text:
        text, mult = text.split("*", 1)
        try:
            multiplier = max(1, int(mult))
        except ValueError:
            multiplier = 1
    if "d" not in text:
        return 0, 0, multiplier
    dice, faces = text.split("d", 1)
    try:
        return max(0, int(dice or 1)), max(0, int(faces)), multiplier
    except ValueError:
        return 0, 0, multiplier


#: 价格文本里的「数量 + 币种」，如 ``2 gp`` / ``1,000 gp`` / ``4 cp`` / ``至少10 gp``
_COST_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(铜币|银币|银金币|金币|铂金币|铂金|铜|银|银金|金|铂|cp|sp|ep|gp|pp)",
    re.IGNORECASE,
)

#: 价格文本里认得的币种 -> 折合铜币
_COST_UNITS: dict[str, int] = {
    "铜币": 1,
    "铜": 1,
    "cp": 1,
    "银币": 10,
    "银": 10,
    "sp": 10,
    "银金币": 50,
    "银金": 50,
    "ep": 50,
    "金币": 100,
    "金": 100,
    "gp": 100,
    "铂金币": 1000,
    "铂金": 1000,
    "铂": 1000,
    "pp": 1000,
}


def parse_cost(text: str) -> int:
    """把价格文本折成铜币，如 ``2 gp`` → 200、``1,000 gp`` → 100000。

    认不出（``—``、``×4`` 这类倍数 / 无价写法）返回 0。
    """
    match = _COST_RE.search(str(text or ""))
    if not match:
        return 0
    amount = float(match.group(1).replace(",", ""))
    unit = _COST_UNITS.get(match.group(2).lower(), 0)
    return round(amount * unit)


def roll_starting_gold(expr: str, rng: Any = None) -> dict[str, int]:
    """掷一笔金币（如 ``5d4×10``），返回钱包（金 / 银 / 铜）。

    供赏金 / 战利品等场景使用；**建卡不走这里**（初始装备包不可换成金币）。
    解析不出骰式时返回空钱包。
    """
    dice, faces, multiplier = _parse_gold_expr(expr)
    if dice <= 0 or faces <= 0:
        return {}
    amount = roll(f"{dice}d{faces}", rng=rng).total * multiplier
    return from_cp(amount * COIN_CP["金"])
